"""应用主控：把窗口、数据、贴桌面、热键、托盘、提醒串起来。

线程模型：除了 Win32Service 那个只处理热键/托盘消息的线程之外，一切都在 Tk 主线程上，
定时任务用 root.after 调度（不用后台线程跑业务逻辑，彻底避开 Tkinter 的线程安全问题）。
"""

from __future__ import annotations

import datetime
import tkinter as tk
import tkinter.font as tkfont
from typing import Optional

from . import autostart, nlp, win32tools as w32
from .config import Config
from .models import PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MEDIUM, PRIORITY_NONE, Task, dt_to_iso
from .store import Store
from .theme import Theme
from .ui.common import CardWindow, FontSet
from .ui.editor import TaskEditor
from .ui.reminder import MissedSummary, ReminderPopup
from .ui.widget import MainWindow

WINDOW_TITLE = "TodoWidget"

MODES = ["bottom", "embed", "top"]
MODE_LABEL = {"bottom": "贴桌面·置底", "embed": "嵌入桌面层", "top": "常驻置顶"}


class Toast(CardWindow):
    """右下角一闪而过的小提示。"""

    _stack: list["Toast"] = []

    def __init__(self, app, text: str, ms: int = 2200):
        super().__init__(app, app.theme, app.fonts, width=120, topmost=True,
                         draggable=False, pad=12)
        pal = self.theme.pal
        tk.Label(self.body, text=text, bg=pal.card, fg=pal.text,
                 font=self.fonts.small).pack()
        self.finalize(position="none")
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        idx = len(Toast._stack)
        Toast._stack.append(self)
        self.geometry(f"+{int(sw - w - self.theme.px(24))}"
                      f"+{int(sh - h - self.theme.px(56) - idx * (h + self.theme.px(8)))}")
        self.after(ms, self._close)

    def _close(self) -> None:
        if self in Toast._stack:
            Toast._stack.remove(self)
        try:
            self.destroy()
        except Exception:
            pass


class Application:
    def __init__(self):
        self.config = Config()
        self._editors: list[TaskEditor] = []
        self._settings = None
        self._voice = None
        self.dpi = w32.screen_dpi()

        # ---- 主题与字体 ----
        self.theme = Theme(self.config.get("theme", "light"), scale=self.dpi / 96.0)
        self.theme.set_palette(self.config.get("theme", "light"))

        # ---- 窗口 ----
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(WINDOW_TITLE)
        self.hwnd = 0
        self._opacity = 0.97
        self.root.tk.call("tk", "scaling", self.dpi / 72.0)
        try:
            self.root.wm_attributes("-transparentcolor", self.theme.pal.key)
        except Exception:
            pass
        self.root.overrideredirect(True)
        self.root.configure(bg=self.theme.pal.key)
        self.set_opacity(self.config.get("opacity", 0.97))

        available = set(tkfont.families(self.root))
        self.theme.resolve_fonts(available)
        self.fonts = FontSet(self.root, self.theme)

        # ---- 数据 ----
        self.store = Store()
        self.store.backup_daily()
        self.store.subscribe(self._on_store_change)

        # ---- 界面 ----
        self.main: Optional[MainWindow] = MainWindow(self)
        self._apply_geometry()

        # ---- Win32 ----
        self.pinner: Optional[w32.DesktopPinner] = None
        self.service = w32.Win32Service()
        self.hwnd = 0

        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

    # ================= 启动 =================
    def start(self) -> None:
        self.root.update_idletasks()
        self.hwnd = w32.toplevel_hwnd(self.root)
        w32.apply_layered_window(self.hwnd, self.theme.pal.key, self._opacity)
        self.pinner = w32.DesktopPinner(self.hwnd, mode=self.config.get("desktop_mode", "bottom"),
                                        click_raise=self.config.get("click_raise", True))
        applied = self.pinner.apply()
        self.root.deiconify()
        self.main.redraw_all()
        # deiconify 之后 Tk 可能重置分层属性，再补一次
        self.root.after(300, lambda: w32.apply_layered_window(
            self.hwnd, self.theme.pal.key, self._opacity))

        # 托盘 + 热键
        try:
            self.service.start()
            icon = autostart.ensure_icon()
            self.service.set_tray_icon(str(icon), self._tray_tip())
            if self.config.get("hotkeys_enabled", True):
                self._register_hotkeys()
        except Exception:
            pass

        self.root.after(120, self._poll_win32)
        self.root.after(1500, self._health_tick)
        self.root.after(3000, self._check_reminders)
        self.root.after(1200, self._warn_about_mode)

    TOGGLE_FALLBACKS = ["ctrl+alt+t", "ctrl+shift+t", "ctrl+alt+8"]
    VOICE_FALLBACKS = ["ctrl+alt+v", "ctrl+shift+space", "ctrl+alt+9"]

    def _register_hotkeys(self) -> None:
        """注册全局热键，被占用时自动退到备用组合。"""
        self.service.register_hotkey(
            "toggle", self.config.get("hotkey_toggle", "ctrl+alt+t"), self.TOGGLE_FALLBACKS)
        self.service.register_hotkey(
            "voice", self.config.get("hotkey_voice", "ctrl+alt+v"), self.VOICE_FALLBACKS)

    def _warn_about_mode(self) -> None:
        """启动后自检：嵌入模式到底画出来了没有。

        嵌入模式在某些系统上会"假成功"——父窗口、坐标、可见性全部正常，
        但屏幕上什么都不显示（动态壁纸独占桌面层时会这样）。这里实测一次像素，
        画不出来就自动回退到「贴桌面·置底」，并把原因告诉用户，而不是留个坏掉的开关。
        """
        if not self.pinner:
            return
        if self.pinner.status.startswith("嵌入失败"):
            self.toast(self.pinner.status, 4000)
        if self.pinner.mode == "embed" and self.pinner.embedded:
            ratio = self.pinner.desktop_layer_coverage(self.theme.pal.card)
            if 0 <= ratio < 0.25:
                self.config["desktop_mode"] = "bottom"
                self.config.save()
                applied = self.pinner.apply("bottom")
                self.refresh_tray_icon()
                self.toast("嵌入桌面层在当前系统上显示不出来（多半是动态壁纸占用了桌面层），"
                           "已自动切回「贴桌面·置底」", 6000)

    # ================= 定时任务 =================
    def _poll_win32(self) -> None:
        try:
            while True:
                kind, payload = self.service.events.get_nowait()
                if kind == "hotkey":
                    if payload == "toggle":
                        self.toggle_window()
                    elif payload == "voice":
                        self.open_voice()
                elif kind == "tray":
                    if payload == "left":
                        self.toggle_window()
                    else:
                        self.show_tray_menu()
                elif kind == "hotkey_changed":
                    # 原热键被占用，换成了备用组合：告诉用户并记住
                    name, spec = payload.split(":", 1)
                    key = "hotkey_toggle" if name == "toggle" else "hotkey_voice"
                    if self.config.get(key) != spec:
                        self.config[key] = spec
                        self.config.save()
                    self.toast(f"热键被占用，已换成 {spec}", 4000)
                elif kind == "error":
                    self.toast(payload, 4000)
        except Exception:
            pass
        self.root.after(120, self._poll_win32)

    def _health_tick(self) -> None:
        try:
            if self.pinner:
                self.pinner.health_check()
        except Exception:
            pass
        self.root.after(2000, self._health_tick)

    def _check_reminders(self) -> None:
        try:
            self._check_reminders_inner()
        except Exception:
            pass
        self.root.after(15000, self._check_reminders)

    def _check_reminders_inner(self) -> None:
        if not self.config.get("reminder_sound", False):
            pass
        if self._in_dnd():
            return
        for task in self.store.due_for_reminder():
            task.notified_at = dt_to_iso(datetime.datetime.now())
            self.store.save()
            if self.config.get("reminder_toast", False):
                self.service.balloon("待办提醒", task.title)
            ReminderPopup(self, task)
        if self.config.get("reminder_sound", False):
            try:
                import winsound
                if self.store.due_for_reminder():
                    winsound.MessageBeep(0x40)
            except Exception:
                pass

    def _in_dnd(self) -> bool:
        if not self.config.get("dnd_enabled", False):
            return False
        try:
            now = datetime.datetime.now().time()
            start = datetime.datetime.strptime(self.config.get("dnd_start", "23:00"), "%H:%M").time()
            end = datetime.datetime.strptime(self.config.get("dnd_end", "07:00"), "%H:%M").time()
        except Exception:
            return False
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end   # 跨零点

    def show_missed_summary(self) -> None:
        if not self.config.get("missed_summary", True):
            return
        missed = self.store.missed_reminders()
        if missed:
            MissedSummary(self, missed)

    # ================= 数据动作 =================
    def quick_add(self, raw: str) -> Task:
        parsed = nlp.parse(raw)
        task = Task.new(parsed.title)
        if parsed.due:
            task.due = dt_to_iso(parsed.due)
        if parsed.priority is not None:
            task.priority = parsed.priority
        if parsed.list_name:
            task.list_id = self.store.ensure_list(parsed.list_name).id
        offset = int(self.config.get("remind_default_offset", 0) or 0)
        if task.due and offset:
            task.remind_at = dt_to_iso(parsed.due - datetime.timedelta(minutes=offset))
        self.store.add(task)
        return task

    def save_task(self, task: Task, is_new: bool = False) -> None:
        if is_new:
            self.store.add(task)
        else:
            self.store.update(task)

    def delete_task(self, task: Task) -> None:
        if self.config.get("confirm_delete", True):
            if not self._confirm(f"删除「{task.title}」？"):
                return
        self.store.delete(task.id)
        self.toast("已删除")

    def toggle_task(self, task: Task) -> None:
        if task.done:
            self.store.uncomplete(task.id)
        else:
            new_task = self.store.complete(task.id)
            if new_task and new_task.due_dt:
                self.toast(f"已完成，下次：{nlp.humanize_due(new_task.due_dt)}")

    def complete_from_reminder(self, task: Task) -> None:
        new_task = self.store.complete(task.id)
        if new_task and new_task.due_dt:
            self.toast(f"已完成，下次：{nlp.humanize_due(new_task.due_dt)}")
        else:
            self.toast("已完成")

    def _confirm(self, text: str) -> bool:
        """用自绘的小确认窗，避免和整体风格不一致的系统对话框。"""
        from tkinter import messagebox
        return messagebox.askyesno("确认", text, parent=None)

    def _on_store_change(self) -> None:
        try:
            self.main.refresh()
            self._update_tray_tip()
        except Exception:
            pass

    # ================= 右键菜单 =================
    def show_context_menu(self, task: Task, x: int, y: int) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="编辑…", command=lambda: self.open_editor(task))
        menu.add_command(label=("取消完成" if task.done else "标记完成"),
                         command=lambda: self.toggle_task(task))
        menu.add_separator()

        pri = tk.Menu(menu, tearoff=0)
        for label, value in (("高", PRIORITY_HIGH), ("中", PRIORITY_MEDIUM),
                             ("低", PRIORITY_LOW), ("无", PRIORITY_NONE)):
            pri.add_command(label=label,
                            command=lambda v=value: self.store.set_priority(task.id, v))
        menu.add_cascade(label=f"优先级（{task.priority_label}）", menu=pri)

        delay = tk.Menu(menu, tearoff=0)
        delay.add_command(label="+10 分钟", command=lambda: self.store.postpone(task.id, minutes=10))
        delay.add_command(label="+1 小时", command=lambda: self.store.postpone(task.id, minutes=60))
        delay.add_command(label="今晚 20:00", command=lambda: self._postpone_to(task, 20, 0))
        delay.add_command(label="明天同一时间", command=lambda: self.store.postpone(task.id, days=1))
        delay.add_command(label="明天 09:00", command=lambda: self._postpone_to(task, 9, 0, days=1))
        delay.add_command(label="下周同一时间", command=lambda: self.store.postpone(task.id, days=7))
        delay.add_command(label="清除期限", command=lambda: self._clear_due(task))
        menu.add_cascade(label="延期", menu=delay)

        if len(self.store.lists) > 1:
            mv = tk.Menu(menu, tearoff=0)
            for l in self.store.lists:
                mv.add_command(label=l.name,
                               command=lambda lid=l.id: self.store.move_to_list(task.id, lid))
            menu.add_cascade(label=f"移动到（{self.store.list_name(task.list_id)}）", menu=mv)

        menu.add_separator()
        menu.add_command(label="删除", command=lambda: self.delete_task(task))
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _postpone_to(self, task: Task, hour: int, minute: int, days: int = 0) -> None:
        now = datetime.datetime.now()
        target = (now + datetime.timedelta(days=days)).replace(
            hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += datetime.timedelta(days=1)
        self.store.postpone(task.id, new_due=target)

    def _clear_due(self, task: Task) -> None:
        task.due = None
        task.remind_at = None
        task.notified_at = None
        self.store.update(task)

    # ================= 窗口行为 =================
    def _apply_geometry(self) -> None:
        w = self.theme.px(self.config.get("width", 330))
        h = self.theme.px(self.config.get("height", 480))
        x = self.config.get("pos_x")
        y = self.config.get("pos_y")
        if x is None or y is None:
            left, top, right, bottom = w32.work_area()
            x = right - w - self.theme.px(24)
            y = top + self.theme.px(48)
        self._win_x, self._win_y = int(x), int(y)
        self.root.geometry(f"{w}x{h}+{int(x)}+{int(y)}")

    def move_window(self, x: int, y: int) -> None:
        self._win_x, self._win_y = int(x), int(y)
        if self.pinner:
            self.pinner.move_to(int(x), int(y))
        else:
            self.root.geometry(f"+{int(x)}+{int(y)}")

    def on_window_moved(self) -> None:
        if self.config.get("remember_pos", True):
            self.config["pos_x"] = self._win_x
            self.config["pos_y"] = self._win_y
            self.config.save()

    def set_opacity(self, value: float) -> None:
        """窗口透明度。必须用 Win32 一次设好 LWA_ALPHA|LWA_COLORKEY，

        否则圆角透明和整窗透明度会互相覆盖（见 win32tools.apply_layered_window）。
        """
        try:
            self._opacity = max(0.4, min(1.0, float(value)))
        except Exception:
            self._opacity = 0.97
        if self.hwnd:
            w32.apply_layered_window(self.hwnd, self.theme.pal.key, self._opacity)

    def show_window(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
            if self.pinner:
                self.pinner.raise_for_edit()
        except Exception:
            pass

    def hide_window(self) -> None:
        try:
            self.root.withdraw()
        except Exception:
            pass

    def toggle_window(self) -> None:
        try:
            if self.root.state() == "withdrawn" or not self.root.winfo_viewable():
                self.show_window()
            else:
                self.hide_window()
        except Exception:
            self.show_window()

    def minimize(self) -> None:
        self.hide_window()
        self.toast("已收进托盘，Ctrl+Alt+T 唤回")

    # ---- 编辑时的浮起/回落 ----
    def on_edit_finished(self) -> None:
        if self.pinner and not self.pinner.is_focused():
            self.pinner.restore_idle()

    def on_background_click(self) -> None:
        if self.pinner:
            self.pinner.restore_idle()

    def open_editor(self, task: Optional[Task] = None) -> None:
        if self.pinner:
            self.pinner.raise_for_edit()
        ed = TaskEditor(self, task)
        self._editors.append(ed)

    def on_editor_closed(self, ed: TaskEditor) -> None:
        if ed in self._editors:
            self._editors.remove(ed)
        if self.pinner and not self._editors:
            self.pinner.restore_idle()

    def cycle_desktop_mode(self) -> None:
        cur = self.pinner.mode if self.pinner else "bottom"
        nxt = MODES[(MODES.index(cur) + 1) % len(MODES)]
        self.config["desktop_mode"] = nxt
        self.config.save()
        if self.pinner:
            applied = self.pinner.apply(nxt)
            self.toast(f"贴在桌面方式：{MODE_LABEL.get(applied, applied)}")
        self.refresh_tray_icon()

    def refresh_tray_icon(self) -> None:
        try:
            self.service.set_tray_icon(str(autostart.ensure_icon()), self._tray_tip())
        except Exception:
            pass

    def _tray_tip(self) -> str:
        c = self.store.counts()
        mode = MODE_LABEL.get(self.pinner.mode if self.pinner else "bottom", "")
        return f"待办清单 · {c['active']} 项未完成 · {mode}"

    def _update_tray_tip(self) -> None:
        try:
            self.service.set_tray_icon(str(autostart.ensure_icon()), self._tray_tip())
        except Exception:
            pass

    def show_tray_menu(self) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        c = self.store.counts()
        menu.add_command(label=f"{c['active']} 项未完成 · 今日 {c['done_today']} 已完成",
                         state="disabled")
        menu.add_separator()
        menu.add_command(label="显示 / 隐藏窗口", command=self.toggle_window)
        menu.add_command(label="快速添加…", command=self._tray_quick_add)
        menu.add_command(label="语音记待办", command=self.open_voice)
        mode_menu = tk.Menu(menu, tearoff=0)
        for m in MODES:
            mode_menu.add_command(
                label=("● " if self.pinner and self.pinner.mode == m else "   ") + MODE_LABEL[m],
                command=lambda mm=m: self._set_mode(mm))
        menu.add_cascade(label="贴在桌面方式", menu=mode_menu)
        menu.add_separator()
        menu.add_command(label="设置…", command=self.open_settings)
        menu.add_command(label="退出", command=self.quit_app)
        try:
            menu.tk_popup(*self._tray_menu_pos())
        finally:
            menu.grab_release()

    def _tray_menu_pos(self) -> tuple[int, int]:
        import ctypes
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def _set_mode(self, mode: str) -> None:
        self.config["desktop_mode"] = mode
        self.config.save()
        if self.pinner:
            applied = self.pinner.apply(mode)
            self.toast(f"贴在桌面方式：{MODE_LABEL.get(applied, applied)}")

    def _tray_quick_add(self) -> None:
        self.show_window()
        self.main.entry.focus_set()

    # ================= 其它面板 =================
    def open_settings(self) -> None:
        from .ui.settings_ui import SettingsPanel
        if self._settings is not None:
            try:
                self._settings.lift()
                self._settings.focus_force()
                return
            except Exception:
                self._settings = None
        if self.pinner:
            self.pinner.raise_for_edit()
        self._settings = SettingsPanel(self)

    def on_settings_closed(self) -> None:
        self._settings = None
        if self.pinner:
            self.pinner.restore_idle()

    def open_voice(self) -> None:
        from .ui.voice import VoicePanel
        if self._voice is not None:
            try:
                self._voice.lift()
                self._voice.focus_force()
                return
            except Exception:
                self._voice = None
        if self.pinner:
            self.pinner.raise_for_edit()
        try:
            self._voice = VoicePanel(self)
        except Exception as exc:
            self.toast(f"语音面板打不开：{exc}", 4000)
            self._voice = None

    def on_voice_closed(self) -> None:
        self._voice = None
        if self.pinner:
            self.pinner.restore_idle()

    # ================= 杂项 =================
    def toast(self, text: str, ms: int = 2200) -> None:
        try:
            Toast(self, text, ms)
        except Exception:
            pass

    def apply_settings(self) -> None:
        """设置变更后重新应用（主题、透明度、窗口尺寸、热键等）。"""
        self.theme.set_palette(self.config.get("theme", "light"))
        try:
            self.root.configure(bg=self.theme.pal.key)
            self.root.wm_attributes("-transparentcolor", self.theme.pal.key)
        except Exception:
            pass
        self.set_opacity(self.config.get("opacity", 0.97))
        self._apply_geometry()
        if self.pinner:
            self.pinner.click_raise = self.config.get("click_raise", True)
            self.pinner.apply(self.config.get("desktop_mode", "bottom"))
        self.service.unregister_hotkey("toggle")
        self.service.unregister_hotkey("voice")
        if self.config.get("hotkeys_enabled", True):
            self._register_hotkeys()
        self.main.apply_theme()
        self.refresh_tray_icon()

    def refresh_fonts_if_needed(self) -> None:
        return

    def quit_app(self) -> None:
        try:
            self.on_window_moved()
            self.store.save()
            self.service.remove_tray_icon()
            self.service.stop()
            self.root.destroy()
        except Exception:
            pass
