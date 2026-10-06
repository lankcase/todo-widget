"""任务编辑面板：新建 / 修改一条任务。

是一个独立的无边框圆角窗口，编辑期间置顶（否则贴桌面的窗口在底层输入不了字）。
不常用的"备注/子任务"默认收起，让面板保持紧凑。
"""

from __future__ import annotations

import datetime
import re
import tkinter as tk
from typing import Callable, Optional

from ..models import (
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    PRIORITY_NONE,
    Subtask,
    Task,
)
from .common import CalendarGrid, ChipGroup, FontSet, IconButton

PAD = 16
WIDTH = 340

PRIORITY_OPTS = [
    ("3", "高", "#EF4444"),
    ("2", "中", "#F59E0B"),
    ("1", "低", "#3B82F6"),
    ("0", "无", None),
]
REPEAT_OPTS = [
    ("", "不重复", None),
    ("daily", "每天", None),
    ("weekdays", "工作日", None),
    ("weekly", "每周", None),
    ("monthly", "每月", None),
    ("interval", "每 N 天", None),
]
REMIND_OPTS = [
    ("0", "到点", None),
    ("5", "提前5分", None),
    ("15", "提前15分", None),
    ("30", "提前30分", None),
    ("60", "提前1小时", None),
]
DATE_OPTS = [
    ("none", "无期限", None),
    ("today", "今天", None),
    ("tomorrow", "明天", None),
    ("after", "后天", None),
    ("pick", "选日期…", None),
]


class TaskEditor:
    def __init__(self, app, task: Optional[Task] = None):
        self.app = app
        self.root: tk.Tk = app.root
        self.cfg = app.config
        self.store = app.store
        self.theme = app.theme
        self.fonts: FontSet = app.fonts
        self.task = task
        self.is_new = task is None
        self.result: Optional[Task] = None
        self._note_open = False
        self._cal_open = False
        self._subtasks: list[Subtask] = []

        t = task or Task.new("")
        self._due_date: Optional[datetime.date] = t.due_dt.date() if t.due_dt else None
        self._time_str = t.due_dt.strftime("%H:%M") if t.due_dt else ""
        self._priority = str(t.priority)
        self._list_id = t.list_id
        self._repeat = (t.repeat or {}).get("freq", "")
        self._interval = int((t.repeat or {}).get("interval") or 1)
        self._remind = str(self._remind_offset_of(t))
        self._subtasks = [Subtask(s.id, s.title, s.done) for s in t.subtasks]

        self.build()

    # ---------------- 构建 ----------------
    def build(self) -> None:
        th, pal = self.theme, self.theme.pal
        self.win = tk.Toplevel(self.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.configure(bg=pal.key)
        try:
            self.win.wm_attributes("-transparentcolor", pal.key)
        except Exception:
            pass
        self.win.attributes("-topmost", True)
        self.win.bind("<Escape>", lambda _e: self.close())

        self.bg = tk.Canvas(self.win, bg=pal.key, highlightthickness=0, bd=0)
        self.body = tk.Frame(self.win, bg=pal.card)
        self.body.bind("<Configure>", lambda _e: self._sync_size())

        self._build_header()
        self._build_title()
        self._build_note_toggle()
        self._build_priority()
        self._build_due()
        self._build_calendar()
        self._build_remind()
        self._build_repeat()
        self._build_list()
        self._build_subtasks()
        self._build_buttons()

        self.bg.place(x=0, y=0, relwidth=1, relheight=1)
        self.body.place(x=PAD, y=PAD)
        self.win.update_idletasks()
        self._sync_size()
        self._position()
        self.win.deiconify()
        self.win.lift()
        self.title_entry.focus_set()
        if self.is_new:
            self.title_entry.icursor("end")

    def _sync_size(self) -> None:
        """按内容重新计算窗口尺寸，并把圆角底板重画一遍。"""
        try:
            self.win.update_idletasks()
            w = max(self.theme.px(WIDTH), self.body.winfo_reqwidth()) + PAD * 2
            h = self.body.winfo_reqheight() + PAD * 2
            self.win.geometry(f"{w}x{h}")
            img = self.theme.card(w, h, self.theme.px(14))
            self._card = img
            self.bg.delete("all")
            self.bg.create_image(0, 0, image=img, anchor="nw")
        except Exception:
            pass

    def _label(self, parent, text: str) -> tk.Label:
        """区块小标题。注意必须挂到当前行容器上，挂到 body 上会跑到所有区块后面去。"""
        return tk.Label(parent, text=text, bg=self.theme.pal.card,
                        fg=self.theme.pal.text_dim, font=self.fonts.tiny, anchor="w")

    def _row(self, pady=(0, 8)) -> tk.Frame:
        f = tk.Frame(self.body, bg=self.theme.pal.card)
        f.pack(fill="x", pady=pady)
        return f

    # ---- 各区域 ----
    def _build_header(self) -> None:
        pal = self.theme.pal
        f = self._row((0, 10))
        tk.Label(f, text="编辑任务" if not self.is_new else "新建任务",
                 bg=pal.card, fg=pal.text, font=self.fonts.h1).pack(side="left")
        IconButton(f, self.theme, "close", size=13, command=self.close,
                   bg=pal.card, tooltip="关闭 (Esc)").pack(side="right")

    def _build_title(self) -> None:
        pal = self.theme.pal
        f = self._row((0, 6))
        self.title_var = tk.StringVar(value=self.task.title if self.task else "")
        box = tk.Frame(f, bg=pal.input_bg)
        box.pack(fill="x", ipady=self.theme.px(6))
        self.title_entry = tk.Entry(box, textvariable=self.title_var, bd=0,
                                    relief="flat", highlightthickness=0,
                                    bg=pal.input_bg, fg=pal.text,
                                    insertbackground=pal.accent, font=self.fonts.body)
        self.title_entry.pack(fill="x", padx=self.theme.px(10), pady=self.theme.px(3))
        self.title_entry.bind("<Return>", lambda _e: self.save())

    def _build_note_toggle(self) -> None:
        pal = self.theme.pal
        f = self._row((0, 8))
        has_note = bool(self.task and self.task.note)
        self.note_btn = tk.Label(f, text="－ 备注" if has_note else "＋ 备注",
                                 bg=pal.card, fg=pal.accent, font=self.fonts.small,
                                 cursor="hand2")
        self.note_btn.pack(side="left")
        self.note_btn.bind("<Button-1>", lambda _e: self.toggle_note())
        self.note_frame = tk.Frame(self.body, bg=pal.card)
        self.note_text = tk.Text(self.note_frame, height=3, bd=0, relief="flat",
                                 highlightthickness=0, bg=pal.input_bg, fg=pal.text,
                                 insertbackground=pal.accent, font=self.fonts.body,
                                 wrap="word", padx=self.theme.px(8), pady=self.theme.px(6))
        if has_note:
            self.note_text.insert("1.0", self.task.note)
            self._open_note()

    def toggle_note(self) -> None:
        if self._note_open:
            self._close_note()
        else:
            self._open_note()

    def _open_note(self) -> None:
        self._note_open = True
        self.note_btn.configure(text="－ 备注")
        self.note_frame.pack(fill="x", pady=(0, 8), after=self.note_btn.master)
        self.note_text.pack(fill="x")
        self._sync_size()

    def _close_note(self) -> None:
        self._note_open = False
        self.note_btn.configure(text="＋ 备注")
        self.note_frame.pack_forget()
        self._sync_size()

    def _build_priority(self) -> None:
        f = self._row((0, 4))
        self._label(f, "优先级").pack(anchor="w")
        self.priority_chips = ChipGroup(f, self.theme, self.fonts, PRIORITY_OPTS,
                                        value=self._priority,
                                        command=lambda v: setattr(self, "_priority", v))
        self.priority_chips.pack(fill="x", pady=(self.theme.px(4), 0))

    def _build_due(self) -> None:
        pal = self.theme.pal
        f = self._row((4, 4))
        head = tk.Frame(f, bg=pal.card)
        head.pack(fill="x")
        self._label(head, "期限").pack(side="left")
        # 时间输入放在标题行右侧
        self.time_var = tk.StringVar(value=self._time_str)
        self.time_entry = tk.Entry(head, textvariable=self.time_var, bd=0, relief="flat",
                                   highlightthickness=0, bg=pal.input_bg, fg=pal.text,
                                   insertbackground=pal.accent, font=self.fonts.small,
                                   width=6, justify="center")
        self.time_entry.pack(side="right", ipady=self.theme.px(3), padx=(0, self.theme.px(2)))
        tk.Label(head, text="时间", bg=pal.card, fg=pal.text_faint,
                 font=self.fonts.tiny).pack(side="right", padx=(0, self.theme.px(6)))

        self.date_chips = ChipGroup(f, self.theme, self.fonts, self._date_options(),
                                    value=self._date_key(),
                                    command=self._on_date_chip)
        self.date_chips.pack(fill="x", pady=(self.theme.px(4), 0))

    def _date_options(self) -> list:
        opts = list(DATE_OPTS)
        if self._due_date:
            opts.insert(0, ("custom", self._due_date.strftime("%m月%d日"), None))
        return opts

    def _date_key(self) -> str:
        if not self._due_date:
            return "none"
        today = datetime.date.today()
        delta = (self._due_date - today).days
        return {0: "today", 1: "tomorrow", 2: "after"}.get(delta, "custom")

    def _on_date_chip(self, key: str) -> None:
        today = datetime.date.today()
        if key == "none":
            self._due_date = None
            self._cal_open = False
            self.cal_frame.pack_forget()
        elif key == "today":
            self._due_date = today
        elif key == "tomorrow":
            self._due_date = today + datetime.timedelta(days=1)
        elif key == "after":
            self._due_date = today + datetime.timedelta(days=2)
        elif key == "pick":
            self._toggle_calendar()
            return
        else:
            return
        self._refresh_date_chips()

    def _refresh_date_chips(self) -> None:
        self.date_chips.set_options(self._date_options(), self._date_key())

    def _build_calendar(self) -> None:
        pal = self.theme.pal
        self.cal_frame = tk.Frame(self.body, bg=pal.card)
        self.cal = CalendarGrid(self.cal_frame, self.theme, self.fonts,
                                on_pick=self._on_calendar_pick, bg=pal.card)
        self.cal.pack()
        self.cal.set_selected(self._due_date)

    def _toggle_calendar(self) -> None:
        self._cal_open = not self._cal_open
        if self._cal_open:
            self.cal.set_selected(self._due_date)
            self.cal_frame.pack(fill="x", pady=(0, 8), after=self.date_chips.master)
        else:
            self.cal_frame.pack_forget()
        self._sync_size()

    def _on_calendar_pick(self, date_obj: datetime.date) -> None:
        self._due_date = date_obj
        self._refresh_date_chips()
        self._cal_open = False
        self.cal_frame.pack_forget()
        if not self.time_var.get().strip():
            self.time_var.set("09:00")
        self._sync_size()

    def _build_remind(self) -> None:
        f = self._row((4, 4))
        self._label(f, "提醒").pack(anchor="w")
        self.remind_chips = ChipGroup(f, self.theme, self.fonts, REMIND_OPTS,
                                      value=self._remind,
                                      command=lambda v: setattr(self, "_remind", v))
        self.remind_chips.pack(fill="x", pady=(self.theme.px(4), 0))

    def _build_repeat(self) -> None:
        f = self._row((4, 4))
        head = tk.Frame(f, bg=self.theme.pal.card)
        head.pack(fill="x")
        self._label(head, "重复").pack(side="left")
        self.interval_var = tk.StringVar(value=str(self._interval))
        self.interval_entry = tk.Entry(head, textvariable=self.interval_var, bd=0,
                                       relief="flat", highlightthickness=0,
                                       bg=self.theme.pal.input_bg, fg=self.theme.pal.text,
                                       insertbackground=self.theme.pal.accent,
                                       font=self.fonts.small, width=4, justify="center")
        self.interval_lbl = tk.Label(head, text="天", bg=self.theme.pal.card,
                                     fg=self.theme.pal.text_faint, font=self.fonts.tiny)
        self.repeat_chips = ChipGroup(f, self.theme, self.fonts, REPEAT_OPTS,
                                      value=self._repeat,
                                      command=self._on_repeat)
        self.repeat_chips.pack(fill="x", pady=(self.theme.px(4), 0))

    def _on_repeat(self, key: str) -> None:
        self._repeat = key
        if key == "interval":
            self.interval_entry.pack(side="right", ipady=self.theme.px(3),
                                     padx=(0, self.theme.px(2)))
            self.interval_lbl.pack(side="right", padx=(0, self.theme.px(6)))
        else:
            self.interval_entry.pack_forget()
            self.interval_lbl.pack_forget()
        self._sync_size()

    def _build_list(self) -> None:
        f = self._row((4, 4))
        self._label(f, "分类").pack(anchor="w")
        opts = [(l.id, l.name, None) for l in self.store.lists]
        opts.append(("__new", "＋新建", None))
        self.list_chips = ChipGroup(f, self.theme, self.fonts, opts, value=self._list_id,
                                    command=self._on_list_chip)
        self.list_chips.pack(fill="x", pady=(self.theme.px(4), 0))

    def _on_list_chip(self, key: str) -> None:
        if key != "__new":
            self._list_id = key
            return
        self._prompt_new_list()

    def _prompt_new_list(self) -> None:
        """用一个内联小输入框新建分类，不弹系统对话框（样式统一）。"""
        pal = self.theme.pal
        if getattr(self, "_newlist_frame", None):
            self._newlist_frame.destroy()
        f = tk.Frame(self.body, bg=pal.card)
        self._newlist_frame = f
        var = tk.StringVar()
        entry = tk.Entry(f, textvariable=var, bd=0, relief="flat", highlightthickness=0,
                         bg=pal.input_bg, fg=pal.text, insertbackground=pal.accent,
                         font=self.fonts.small)
        entry.pack(side="left", fill="x", expand=True, ipady=self.theme.px(4),
                   padx=(0, self.theme.px(6)))

        def commit(_e=None):
            name = var.get().strip()
            if name:
                tl = self.store.ensure_list(name)
                self.store.save()
                self._list_id = tl.id
                opts = [(l.id, l.name, None) for l in self.store.lists]
                opts.append(("__new", "＋新建", None))
                self.list_chips.set_options(opts, self._list_id)
            f.destroy()
            self._newlist_frame = None
            self._sync_size()

        entry.bind("<Return>", commit)
        entry.bind("<Escape>", lambda _e: (f.destroy(), self._sync_size()))
        tk.Label(f, text="回车确认", bg=pal.card, fg=pal.text_faint,
                 font=self.fonts.tiny).pack(side="right")
        f.pack(fill="x", pady=(self.theme.px(4), 0), after=self.list_chips.master)
        entry.focus_set()
        self._sync_size()

    def _build_subtasks(self) -> None:
        pal = self.theme.pal
        f = self._row((4, 8))
        self.sub_btn = tk.Label(f, text="＋ 子任务" if not self._subtasks else "－ 子任务",
                                bg=pal.card, fg=pal.accent, font=self.fonts.small,
                                cursor="hand2")
        self.sub_btn.pack(side="left")
        self.sub_btn.bind("<Button-1>", lambda _e: self.toggle_subtasks())
        self.sub_frame = tk.Frame(self.body, bg=pal.card)
        inner = tk.Frame(self.sub_frame, bg=pal.card)
        inner.pack(fill="x")
        self.sub_entry = tk.Entry(inner, bd=0, relief="flat", highlightthickness=0,
                                  bg=pal.input_bg, fg=pal.text,
                                  insertbackground=pal.accent, font=self.fonts.small)
        self.sub_entry.pack(side="left", fill="x", expand=True, ipady=self.theme.px(4))
        self.sub_entry.bind("<Return>", lambda _e: self._add_subtask())
        tk.Label(inner, text="回车添加", bg=pal.card, fg=pal.text_faint,
                 font=self.fonts.tiny).pack(side="right", padx=(self.theme.px(6), 0))
        self.sub_list = tk.Frame(self.sub_frame, bg=pal.card)
        self.sub_list.pack(fill="x", pady=(self.theme.px(6), 0))
        if self._subtasks:
            self._open_subtasks()
        self._render_subtasks()

    def toggle_subtasks(self) -> None:
        if self.sub_frame.winfo_ismapped():
            self.sub_frame.pack_forget()
            self.sub_btn.configure(text="＋ 子任务")
        else:
            self._open_subtasks()
        self._sync_size()

    def _open_subtasks(self) -> None:
        self.sub_btn.configure(text="－ 子任务")
        self.sub_frame.pack(fill="x", pady=(0, 8), after=self.sub_btn.master)

    def _add_subtask(self) -> None:
        text = self.sub_entry.get().strip()
        if not text:
            return
        self._subtasks.append(Subtask.new(text))
        self.sub_entry.delete(0, "end")
        self._render_subtasks()
        self._sync_size()

    def _render_subtasks(self) -> None:
        pal = self.theme.pal
        for child in self.sub_list.winfo_children():
            child.destroy()
        for st in self._subtasks:
            row = tk.Frame(self.sub_list, bg=pal.card)
            row.pack(fill="x", pady=1)
            var = tk.BooleanVar(value=st.done)
            cb = tk.Checkbutton(row, variable=var, bg=pal.card, activebackground=pal.card,
                                selectcolor=pal.input_bg, bd=0, highlightthickness=0,
                                command=lambda s=st, v=var: setattr(s, "done", v.get()))
            cb.pack(side="left")
            tk.Label(row, text=st.title, bg=pal.card, fg=pal.text,
                     font=self.fonts.small, anchor="w").pack(side="left", fill="x", expand=True)
            tk.Label(row, text="✕", bg=pal.card, fg=pal.text_faint,
                     font=self.fonts.tiny, cursor="hand2").pack(side="right")

            def remove(_e, s=st):
                self._subtasks = [x for x in self._subtasks if x.id != s.id]
                self._render_subtasks()
                self._sync_size()

            row.winfo_children()[-1].bind("<Button-1>", remove)

    def _build_buttons(self) -> None:
        pal = self.theme.pal
        f = self._row((6, 0))
        self._button(f, "保存", self.save, primary=True).pack(side="right")
        if not self.is_new:
            self._button(f, "删除", self.delete, danger=True).pack(side="right",
                                                                  padx=(0, self.theme.px(8)))
        self._button(f, "取消", self.close).pack(side="right", padx=(0, self.theme.px(8)))

    def _button(self, parent, text: str, command: Callable, *,
                primary: bool = False, danger: bool = False) -> tk.Label:
        pal = self.theme.pal
        bg = pal.accent if primary else (pal.danger if danger else pal.input_bg)
        fg = "#FFFFFF" if (primary or danger) else pal.text_dim
        lbl = tk.Label(parent, text=text, bg=bg, fg=fg, font=self.fonts.small,
                       padx=self.theme.px(16), pady=self.theme.px(6), cursor="hand2")
        lbl.bind("<Button-1>", lambda _e: command())
        return lbl

    # ---------------- 逻辑 ----------------
    @staticmethod
    def _remind_offset_of(task: Task) -> int:
        if task.due_dt and task.remind_at:
            from ..models import parse_dt
            ra = parse_dt(task.remind_at)
            if ra:
                return int((task.due_dt - ra).total_seconds() // 60)
        return 0

    def _parse_time(self) -> Optional[tuple[int, int]]:
        raw = self.time_var.get().strip()
        if not raw:
            return None
        m = re.match(r"^(\d{1,2})\s*[:：点时]\s*(\d{1,2})?\s*分?$", raw)
        if not m:
            m = re.match(r"^(\d{1,2})$", raw)
            if not m:
                return None
            return (int(m.group(1)) % 24, 0)
        return (int(m.group(1)) % 24, int(m.group(2) or 0) % 60)

    def _collect(self) -> Optional[Task]:
        from .. import nlp
        from ..models import dt_to_iso

        title = self.title_var.get().strip()
        if not title:
            self.title_entry.focus_set()
            self._flash(self.title_entry.master)
            return None

        # 标题里如果带了时间/优先级，也顺手解析（方便从快速输入回填后直接保存）
        parsed = nlp.parse(title)
        due = None
        time_part = self._parse_time()
        if self._due_date:
            hh, mm = time_part or (9, 0)
            due = datetime.datetime.combine(self._due_date, datetime.time(hh, mm))
        elif time_part and parsed.due:
            due = parsed.due

        task = self.task or Task.new(title)
        if self.is_new:
            task.title = title
        else:
            task.title = title
        task.priority = int(self._priority) if self._priority.isdigit() else PRIORITY_NONE
        task.list_id = self._list_id
        task.due = dt_to_iso(due)
        task.remind_at = None
        task.note = self.note_text.get("1.0", "end").strip() if self._note_open else (
            self.task.note if self.task else "")
        task.subtasks = self._subtasks

        offset = int(self._remind) if self._remind.isdigit() else 0
        if due and offset:
            task.remind_at = dt_to_iso(due - datetime.timedelta(minutes=offset))

        if self._repeat:
            rule = {"freq": self._repeat, "interval": 1}
            if self._repeat == "interval":
                try:
                    rule["interval"] = max(1, int(self.interval_var.get()))
                except ValueError:
                    rule["interval"] = 1
                rule["unit"] = "day"
            task.repeat = rule
        else:
            task.repeat = None
        return task

    def _flash(self, widget) -> None:
        """标题为空时闪一下提示，而不是弹一个丑对话框。"""
        pal = self.theme.pal
        try:
            widget.configure(bg=pal.danger)
            self.win.after(220, lambda: widget.configure(bg=pal.input_bg))
        except Exception:
            pass

    def save(self) -> None:
        task = self._collect()
        if task is None:
            return
        self.result = task
        self.app.save_task(task, is_new=self.is_new)
        self.close()

    def delete(self) -> None:
        if self.task:
            self.app.delete_task(self.task)
        self.close()

    def _position(self) -> None:
        """放在小卡片旁边，避免盖住它。"""
        try:
            self.win.update_idletasks()
            w, h = self.win.winfo_width(), self.win.winfo_height()
            sw = self.win.winfo_screenwidth()
            sh = self.win.winfo_screenheight()
            if self.app.main and self.app.main.canvas.winfo_ismapped():
                wx = self.root.winfo_x()
                wy = self.root.winfo_y()
                ww = self.root.winfo_width()
                x = wx + ww + self.theme.px(8)
                if x + w > sw:
                    x = max(0, wx - w - self.theme.px(8))
                y = min(max(0, wy), sh - h)
            else:
                x, y = (sw - w) // 2, (sh - h) // 3
            self.win.geometry(f"+{int(x)}+{int(y)}")
        except Exception:
            pass

    def close(self) -> None:
        try:
            self.win.destroy()
        except Exception:
            pass
        self.app.on_editor_closed(self)
