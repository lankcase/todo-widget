"""Windows 集成：DPI 感知、单实例、贴桌面层、全局热键、托盘图标。

设计要点
--------
* 所有 Win32 消息（热键、托盘点击）都在一个**独立线程的隐藏窗口**里处理，
  事件通过线程安全队列交给 Tk 主循环轮询 —— Tkinter 不是线程安全的，
  绝不能在别的线程里直接调用控件方法。
* 贴桌面用三种模式，互相独立，失败可以降级：
  - bottom : 普通顶层窗口常驻 z 序最底，点击时系统自动置顶（可直接输入），
             失焦后自动回到最底。最稳，作为默认。
  - embed  : SetParent 到 Progman，真正成为桌面的一部分（Win+D 不影响），
             编辑时临时脱离到最前。可能与 Wallpaper Engine 冲突。
  - top    : 常驻置顶。
"""

from __future__ import annotations

import ctypes
import queue
import threading
import time
from ctypes import wintypes
from typing import Any, Callable, Optional

import win32api
import win32con
import win32event
import win32gui
import winerror

# ---------------- 常量 ----------------
HWND_BOTTOM = 1
HWND_TOP = 0
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOOWNERZORDER = 0x0200
SWP_NOSENDCHANGING = 0x0400

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000

WM_HOTKEY = 0x0312
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_COMMAND_TASK = WM_APP + 2     # 让服务线程去执行 RegisterHotKey / 托盘操作

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

MUTEX_NAME = "TodoWidget_SingleInstance_v1"

# RegisterHotKey 常见失败原因（Windows 的 1409 就是"热键已被别的程序注册"）
HOTKEY_ERRORS = {
    0: "系统没有报告具体原因",
    1409: "这个组合已经被别的程序占用了",
    1408: "窗口句柄无效（内部错误，重启程序即可）",
    87: "组合键参数不合法",
}

# ---------------------------------------------------------------------------
# ctypes 句柄类型声明
#
# 一定要声明 argtypes：64 位下句柄是指针宽度，不声明的话 ctypes 会按 32 位 int 传参，
# 句柄被截断 -> 调用失败。这个坑真实踩过：screen_dpi() 里 GetDeviceCaps 拿到被截断的
# HDC 后返回 0，程序就把 144 DPI 的屏幕当成 96 DPI，整个界面按 1.0 缩放画出来
# （窗口只有应有尺寸的 2/3，字体也偏小）。句柄值偏小时侥幸能过，所以特别隐蔽。
# ---------------------------------------------------------------------------
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.GetDC.argtypes = [wintypes.HWND]
_user32.GetDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.ReleaseDC.restype = ctypes.c_int
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.SetFocus.argtypes = [wintypes.HWND]
_user32.BringWindowToTop.argtypes = [wintypes.HWND]
_user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF,
                                               wintypes.BYTE, wintypes.DWORD]
_user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
_user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
_user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
_user32.GetParent.argtypes = [wintypes.HWND]
_user32.GetParent.restype = wintypes.HWND
_user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
_user32.SetParent.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL

_gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
_gdi32.GetDeviceCaps.restype = ctypes.c_int
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteDC.restype = wintypes.BOOL
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.StretchBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.DWORD]
_gdi32.StretchBlt.restype = wintypes.BOOL
_gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
_gdi32.GetDIBits.restype = ctypes.c_int

_kernel32.GetCurrentThreadId.restype = wintypes.DWORD


# ---------------- DPI ----------------
def set_dpi_awareness() -> str:
    """开启 Per-Monitor V2，避免高分屏上界面发虚。"""
    try:
        if _user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor"
    except Exception:
        pass
    try:
        _user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "none"


def screen_dpi() -> float:
    try:
        dc = _user32.GetDC(None)
        if not dc:
            return 96.0
        dpi = _gdi32.GetDeviceCaps(wintypes.HDC(dc), 88)  # LOGPIXELSX
        _user32.ReleaseDC(None, wintypes.HDC(dc))
        return float(dpi) if dpi else 96.0
    except Exception:
        return 96.0


def dpi_report(hwnd: int = 0, extra: Optional[dict] = None) -> str:
    """把 DPI 相关的实际状态打成一段文本，排查"界面尺寸不对"时用。

    注意：一定要在**本进程内**调用。从别的进程（尤其是没有开启 DPI 感知的脚本）
    去 GetWindowRect，拿到的是 DPI 虚拟化后的坐标，会得到一个"尺寸小了 1.5 倍"的
    假象 —— 这个坑踩过一次，整整查了半天。
    """
    _user32.GetWindowDpiAwarenessContext.argtypes = [wintypes.HWND]
    _user32.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
    _user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    _user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
    _user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    _user32.GetDpiForWindow.restype = ctypes.c_uint
    _user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p

    kinds = {0: "unaware", 1: "system", 2: "per-monitor(v1或v2)"}
    lines = ["--- DPI 诊断（本进程内测量） ---"]
    try:
        ctx = _user32.GetThreadDpiAwarenessContext()
        lines.append(f"线程感知级别: {kinds.get(_user32.GetAwarenessFromDpiAwarenessContext(ctx), '?')}")
    except Exception as exc:
        lines.append(f"线程感知级别查询失败: {exc}")
    if hwnd:
        try:
            wctx = _user32.GetWindowDpiAwarenessContext(wintypes.HWND(hwnd))
            lines.append(f"窗口感知级别: {kinds.get(_user32.GetAwarenessFromDpiAwarenessContext(wctx), '?')}")
            lines.append(f"窗口所在显示器 DPI: {_user32.GetDpiForWindow(wintypes.HWND(hwnd))}")
            lines.append(f"窗口矩形: {win32gui.GetWindowRect(hwnd)}")
        except Exception as exc:
            lines.append(f"窗口信息查询失败: {exc}")
    lines.append(f"屏幕 DPI: {screen_dpi()}")
    lines.append(f"工作区: {work_area()}")
    for k, v in (extra or {}).items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines)


def work_area() -> tuple[int, int, int, int]:
    """主屏工作区（排除任务栏）。"""
    try:
        rect = win32gui.SystemParametersInfo(win32con.SPI_GETWORKAREA)
        return rect  # (left, top, right, bottom)
    except Exception:
        return (0, 0, win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1))


# ---------------- 单实例 ----------------
class SingleInstance:
    def __init__(self, name: str = MUTEX_NAME):
        self.handle = None
        self.already_running = False
        try:
            self.handle = win32event.CreateMutex(None, False, name)
            self.already_running = win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS
        except Exception:
            self.already_running = False

    def release(self) -> None:
        if self.handle:
            try:
                win32api.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = None


def toplevel_hwnd(widget: Any) -> int:
    """拿到 Tk 顶层窗口真正的 HWND（winfo_id 返回的是内层窗口）。"""
    wid = int(widget.winfo_id())
    try:
        parent = _user32.GetParent(wintypes.HWND(wid))
        return int(parent) if parent else wid
    except Exception:
        return wid


# ---------------- 贴桌面 ----------------
class DesktopPinner:
    """把窗口贴到桌面层。mode: bottom | embed | top"""

    def __init__(self, hwnd: int, mode: str = "bottom", click_raise: bool = True):
        self.hwnd = hwnd
        self.mode = mode if mode in ("bottom", "embed", "top") else "bottom"
        self.click_raise = click_raise
        self.embedded = False
        self._detached_for_edit = False
        self.status = "未初始化"
        self._set_tool_window(True)

    # ---- 基础 ----
    def _set_tool_window(self, enable: bool) -> None:
        """让窗口不出现在任务栏和 Alt+Tab 里。"""
        try:
            ex = win32gui.GetWindowLong(self.hwnd, GWL_EXSTYLE)
            if enable:
                ex = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
            else:
                ex = (ex & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
            win32gui.SetWindowLong(self.hwnd, GWL_EXSTYLE, ex)
        except Exception:
            pass

    def _set_no_activate(self, enable: bool) -> None:
        try:
            ex = win32gui.GetWindowLong(self.hwnd, GWL_EXSTYLE)
            ex = (ex | WS_EX_NOACTIVATE) if enable else (ex & ~WS_EX_NOACTIVATE)
            win32gui.SetWindowLong(self.hwnd, GWL_EXSTYLE, ex)
        except Exception:
            pass

    def _set_pos(self, insert_after: int, *, activate: bool = False) -> None:
        flags = SWP_NOSIZE | SWP_NOMOVE | SWP_NOOWNERZORDER | SWP_NOSENDCHANGING
        if not activate:
            flags |= SWP_NOACTIVATE
        try:
            win32gui.SetWindowPos(self.hwnd, insert_after, 0, 0, 0, 0, flags)
        except Exception:
            pass

    # ---- Progman 探测 ----
    @staticmethod
    def _children(hwnd: int) -> list[tuple[int, str]]:
        out = []
        try:
            child = win32gui.GetWindow(hwnd, win32con.GW_CHILD)
            while child:
                try:
                    out.append((child, win32gui.GetClassName(child)))
                except Exception:
                    pass
                child = win32gui.GetWindow(child, win32con.GW_HWNDNEXT)
        except Exception:
            pass
        return out

    @classmethod
    def probe_desktop(cls) -> dict[str, Any]:
        """探测桌面窗口结构，给设置面板显示用。"""
        info: dict[str, Any] = {"progman": 0, "defview": 0, "wallpaper_worker": 0,
                                "top_workerw": 0, "ok": False}
        try:
            progman = win32gui.FindWindow("Progman", None)
            info["progman"] = progman or 0
            if progman:
                for child, cls in cls._children(progman):
                    if cls == "SHELLDLL_DefView" and not info["defview"]:
                        info["defview"] = child
                    if cls == "WorkerW" and not info["wallpaper_worker"]:
                        info["wallpaper_worker"] = child
            # 经典 WorkerW 兄弟窗口
            top = 0
            def cb(h, _):
                nonlocal top
                try:
                    if win32gui.GetClassName(h) == "WorkerW" and win32gui.FindWindowEx(h, 0, "SHELLDLL_DefView", None):
                        top = h
                        return False
                except Exception:
                    pass
                return True
            try:
                win32gui.EnumWindows(cb, None)
            except Exception:
                pass
            info["top_workerw"] = top
            info["ok"] = bool(progman)
        except Exception:
            pass
        return info

    # ---- 模式应用 ----
    def apply(self, mode: Optional[str] = None, *, move_x: Optional[int] = None,
              move_y: Optional[int] = None) -> str:
        """应用贴桌面模式，返回实际生效的模式（可能降级）。"""
        if mode:
            self.mode = mode
        if self.embedded:
            self._unembed()
        if self.mode == "top":
            self._set_no_activate(False)
            self._set_pos(HWND_TOPMOST)
            self.status = "常驻置顶"
            return self.mode
        if self.mode == "embed":
            if self._embed():
                self.status = "已嵌入桌面层"
                return self.mode
            # 嵌入失败 -> 自动降级
            self.mode = "bottom"
            self.status = "嵌入失败，已回退为贴桌面·置底"
        self._set_no_activate(not self.click_raise)
        self._set_pos(HWND_BOTTOM)
        self.status = "贴桌面·置底"
        return self.mode

    def _embed(self) -> bool:
        """把窗口挂到 Progman 上，成为桌面的一部分。

        两个坑（已实测确认）：
        1) 重挂载后窗口坐标会被当成父窗口客户区坐标，必须用 ScreenToClient 换算，
           否则窗口会跑到屏幕外；
        2) WS_POPUP 窗口用 GetParent 查到的是 owner(0)，检测父窗口必须用
           GetAncestor(GA_PARENT)。
        """
        try:
            progman = win32gui.FindWindow("Progman", None)
            if not progman:
                return False
            left, top, right, bottom = win32gui.GetWindowRect(self.hwnd)
            prev = _user32.SetParent(wintypes.HWND(self.hwnd), wintypes.HWND(progman))
            if not prev:
                return False
            try:
                cx, cy = win32gui.ScreenToClient(progman, (left, top))
            except Exception:
                cx, cy = left, top
            # 放在 Progman 子窗口 z 序最上：压过壁纸层和桌面图标层，
            # 这样 Wallpaper Engine 的动态壁纸也不会盖住我们
            win32gui.SetWindowPos(self.hwnd, HWND_TOP, cx, cy, 0, 0,
                                  SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
            self.embedded = self.parent_hwnd() == progman
            self._set_no_activate(False)
            return self.embedded
        except Exception:
            self.embedded = False
            return False

    def parent_hwnd(self) -> int:
        """真实父窗口（对 WS_POPUP 窗口 GetParent 返回的是 owner，不可靠）。"""
        try:
            return win32gui.GetAncestor(self.hwnd, win32con.GA_PARENT) or 0
        except Exception:
            return 0

    def _unembed(self) -> None:
        if not self.embedded:
            return
        try:
            rect = win32gui.GetWindowRect(self.hwnd)
            win32gui.SetParent(self.hwnd, 0)
            win32gui.SetWindowPos(self.hwnd, HWND_TOP, rect[0], rect[1], 0, 0,
                                  SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)
        except Exception:
            pass
        self.embedded = False

    # ---- 编辑时临时浮起 ----
    def raise_for_edit(self) -> None:
        """点击后让窗口浮到最前，可以正常输入。"""
        if self.mode == "top":
            self._set_pos(HWND_TOPMOST, activate=True)
            force_foreground(self.hwnd)
            return
        if self.embedded:
            self._detached_for_edit = True
            rect = win32gui.GetWindowRect(self.hwnd)
            try:
                win32gui.SetParent(self.hwnd, 0)
                self.embedded = False
                win32gui.SetWindowPos(self.hwnd, HWND_TOPMOST, rect[0], rect[1], 0, 0,
                                      SWP_NOSIZE | SWP_SHOWWINDOW | SWP_NOOWNERZORDER)
            except Exception:
                pass
            force_foreground(self.hwnd)
            return
        self._set_no_activate(False)
        self._set_pos(HWND_TOPMOST, activate=True)
        force_foreground(self.hwnd)

    def restore_idle(self) -> None:
        """结束编辑，回到桌面层。"""
        if self.mode == "top":
            return
        if self._detached_for_edit:
            self._detached_for_edit = False
            self._embed()
            return
        if self.embedded:
            return
        self._set_pos(HWND_BOTTOM)
        self._set_no_activate(not self.click_raise)

    # ---- 保活 ----
    def health_check(self) -> bool:
        """检测窗口是否被"显示桌面"隐藏或掉出桌面层，需要时拉回来。返回是否做了修复。"""
        try:
            if not win32gui.IsWindow(self.hwnd):
                return False
            fixed = False
            if win32gui.IsIconic(self.hwnd):
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
                fixed = True
            if not win32gui.IsWindowVisible(self.hwnd):
                win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNOACTIVATE)
                fixed = True
            focused = self.is_focused()
            if self.mode == "top":
                if not focused:
                    self._set_pos(HWND_TOPMOST)
            elif self.embedded or self._detached_for_edit:
                # 嵌入模式不受 Win+D 影响，只需确认父窗口还在
                if self.embedded and not self._detached_for_edit:
                    progman = win32gui.FindWindow("Progman", None)
                    if progman and self.parent_hwnd() != progman:
                        self.embedded = False
                        self._embed()
                        fixed = True
            elif not focused:
                self._set_pos(HWND_BOTTOM)
            return fixed
        except Exception:
            return False

    def is_focused(self) -> bool:
        try:
            return win32gui.GetForegroundWindow() == self.hwnd
        except Exception:
            return False

    # ---- 嵌入模式自检 ----
    def desktop_layer_coverage(self, card_color: str, tolerance: int = 14) -> float:
        """窗口矩形区域内，有多大比例的像素和卡片底色接近（返回 0~1，失败返回 -1）。

        取像素用的是 Progman 的 DC —— 那是「桌面层」本身，不受上层窗口遮挡影响，
        所以不管用户此刻开着什么窗口，这个判断都成立。

        用来回答一个问题：嵌入模式到底画出来了没有。实测在部分 Win11 版本上，
        窗口成功挂进 Progman 了（父窗口、矩形、可见性全部正常），但屏幕上什么都不显示
        —— 多半是动态壁纸（Wallpaper Engine）独占渲染了桌面层。这时只能回退。
        """
        try:
            key = card_color.lstrip("#")
            er, eg, eb = (int(key[i:i + 2], 16) for i in (0, 2, 4))
            progman = win32gui.FindWindow("Progman", None)
            if not progman:
                return -1.0
            left, top, right, bottom = win32gui.GetWindowRect(self.hwnd)
            w, h = right - left, bottom - top
            if w <= 0 or h <= 0:
                return -1.0
            step = 1 if w * h <= 400000 else 3      # 太大就抽样，别每次拷一大块位图
            sw, sh = max(1, w // step), max(1, h // step)

            src_dc = wintypes.HDC(win32gui.GetDC(progman))
            mem_dc = _gdi32.CreateCompatibleDC(src_dc)
            bmp = _gdi32.CreateCompatibleBitmap(src_dc, sw, sh)
            _gdi32.SelectObject(mem_dc, bmp)
            ox, oy = win32gui.ClientToScreen(progman, (0, 0))
            _gdi32.StretchBlt(mem_dc, 0, 0, sw, sh, src_dc,
                              left - ox, top - oy, w, h, 0x00CC0020)

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                            ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                            ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                            ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                            ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                            ("biClrImportant", ctypes.c_uint32)]

            bi = BITMAPINFOHEADER()
            bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bi.biWidth, bi.biHeight = sw, -sh
            bi.biPlanes, bi.biBitCount = 1, 32
            buf = ctypes.create_string_buffer(sw * sh * 4)
            _gdi32.GetDIBits(mem_dc, bmp, 0, sh, buf, ctypes.byref(bi), 0)

            data = buf.raw
            total = sw * sh
            hit = 0
            for i in range(0, total * 4, 4):
                b, g, r = data[i], data[i + 1], data[i + 2]
                if (abs(r - er) <= tolerance and abs(g - eg) <= tolerance
                        and abs(b - eb) <= tolerance):
                    hit += 1
            _gdi32.DeleteObject(bmp)
            _gdi32.DeleteDC(mem_dc)
            win32gui.ReleaseDC(progman, src_dc)
            return hit / total if total else -1.0
        except Exception:
            return -1.0

    def move_to(self, x: int, y: int) -> None:
        """把窗口移动到屏幕坐标 (x, y)。嵌入模式下要换算成父窗口客户区坐标。"""
        try:
            tx, ty = x, y
            if self.embedded or self._detached_for_edit:
                parent = self.parent_hwnd()
                if parent:
                    tx, ty = win32gui.ScreenToClient(parent, (x, y))
            win32gui.SetWindowPos(self.hwnd, 0, tx, ty, 0, 0,
                                  SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)
        except Exception:
            pass


# ---------------- 服务线程：热键 + 托盘 ----------------
class Win32Service:
    """独立线程里的隐藏窗口，处理全局热键和托盘图标。

    事件以 ("hotkey", name) / ("tray", "left"|"right") 形式投进 self.events，
    由 Tk 主循环轮询取出（避免跨线程操作 Tk）。
    """

    def __init__(self, tooltip: str = "待办清单"):
        self.events: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self.tooltip = tooltip
        self.hwnd = 0
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._hotkeys: dict[int, tuple[str, str]] = {}   # id -> (name, spec)
        self._next_id = 1
        self._tray_added = False
        self._icon_handle = 0
        self._pending: list[tuple[str, Any]] = []
        self._cmds: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self._lock = threading.Lock()
        self.last_error = ""

    # ---- 生命周期 ----
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="TodoWidget-Win32", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)

    def _run(self) -> None:
        try:
            self._create_window()
        except Exception as exc:           # pragma: no cover - 环境相关
            self.last_error = f"创建消息窗口失败: {exc}"
            self._ready.set()
            return
        # 窗口创建后，把排队的操作补做
        with self._lock:
            for action, arg in self._pending:
                try:
                    if action == "hotkey":
                        self._do_register(*arg)
                    elif action == "unhotkey":
                        self._do_unregister(*arg)
                    elif action == "tray":
                        self._do_tray(*arg)
                except Exception as exc:
                    self.last_error = str(exc)
            self._pending.clear()
        self._ready.set()
        win32gui.PumpMessages()

    def _create_window(self) -> None:
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self._wndproc
        wc.lpszClassName = "TodoWidgetMsgWnd"
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.style = 0
        try:
            atom = win32gui.RegisterClass(wc)
        except win32gui.error:
            atom = "TodoWidgetMsgWnd"      # 已注册过（同进程重启）
        self.hwnd = win32gui.CreateWindow(atom, "TodoWidget", 0, 0, 0, 0, 0,
                                          0, 0, wc.hInstance, None)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_HOTKEY:
            name = self._hotkeys.get(int(wparam), ("", ""))[0]
            if name:
                self.events.put(("hotkey", name))
            return 0
        if msg == WM_COMMAND_TASK:
            self._drain_commands()
            return 0
        if msg == WM_TRAY:
            event = int(lparam)
            if event in (win32con.WM_LBUTTONUP, win32con.WM_LBUTTONDBLCLK):
                self.events.put(("tray", "left"))
            elif event in (win32con.WM_RBUTTONUP, win32con.WM_CONTEXTMENU):
                self.events.put(("tray", "right"))
            return 0
        if msg == win32con.WM_DESTROY:
            self._do_tray(False)
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _drain_commands(self) -> None:
        while True:
            try:
                action, arg = self._cmds.get_nowait()
            except queue.Empty:
                return
            try:
                if action == "hotkey":
                    self._do_register(*arg)
                elif action == "unhotkey":
                    self._do_unregister(*arg)
                elif action == "tray":
                    self._do_tray(*arg)
            except Exception as exc:
                self.last_error = str(exc)

    def _do_unregister(self, hid: int) -> None:
        try:
            _user32.UnregisterHotKey(wintypes.HWND(self.hwnd), ctypes.c_int(hid))
        except Exception:
            pass

    def _post(self, action: str, arg: Any) -> None:
        """把操作丢给服务线程执行。

        RegisterHotKey / Shell_NotifyIcon 都要求"在窗口所属线程调用"，
        在主线程直接调会报 1408（窗口属于另一线程），所以这里只投递命令 + 发消息唤醒。
        """
        if not self.hwnd:
            with self._lock:
                self._pending.append((action, arg))
            return
        self._cmds.put((action, arg))
        try:
            win32gui.PostMessage(self.hwnd, WM_COMMAND_TASK, 0, 0)
        except Exception as exc:
            self.last_error = str(exc)

    def stop(self) -> None:
        if self.hwnd:
            try:
                win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass

    # ---- 全局热键 ----
    @staticmethod
    def parse_hotkey(spec: str) -> Optional[tuple[int, int]]:
        """'ctrl+alt+t' -> (MOD_CONTROL|MOD_ALT, ord('T'))"""
        if not spec:
            return None
        mods = 0
        vk = 0
        for part in spec.lower().replace(" ", "").split("+"):
            if part in ("ctrl", "control"):
                mods |= MOD_CONTROL
            elif part == "alt":
                mods |= MOD_ALT
            elif part == "shift":
                mods |= MOD_SHIFT
            elif part in ("win", "super", "meta"):
                mods |= MOD_WIN
            elif part:
                if len(part) == 1:
                    vk = ord(part.upper())
                elif part.startswith("f") and part[1:].isdigit():
                    vk = 0x70 + int(part[1:]) - 1   # VK_F1..
                elif part == "space":
                    vk = 0x20
                else:
                    return None
        if not vk or not mods:
            return None
        return mods, vk

    def register_hotkey(self, name: str, spec: str,
                        fallbacks: Optional[list[str]] = None) -> bool:
        """注册全局热键。被别的程序占用时会依次尝试备用组合。"""
        candidates = [spec] + [f for f in (fallbacks or []) if f and f != spec]
        parsed_list = [(c, self.parse_hotkey(c)) for c in candidates]
        parsed_list = [(c, p) for c, p in parsed_list if p]
        if not parsed_list:
            self.last_error = f"热键格式无法识别: {spec}"
            self.events.put(("error", self.last_error))
            return False
        # 先清掉同名的旧热键
        self.unregister_hotkey(name)
        self._next_id += 1
        hid = self._next_id
        self._hotkeys[hid] = (name, spec)
        self._post("hotkey", (hid, name, parsed_list))
        return True

    def _do_register(self, hid: int, name: str, parsed_list: list) -> None:
        """在服务线程里注册热键。

        这里刻意不用 win32gui.RegisterHotKey：pywin32 这个包装有时抛异常、有时静默
        返回 0，还会把 GetLastError 吃掉，导致既拿不到结果也拿不到原因。直接用 ctypes
        调 user32，行为确定、错误码可靠。
        """
        last_err = 0
        for idx, (spec, (mods, vk)) in enumerate(parsed_list):
            try:
                ok = _user32.RegisterHotKey(wintypes.HWND(self.hwnd), ctypes.c_int(hid),
                                            ctypes.c_uint(mods), ctypes.c_uint(vk))
                last_err = ctypes.get_last_error()
            except Exception as exc:
                self.last_error = f"注册热键 {spec} 时出错：{exc}"
                continue
            if ok:
                self._hotkeys[hid] = (name, spec)
                if idx > 0:
                    self.events.put(("hotkey_changed", f"{name}:{spec}"))
                self.events.put(("hotkey_ok", f"{name}:{spec}"))
                return
        specs = "、".join(c for c, _p in parsed_list)
        self.last_error = f"热键 {specs} 注册失败：{HOTKEY_ERRORS.get(last_err, f'错误码 {last_err}')}"
        self.events.put(("error", self.last_error))

    def unregister_hotkey(self, name: str) -> None:
        for hid, (n, _spec) in list(self._hotkeys.items()):
            if n == name:
                self._hotkeys.pop(hid, None)
                self._post("unhotkey", (hid,))

    # ---- 托盘 ----
    def set_tray_icon(self, icon_path: str, tooltip: str) -> None:
        self.tooltip = tooltip
        self._post("tray", (icon_path, tooltip))

    def _do_tray(self, icon_path: Optional[str], tooltip: str = "") -> None:
        if not self.hwnd:
            return
        if icon_path is None:
            if self._tray_added:
                win32gui.Shell_NotifyIcon(NIM_DELETE, (self.hwnd, 1))
                self._tray_added = False
            return
        hicon = 0
        try:
            hicon = win32gui.LoadImage(0, icon_path, win32con.IMAGE_ICON, 0, 0,
                                       win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
        except Exception as exc:
            self.last_error = f"加载托盘图标失败: {exc}"
        nid = (self.hwnd, 1, NIF_MESSAGE | NIF_ICON | NIF_TIP, WM_TRAY, hicon,
               (tooltip or self.tooltip)[:127])
        try:
            if self._tray_added:
                win32gui.Shell_NotifyIcon(NIM_MODIFY, nid)
            else:
                win32gui.Shell_NotifyIcon(NIM_ADD, nid)
                self._tray_added = True
            self._icon_handle = hicon
        except Exception as exc:
            self.last_error = f"托盘图标添加失败: {exc}"

    def remove_tray_icon(self) -> None:
        self._post("tray", (None, ""))

    def balloon(self, title: str, text: str) -> None:
        """托盘气泡提示（备用通知渠道）。"""
        try:
            if self.hwnd and self._tray_added:
                win32gui.Shell_NotifyIcon(NIM_MODIFY, (
                    self.hwnd, 1, NIF_MESSAGE | NIF_ICON | NIF_TIP,
                    WM_TRAY, self._icon_handle, (title + " — " + text)[:127]))
        except Exception:
            pass


def apply_layered_window(hwnd: int, key_color: str, alpha: float = 1.0) -> bool:
    """同时启用"透明键色"和"整窗透明度"。

    Tk 的 -alpha 和 -transparentcolor 会互相覆盖（都是调 SetLayeredWindowAttributes，
    后一次调用把前一次的标志位顶掉了），结果是圆角失效、整个窗口变成半透明方块。
    这里直接用 Win32 一次把 LWA_ALPHA|LWA_COLORKEY 一起设上，两个效果就能共存。
    """
    try:
        key = key_color.lstrip("#")
        r, g, b = (int(key[i:i + 2], 16) for i in (0, 2, 4))
        colorref = r | (g << 8) | (b << 16)          # COLORREF 是 0x00BBGGRR
        a = max(0, min(255, int(round(alpha * 255))))
        LWA_ALPHA, LWA_COLORKEY = 0x00000002, 0x00000001
        ex = win32gui.GetWindowLong(hwnd, GWL_EXSTYLE)
        win32gui.SetWindowLong(hwnd, GWL_EXSTYLE, ex | 0x00080000)   # WS_EX_LAYERED
        ok = _user32.SetLayeredWindowAttributes(wintypes.HWND(hwnd), wintypes.COLORREF(colorref),
                                               wintypes.BYTE(a), LWA_ALPHA | LWA_COLORKEY)
        return bool(ok)
    except Exception:
        return False


def force_foreground(hwnd: int) -> bool:
    """把窗口强行提到前台并给它键盘焦点。

    嵌入桌面模式下我们的窗口是 explorer 进程的子窗口，点击它激活的是 Progman
    而不是我们自己的进程，所以普通 SetForegroundWindow 会被系统拒绝。
    这里用 AttachThreadInput 把自己的线程挂到当前前台线程上借一下前台权限。
    """
    try:
        h = wintypes.HWND(hwnd)
        if _user32.GetForegroundWindow() == hwnd:
            _user32.SetFocus(h)
            return True
        if _user32.SetForegroundWindow(h):
            _user32.SetFocus(h)
            return True
        # 借用前台线程的输入队列（借完还回去）
        fg = _user32.GetForegroundWindow()
        fg_thread = _user32.GetWindowThreadProcessId(fg, None)
        our_thread = _kernel32.GetCurrentThreadId()
        attached = False
        if fg_thread and fg_thread != our_thread:
            attached = bool(_user32.AttachThreadInput(our_thread, fg_thread, True))
        try:
            _user32.BringWindowToTop(h)
            _user32.SetForegroundWindow(h)
            _user32.SetFocus(h)
        finally:
            if attached:
                _user32.AttachThreadInput(our_thread, fg_thread, False)
        return _user32.GetForegroundWindow() == hwnd
    except Exception:
        return False


def foreground_window_is_fullscreen() -> bool:
    """判断当前前台窗口是否全屏（全屏时不弹提醒，避免打断看视频）。"""
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return False
        rect = win32gui.GetWindowRect(hwnd)
        if rect[2] - rect[0] < win32api.GetSystemMetrics(0):
            return False
        if rect[3] - rect[1] < win32api.GetSystemMetrics(1):
            return False
        return win32gui.GetClassName(hwnd) not in ("Progman", "WorkerW", "Shell_TrayWnd")
    except Exception:
        return False


def flash_window(hwnd: int) -> None:
    """任务栏闪烁，提示用户有提醒。"""
    try:
        win32gui.FlashWindow(hwnd, True)
    except Exception:
        pass


def uptime_seconds() -> float:
    """系统运行时长，用于判断是否是开机后首次启动。"""
    try:
        return win32api.GetTickCount64() / 1000.0
    except Exception:
        return time.time()
