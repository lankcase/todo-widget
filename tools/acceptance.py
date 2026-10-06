"""验收测试：在真实桌面上验证贴桌面行为，并实测启动耗时与内存占用。

会做一件略有存在感的事：模拟一次 Win+D（最小化所有窗口）来确认贴桌面效果，
然后把窗口恢复原状。不做任何不可逆的操作。

用法：
    python tools/acceptance.py            # 默认测 embed 模式
    python tools/acceptance.py bottom
"""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP_HOME = Path(tempfile.mkdtemp(prefix="todowidget-accept-"))
os.environ["TODOWIDGET_HOME"] = str(TMP_HOME)

from app import win32tools as w32  # noqa: E402

w32.set_dpi_awareness()

import tkinter as tk  # noqa: E402
import win32con  # noqa: E402
import win32gui  # noqa: E402


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def memory_mb() -> tuple[float, float]:
    """返回 (当前工作集, 页面文件占用)，单位 MB。

    psapi.GetProcessMemoryInfo 在部分系统上拿不到，K32GetProcessMemoryInfo 更稳。
    """
    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    for dll, fn in (("kernel32", "K32GetProcessMemoryInfo"), ("psapi", "GetProcessMemoryInfo")):
        try:
            func = getattr(getattr(ctypes.windll, dll), fn)
            func.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
            if func(handle, ctypes.byref(counters), counters.cb):
                return counters.WorkingSetSize / 1048576, counters.PagefileUsage / 1048576
        except Exception:
            continue
    return (0.0, 0.0)


def enumerate_visible_tops(exclude: set[int]) -> list[int]:
    out: list[int] = []

    def cb(h, _):
        try:
            if h in exclude or not win32gui.IsWindowVisible(h) or win32gui.IsIconic(h):
                return True
            if win32gui.GetParent(h):
                return True
            cls = win32gui.GetClassName(h)
            if cls in ("Shell_TrayWnd", "Progman", "WorkerW", "Windows.UI.Core.CoreWindow",
                       "Shell_SecondaryTrayWnd", "TaskListThumbnailWnd"):
                return True
            length = win32gui.GetWindowTextLength(h)
            if length == 0 and cls in ("Tooltip", "SysShadow"):
                return True
            rect = win32gui.GetWindowRect(h)
            if rect[2] - rect[0] < 80 or rect[3] - rect[1] < 80:
                return True      # 小窗（托盘气泡之类）不动它
            out.append(h)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return out


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "embed").lower()
    print("=" * 60)
    print(f"验收模式：{mode}")
    print("=" * 60)

    from app.config import Config
    cfg = Config()
    cfg["desktop_mode"] = mode
    cfg["hotkeys_enabled"] = True
    cfg["opacity"] = 0.97
    cfg["pos_x"] = None
    cfg["pos_y"] = None
    cfg.save()

    from app.application import Application

    t0 = time.perf_counter()
    app = Application()
    t_construct = time.perf_counter() - t0
    app.start()
    t_start = time.perf_counter() - t0

    root = app.root
    for _ in range(6):
        root.update_idletasks()
        root.update()
    time.sleep(0.8)
    root.update()

    hwnd = app.hwnd
    rect = win32gui.GetWindowRect(hwnd)
    parent = app.pinner.parent_hwnd() if app.pinner else 0
    progman = win32gui.FindWindow("Progman", None)
    ws, pf = memory_mb()

    print(f"\n启动耗时：构造 {t_construct * 1000:.0f} ms，到窗口就绪 {t_start * 1000:.0f} ms")
    print(f"内存占用：工作集 {ws:.1f} MB，页面文件 {pf:.1f} MB")
    print(f"窗口矩形：{rect}  尺寸 {rect[2] - rect[0]}×{rect[3] - rect[1]}")
    print(f"贴桌面状态：{app.pinner.status}（模式 {app.pinner.mode}）")
    print(f"父窗口：{hex(parent)}  Progman：{hex(progman or 0)}  "
          f"{'已嵌入桌面层 ✓' if parent == progman else '未嵌入（顶层窗口）'}")

    # 在 Progman 子窗口里的 z 序位置
    if parent == progman:
        order = []
        c = win32gui.GetWindow(progman, win32con.GW_CHILD)
        while c:
            order.append((hex(c), win32gui.GetClassName(c)))
            c = win32gui.GetWindow(c, win32con.GW_HWNDNEXT)
        print("Progman 子窗口 z 序（上 → 下）：", order)
        idx = next((i for i, (h, _c) in enumerate(order) if int(h, 16) == hwnd), -1)
        print(f"我们的窗口在第 {idx + 1} 位"
              f"{'（在壁纸层之上 ✓）' if idx == 0 else '（注意：不在最上层）'}")

    # ---------------- 模拟 Win+D ----------------
    print("\n" + "-" * 60)
    print("模拟 Win+D：最小化所有窗口，看贴桌面的窗口是否还在")
    print("-" * 60)
    victims = enumerate_visible_tops({hwnd})
    print(f"将临时最小化 {len(victims)} 个窗口…")
    for h in victims:
        try:
            win32gui.ShowWindow(h, win32con.SW_MINIMIZE)
        except Exception:
            pass
    time.sleep(0.9)
    root.update()

    alive = win32gui.IsWindowVisible(hwnd) and not win32gui.IsIconic(hwnd)
    print(f"Win+D 之后：可见={bool(win32gui.IsWindowVisible(hwnd))} "
          f"最小化={bool(win32gui.IsIconic(hwnd))}  →  {'存活 ✓' if alive else '被隐藏 ✗'}")

    # 桌面暴露出来了，这时候截图才有意义（只截窗口附近一小块）
    shot = None
    try:
        from PIL import ImageGrab
        margin = 60
        box = (max(0, rect[0] - margin), max(0, rect[1] - margin),
               rect[2] + margin, rect[3] + margin)
        out = ROOT / "screenshots"
        out.mkdir(exist_ok=True)
        shot = out / f"acceptance-{mode}-after-wind.png"
        ImageGrab.grab(bbox=box).save(shot)
        print(f"桌面截图已存：{shot}")
    except Exception as exc:
        print("截图失败:", exc)

    # 恢复窗口
    for h in victims:
        try:
            win32gui.ShowWindow(h, win32con.SW_RESTORE)
        except Exception:
            pass
    print("已恢复所有窗口")

    # ---------------- 托盘与热键 ----------------
    print("\n" + "-" * 60)
    print("系统集成")
    print("-" * 60)
    time.sleep(0.6)
    root.update()
    errors = []
    oks = []
    try:
        while True:
            kind, payload = app.service.events.get_nowait()
            if kind == "error":
                errors.append(payload)
            elif kind == "hotkey_ok":
                oks.append(payload)
    except Exception:
        pass
    print(f"热键注册成功：{oks if oks else '（无回执）'}")
    print(f"错误：{errors if errors else '无'}")
    print(f"托盘图标：{'已添加' if app.service._tray_added else '未添加'}")
    if app.service.last_error:
        print(f"服务线程最后错误：{app.service.last_error}")

    if shot:
        print(f"\n截图：{shot}")
    print(f"数据目录：{TMP_HOME}")

    def cleanup():
        try:
            app.store.save()
            app.service.remove_tray_icon()
            app.service.stop()
            root.destroy()
        except Exception:
            pass

    root.after(300, cleanup)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
