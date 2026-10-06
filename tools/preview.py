"""界面预览 / 视觉自检工具。

在隔离的数据目录里塞一批示例任务，启动程序，切到指定模式，截图存到 screenshots/，
然后自动退出。这样可以在不碰你真数据的前提下反复检查界面。

用法：
    python tools/preview.py                 # 置顶模式截图（能看清界面）
    python tools/preview.py embed           # 嵌入桌面模式
    python tools/preview.py theme=dark      # 深色主题
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP_HOME = Path(tempfile.mkdtemp(prefix="todowidget-preview-"))
os.environ["TODOWIDGET_HOME"] = str(TMP_HOME)

from app import win32tools as w32  # noqa: E402

w32.set_dpi_awareness()

import tkinter as tk  # noqa: E402
from app.models import Task, dt_to_iso  # noqa: E402

SECTIONS = ["look", "desktop", "remind", "ai", "system", "data"]


def seed(store) -> None:
    now = datetime.now().replace(second=0, microsecond=0)
    study = store.ensure_list("学习")
    samples = [
        Task.new("交暑假作业", priority=3, list_id=study.id),
        Task.new("复习数学第三章 二次根式", priority=3, list_id="work"),
        Task.new("给妈妈打电话", priority=2, list_id="life"),
        Task.new("买牛奶和面包", priority=1, list_id="life"),
        Task.new("整理下周班会发言稿", priority=2, list_id="work"),
        Task.new("背单词 30 个", priority=1, list_id=study.id,
                 repeat={"freq": "daily", "interval": 1}),
        Task.new("预约体检", priority=0),
    ]
    samples[0].due = dt_to_iso(now.replace(hour=18, minute=0))
    samples[1].due = dt_to_iso(now - timedelta(days=1))          # 逾期
    samples[2].due = dt_to_iso(now + timedelta(hours=3))
    samples[3].due = dt_to_iso((now + timedelta(days=1)).replace(hour=9, minute=0))
    samples[4].due = dt_to_iso((now + timedelta(days=3)).replace(hour=14, minute=30))
    samples[4].note = "记得带上上周的会议记录"
    samples[5].due = dt_to_iso(now.replace(hour=21, minute=0))
    samples[6].priority = 0
    for s in samples:
        store.add(s, save=False)
    done = Task.new("交英语作业", priority=2)
    store.add(done, save=False)
    store.complete(done.id, save=False)
    store.save()


def grab(hwnd: int, name: str, margin: int = 50):
    try:
        from PIL import ImageGrab
        import win32gui
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        box = (max(0, left - margin), max(0, top - margin), right + margin, bottom + margin)
        out = ROOT / "screenshots"
        out.mkdir(exist_ok=True)
        path = out / f"{name}.png"
        ImageGrab.grab(bbox=box).save(path)
        return path
    except Exception as exc:
        return f"截图失败: {exc}"


def main() -> None:
    mode = "top"
    theme = "light"
    for arg in sys.argv[1:]:
        if arg.startswith("theme="):
            theme = arg.split("=", 1)[1]
        else:
            mode = arg

    from app.config import Config
    from app.store import Store

    cfg = Config()
    cfg["theme"] = theme
    cfg["desktop_mode"] = mode
    cfg["pos_x"] = None
    cfg["pos_y"] = None
    cfg.save()
    store = Store()
    seed(store)

    from app.application import Application
    app = Application()
    app.start()
    print(f"数据目录: {TMP_HOME}")
    print(f"DPI={app.dpi} scale={app.theme.scale} 模式={app.pinner.mode} 状态={app.pinner.status}")

    root = app.root
    results = []

    def shot(tag: str):
        root.update_idletasks()
        root.update()
        results.append(grab(app.hwnd, f"ui-{tag}-{theme}"))

    root.after(600, lambda: shot("main"))

    def open_editor():
        tasks = app.store.view("today")
        ed = None
        if tasks:
            app.open_editor(tasks[0])
            ed = app._editors[0] if app._editors else None

        def do_shot():
            root.update_idletasks()
            root.update()
            if ed is not None:
                import win32gui
                hwnd = w32.toplevel_hwnd(ed.win)
                results.append(grab(hwnd, f"ui-editor-{theme}", margin=30))
                try:
                    ed._toggle_calendar()
                except Exception:
                    pass
                root.update_idletasks()
                root.update()
                results.append(grab(hwnd, f"ui-editor-cal-{theme}", margin=30))
                try:
                    ed.close()
                except Exception:
                    pass
            root.after(400, open_settings)

        root.after(800, do_shot)

    def open_settings():
        app.open_settings()
        panel = app._settings

        def shot_sections(i=0):
            if panel is None or i >= len(SECTIONS):
                root.after(300, open_voice)
                return
            key = SECTIONS[i]
            try:
                panel._switch(key)
            except Exception:
                pass
            root.update_idletasks()
            root.update()
            import win32gui
            results.append(grab(w32.toplevel_hwnd(panel), f"ui-settings-{key}-{theme}", margin=24))
            root.after(350, lambda: shot_sections(i + 1))

        root.after(700, shot_sections)

    def open_voice():
        try:
            app.open_voice()
        except Exception as exc:
            print("voice open failed:", exc)
        panel = app._voice

        def shot():
            root.update_idletasks()
            root.update()
            if panel is not None:
                import win32gui
                results.append(grab(w32.toplevel_hwnd(panel), f"ui-voice-{theme}", margin=24))
                # 手动塞一批候选任务，看预览长什么样
                try:
                    panel._on_extracted([
                        {"title": "交暑假作业", "priority": 3, "due": "2026-10-07T18:00",
                         "category": "学习", "note": ""},
                        {"title": "给妈妈打电话", "priority": 2, "due": None,
                         "category": "生活", "note": "问问体检的事"},
                        {"title": "复习二次根式第三章", "priority": 1, "due": "2026-10-09T20:00",
                         "category": "学习", "note": ""},
                    ])
                except Exception as exc:
                    print("preview shot failed:", exc)
                root.update_idletasks()
                root.update()
                results.append(grab(w32.toplevel_hwnd(panel), f"ui-voice-preview-{theme}", margin=24))
            root.after(300, open_reminder)

        root.after(900, shot)

    def open_reminder():
        from app.models import Task, dt_to_iso
        import datetime
        t = Task.new("交暑假作业", priority=3, list_id="study")
        t.due = dt_to_iso(datetime.datetime.now().replace(hour=18, minute=0))
        t.note = "记得带上练习册"
        app.store.add(t)
        from app.ui.reminder import ReminderPopup
        pop = ReminderPopup(app, t)

        def shot():
            root.update_idletasks()
            root.update()
            import win32gui
            results.append(grab(w32.toplevel_hwnd(pop), f"ui-reminder-{theme}", margin=24))
            root.after(200, finish)

        root.after(700, shot)

    def finish():
        for r in results:
            print("saved:", r)
        try:
            app.pinner.restore_idle()
            app.store.save()
        except Exception:
            pass
        root.after(200, root.destroy)

    root.after(1500, open_editor)
    root.mainloop()


if __name__ == "__main__":
    main()
