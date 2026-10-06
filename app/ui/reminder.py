"""提醒弹窗：到点后在右下角弹出的卡片，带【完成】【延后】【明天】【改期】按钮。

这是唯一会主动打断你的界面，所以做得很克制：一条任务一张卡，点一下就能处理掉。
另外还有"错过的提醒"汇总窗口，用于开机后一次性告诉你关机期间到期了什么。
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional

from .. import nlp
from ..models import Task
from .common import CardWindow, FontSet, IconButton

WIDTH = 330


class ReminderPopup(CardWindow):
    """单条任务的提醒卡。"""

    _stack: list["ReminderPopup"] = []

    def __init__(self, app, task: Task):
        super().__init__(app, app.theme, app.fonts, width=WIDTH, topmost=True)
        self.app = app
        self.store = app.store
        self.task = task
        self._build()
        self.finalize(position="none")
        self._reposition()
        ReminderPopup._stack.append(self)
        # 关闭时把自己从堆栈里摘掉，并重排其余的
        self.bind("<Destroy>", self._on_destroy)

    def _on_destroy(self, event) -> None:
        if event.widget is self and self in ReminderPopup._stack:
            ReminderPopup._stack.remove(self)
            for p in ReminderPopup._stack:
                p._reposition()

    def _reposition(self) -> None:
        """多张卡时依次向上堆叠，不互相遮挡。"""
        try:
            idx = ReminderPopup._stack.index(self)
        except ValueError:
            idx = 0
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = sw - w - self.theme.px(24)
        y = sh - h - self.theme.px(56) - idx * (h + self.theme.px(10))
        self.geometry(f"+{int(x)}+{int(max(self.theme.px(20), y))}")

    def _build(self) -> None:
        pal, th, fonts = self.theme.pal, self.theme, self.fonts
        task = self.task

        head = tk.Frame(self.body, bg=pal.card)
        head.pack(fill="x")
        tk.Label(head, text="提醒", bg=pal.card, fg=pal.accent,
                 font=fonts.tiny).pack(side="left")
        IconButton(head, th, "close", size=12, command=self.dismiss,
                   bg=pal.card, tooltip="稍后再说").pack(side="right")

        tk.Label(self.body, text=task.title, bg=pal.card, fg=pal.text,
                 font=fonts.title, wraplength=th.px(WIDTH - 40), justify="left",
                 anchor="w").pack(fill="x", pady=(th.px(6), th.px(2)))

        meta = []
        due = task.due_dt
        if due:
            meta.append(nlp.humanize_due(due))
        if task.list_id != "inbox":
            meta.append(self.store.list_name(task.list_id))
        if task.note:
            meta.append("有备注")
        if meta:
            tk.Label(self.body, text=" · ".join(meta), bg=pal.card, fg=pal.text_dim,
                     font=fonts.small, anchor="w").pack(fill="x")
        if task.note:
            tk.Label(self.body, text=task.note[:120], bg=pal.card, fg=pal.text_faint,
                     font=fonts.small, wraplength=th.px(WIDTH - 40), justify="left",
                     anchor="w").pack(fill="x", pady=(th.px(4), 0))

        btns = tk.Frame(self.body, bg=pal.card)
        btns.pack(fill="x", pady=(th.px(14), 0))
        self._btn(btns, "完成", lambda: self._complete(), primary=True).pack(side="left")
        self._btn(btns, "10分钟", lambda: self._postpone(minutes=10)).pack(side="left",
                                                                         padx=(th.px(6), 0))
        self._btn(btns, "1小时", lambda: self._postpone(minutes=60)).pack(side="left",
                                                                        padx=(th.px(6), 0))
        self._btn(btns, "明天", lambda: self._postpone(days=1)).pack(side="left",
                                                                   padx=(th.px(6), 0))
        self._btn(btns, "改期…", self._open_editor).pack(side="right")

    def _btn(self, parent, text: str, command: Callable, *,
             primary: bool = False, danger: bool = False) -> tk.Label:
        pal, th = self.theme.pal, self.theme
        bg = pal.accent if primary else (pal.input_bg)
        fg = "#FFFFFF" if primary else (pal.danger if danger else pal.text_dim)
        lbl = tk.Label(parent, text=text, bg=bg, fg=fg, font=self.fonts.small,
                       padx=th.px(12), pady=th.px(6), cursor="hand2")
        lbl.bind("<Button-1>", lambda _e: command())
        lbl.bind("<Enter>", lambda _e: lbl.configure(bg=pal.card_hover if not primary else pal.accent))
        lbl.bind("<Leave>", lambda _e: lbl.configure(bg=bg))
        return lbl

    # ---- 动作 ----
    def _complete(self) -> None:
        self.app.complete_from_reminder(self.task)
        self.destroy()

    def _postpone(self, minutes: int = 0, days: int = 0) -> None:
        self.store.postpone(self.task.id, minutes=minutes, days=days)
        self.app.toast(f"已延后：{self.task.title}")
        self.destroy()

    def _open_editor(self) -> None:
        self.destroy()
        self.app.open_editor(self.task)

    def dismiss(self) -> None:
        """关掉但不算完成 —— 标记为已提醒，不再重复弹。"""
        from ..models import now_iso
        self.task.notified_at = now_iso()
        self.store.save()
        self.destroy()


class MissedSummary(CardWindow):
    """开机后汇总关机期间错过的提醒，避免一次弹十几个窗。"""

    def __init__(self, app, tasks: list[Task]):
        super().__init__(app, app.theme, app.fonts, width=WIDTH + 20, topmost=True)
        self.app = app
        self.store = app.store
        self.tasks = tasks[:8]
        self._build(len(tasks))
        self.finalize(position="none")
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{int(sw - w - self.theme.px(24))}+{int(sh - h - self.theme.px(56))}")

    def _build(self, total: int) -> None:
        pal, th, fonts = self.theme.pal, self.theme, self.fonts
        head = tk.Frame(self.body, bg=pal.card)
        head.pack(fill="x")
        tk.Label(head, text="错过的提醒", bg=pal.card, fg=pal.p_mid,
                 font=fonts.tiny).pack(side="left")
        IconButton(head, th, "close", size=12, command=self.destroy,
                   bg=pal.card, tooltip="知道了").pack(side="right")

        tk.Label(self.body, text=f"你不在的时候有 {total} 项到期了",
                 bg=pal.card, fg=pal.text, font=fonts.title, anchor="w").pack(
            fill="x", pady=(th.px(6), th.px(8)))

        for t in self.tasks:
            row = tk.Frame(self.body, bg=pal.card)
            row.pack(fill="x", pady=1)
            due = t.due_dt
            label = nlp.humanize_due(due) if due else ""
            color = pal.danger if t.is_overdue() else pal.text_faint
            tk.Label(row, text=label, bg=pal.card, fg=color, font=fonts.tiny,
                     width=8, anchor="w").pack(side="left")
            tk.Label(row, text=t.title, bg=pal.card, fg=pal.text, font=fonts.small,
                     anchor="w").pack(side="left", fill="x", expand=True)
        if total > len(self.tasks):
            tk.Label(self.body, text=f"…… 还有 {total - len(self.tasks)} 项",
                     bg=pal.card, fg=pal.text_faint, font=fonts.tiny, anchor="w").pack(
                fill="x", pady=(th.px(4), 0))

        btns = tk.Frame(self.body, bg=pal.card)
        btns.pack(fill="x", pady=(th.px(14), 0))
        lbl = tk.Label(btns, text="知道了", bg=pal.accent, fg="#FFFFFF",
                       font=fonts.small, padx=th.px(16), pady=th.px(6), cursor="hand2")
        lbl.pack(side="right")
        lbl.bind("<Button-1>", lambda _e: self.destroy())
        lbl2 = tk.Label(btns, text="打开主界面", bg=pal.input_bg, fg=pal.text_dim,
                        font=fonts.small, padx=th.px(16), pady=th.px(6), cursor="hand2")
        lbl2.pack(side="right", padx=(0, th.px(8)))
        lbl2.bind("<Button-1>", lambda _e: (self.app.show_window(), self.destroy()))
