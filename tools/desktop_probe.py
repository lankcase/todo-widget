"""贴桌面层诊断工具。

用途：验证当前机器上"贴桌面"到底能不能用、长什么样，以及"显示桌面"(Win+D) 之后
能不能自动恢复。它只创建一个小窗口，跑几秒后自动退出，顺手把截图存到 screenshots/。

用法：
    python tools/desktop_probe.py            # 默认测试 bottom 模式
    python tools/desktop_probe.py embed      # 测试嵌入桌面模式
    python tools/desktop_probe.py all        # 依次测试三种模式
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk

from app import config as cfg_mod
from app import win32tools as w32
from app.theme import Theme

OUT = Path(__file__).resolve().parent.parent / "screenshots"


def grab(hwnd: int, name: str, margin: int = 40) -> str | None:
    """只截取窗口附近的一小块区域，避免把整个桌面都拍下来。"""
    try:
        from PIL import ImageGrab
    except Exception as exc:
        return f"PIL 不可用: {exc}"
    try:
        left, top, right, bottom = win32gui_rect(hwnd)
        box = (max(0, left - margin), max(0, top - margin),
               right + margin, bottom + margin)
        img = ImageGrab.grab(bbox=box)
        OUT.mkdir(exist_ok=True)
        path = OUT / f"{name}.png"
        img.save(path)
        return str(path)
    except Exception as exc:
        return f"截图失败: {exc}"


def win32gui_rect(hwnd: int) -> tuple[int, int, int, int]:
    import win32gui
    return win32gui.GetWindowRect(hwnd)


def make_window(theme: Theme, text: str) -> tk.Tk:
    root = tk.Tk()
    root.overrideredirect(True)
    w, h = 300, 150
    root.geometry(f"{w}x{h}+200+200")
    key = theme.pal.key
    root.configure(bg=key)
    try:
        root.wm_attributes("-transparentcolor", key)
    except Exception as exc:
        print("  ! transparentcolor 不可用:", exc)
    canvas = tk.Canvas(root, width=w, height=h, bg=key, highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)
    img = theme.card(w, h, 14, fill=theme.pal.card, outline=theme.pal.border)
    canvas.create_image(0, 0, image=img, anchor="nw")
    canvas._keep = img
    canvas.create_text(20, 30, text=text, anchor="nw",
                       fill=theme.pal.text, font=theme.font(13, "bold"))
    canvas.create_text(20, 60, anchor="nw", fill=theme.pal.text_dim,
                       font=theme.font(10), text="贴桌面诊断窗口")
    canvas.create_text(20, 90, anchor="nw", fill=theme.pal.accent,
                       font=theme.font(10), text="如果你能看到这个卡片贴着桌面，")
    canvas.create_text(20, 110, anchor="nw", fill=theme.pal.accent,
                       font=theme.font(10), text="说明贴桌面层的绘制是正常的。")
    return root


def test_mode(mode: str, seconds: float = 6.0) -> None:
    print(f"\n=== 测试模式: {mode} ===")
    theme = Theme("light", 1.0)
    root = make_window(theme, f"模式：{mode}")
    root.update()
    hwnd = w32.toplevel_hwnd(root)
    print(f"  HWND = {hex(hwnd)}")

    pinner = w32.DesktopPinner(hwnd, mode=mode, click_raise=True)
    applied = pinner.apply()
    print(f"  实际生效模式 = {applied}，status = {pinner.status}")
    print(f"  桌面结构 = {pinner.probe_desktop()}")
    root.update()

    time.sleep(1.2)
    p1 = grab(hwnd, f"desktop-{mode}-1-idle")
    print(f"  [1] 空闲截图: {p1}")

    # 模拟"显示桌面"(Win+D)：窗口被最小化后，看保活逻辑能否拉回来
    print("  模拟 Win+D（最小化窗口）…")
    try:
        import win32con, win32gui
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
    except Exception as exc:
        print("  最小化失败:", exc)
    time.sleep(0.8)
    fixed = pinner.health_check()
    root.update()
    time.sleep(0.8)
    p2 = grab(hwnd, f"desktop-{mode}-2-after-restore")
    print(f"  [2] 显示桌面恢复后截图: {p2}  (health_check 修复={fixed})")

    # 记录最终 z 序状态
    try:
        import win32gui
        print(f"  可见={bool(win32gui.IsWindowVisible(hwnd))} "
              f"最小化={bool(win32gui.IsIconic(hwnd))} "
              f"父窗口={hex(win32gui.GetParent(hwnd) or 0)} "
              f"前台={win32gui.GetForegroundWindow() == hwnd}")
    except Exception as exc:
        print("  状态查询失败:", exc)

    root.destroy()


def main() -> None:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "bottom").lower()
    print("DPI 感知:", w32.set_dpi_awareness())
    print("屏幕 DPI:", w32.screen_dpi())
    print("工作区:", w32.work_area())
    pinner_cls = w32.DesktopPinner
    print("桌面结构探测:", pinner_cls.probe_desktop())
    print("配置目录:", cfg_mod.data_dir())

    mode_map = {"bottom": ["bottom"], "embed": ["embed"], "top": ["top"],
                "all": ["bottom", "embed", "top"]}
    for m in mode_map.get(mode, ["bottom"]):
        try:
            test_mode(m)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"  模式 {m} 测试异常: {exc}")
    print("\n完成。")


if __name__ == "__main__":
    main()
