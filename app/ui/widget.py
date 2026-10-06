"""主窗口：贴在桌面上的待办卡片。

全部内容自绘在一个 Canvas 上（每行不是一个控件），所以几百条任务也不卡；
只有输入框是真控件 —— 因为中文输入法需要真正的 Entry 才能正常工作。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, Optional

from .. import nlp
from ..models import PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_MEDIUM, PRIORITY_NONE, Task
from .common import FontSet, IconButton, Tooltip, ellipsize

VIEWS = [("today", "今天"), ("week", "本周"), ("all", "全部"),
         ("quadrant", "象限"), ("done", "完成")]

HEADER_H = 44
TABS_H = 38
INPUT_H = 54
FOOTER_H = 30
MARGIN = 10


class MainWindow:
    def __init__(self, app):
        self.app = app
        self.root: tk.Tk = app.root
        self.cfg = app.config
        self.store = app.store
        self.theme = app.theme
        self.fonts: FontSet = app.fonts

        self.view = self.cfg.get("last_view") or "today"
        self.search = ""
        self.list_filter = ""
        self.search_open = False

        self.hover_id: Optional[str] = None
        self.row_pos: dict[str, tuple[int, int]] = {}
        self._row_tasks: list[Task] = []

        self._drag_task: Optional[Task] = None
        self._drag_start: Optional[tuple[int, int]] = None
        self._dragging = False
        self._press_task: Optional[Task] = None
        self._win_drag_origin: Optional[tuple[int, int]] = None
        self._window_dragging = False

        self._preview_after: Optional[str] = None
        self._empty_label = ""

        self.build()

    # ================= 布局 =================
    @property
    def W(self) -> int:
        return self.root.winfo_width()

    @property
    def H(self) -> int:
        return self.root.winfo_height()

    def px(self, v: float) -> int:
        return self.theme.px(v)

    def build(self) -> None:
        th, pal = self.theme, self.theme.pal
        self.canvas = tk.Canvas(self.root, bg=pal.key, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_bg_click)
        self.canvas.bind("<Configure>", self._on_configure)

        # ---- 列表区（独立 Canvas，天然裁剪 + 平滑滚动）----
        self.list_canvas = tk.Canvas(self.root, bg=pal.card, highlightthickness=0,
                                     bd=0, takefocus=0)
        self.list_canvas.bind("<MouseWheel>", self._on_wheel)
        self.list_canvas.bind("<Button-1>", self._on_list_click)
        self.list_canvas.bind("<B1-Motion>", self._on_list_drag)
        self.list_canvas.bind("<ButtonRelease-1>", self._on_list_release)
        self.list_canvas.bind("<Motion>", self._on_list_motion)
        self.list_canvas.bind("<Leave>", lambda _e: self._set_hover(None))
        self.list_canvas.bind("<Button-3>", self._on_list_right_click)
        self.list_canvas.bind("<Double-Button-1>", self._on_list_double)

        # ---- 输入框（真 Entry，中文输入法必需）----
        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(self.root, textvariable=self.entry_var, bd=0,
                              relief="flat", highlightthickness=0,
                              bg=pal.input_bg, fg=pal.text,
                              insertbackground=pal.accent,
                              font=self.fonts.body)
        self.entry.bind("<Return>", self._on_entry_enter)
        self.entry.bind("<Escape>", lambda _e: self._end_edit())
        self.entry.bind("<KeyRelease>", self._on_entry_key)
        self.entry.bind("<FocusIn>", lambda _e: self._refresh_preview())

        # 占位提示：Entry 会盖住画在画布上的文字，所以用一个浮在上面的 Label
        self.placeholder = tk.Label(self.root, text="添加任务…", bg=pal.input_bg,
                                    fg=pal.text_faint, font=self.fonts.small,
                                    anchor="w", cursor="xterm")
        self.placeholder.bind("<Button-1>", lambda _e: self.entry.focus_set())

        # ---- 头部按钮 ----
        self.header = tk.Frame(self.root, bg=pal.card)
        self.title_lbl = tk.Label(self.header, text="待办", bg=pal.card, fg=pal.text,
                                  font=self.fonts.h1, cursor="fleur")
        self.title_lbl.pack(side="left", padx=(self.px(MARGIN + 12), self.px(6)))
        self.badge_lbl = tk.Label(self.header, bg=pal.accent_soft, fg=pal.accent,
                                  font=self.fonts.tiny, bd=0)
        self.btns: dict[str, IconButton] = {}
        # 从右往左依次排开：最右是"收进托盘"，最左是搜索
        specs = [
            ("min", "收进托盘 (Ctrl+Alt+T 唤回)", self.app.minimize),
            ("desktop", "切换贴在桌面的方式", self.app.cycle_desktop_mode),
            ("gear", "设置", self.app.open_settings),
            ("mic", "语音 / 文字转待办 (Ctrl+Alt+Space)", self.app.open_voice),
            ("search", "搜索 (Ctrl+F)", self.toggle_search),
        ]
        for name, tip, cmd in specs:
            b = IconButton(self.header, th, name, size=15, command=cmd, tooltip=tip)
            b.pack(side="right", padx=self.px(2))
            self.btns[name] = b

        # 顶栏空白处可以拖动窗口（点按钮不会触发）
        for w in (self.header, self.title_lbl):
            w.bind("<Button-1>", self._start_window_drag)
            w.bind("<B1-Motion>", self._do_window_drag)
            w.bind("<ButtonRelease-1>", self._end_window_drag)
            w.bind("<Double-Button-1>", lambda _e: self.app.cycle_desktop_mode())

        self.place_all()

    def place_all(self) -> None:
        w, h = self.W, self.H
        if w <= 1 or h <= 1:
            return
        # 顶栏是矩形实体 Frame，必须往内缩，否则会把卡片的圆角盖住（四角会变成方的）
        edge = self.px(8)
        self.header.place(x=edge, y=self.px(5), width=w - edge * 2,
                          height=self.px(HEADER_H) - self.px(5))
        list_y = self.px(HEADER_H + TABS_H)
        list_h = h - list_y - self.px(INPUT_H + FOOTER_H)
        self.list_canvas.place(x=0, y=list_y, width=w, height=max(self.px(40), list_h))
        box_h = self.px(30)
        box_y = h - self.px(FOOTER_H) - self.px(6) - box_h
        self.entry.place(x=self.px(MARGIN + 12), y=box_y + self.px(5),
                         width=w - self.px(2 * MARGIN + 24),
                         height=box_h - self.px(10))
        self.placeholder.place(x=self.px(MARGIN + 12), y=box_y + self.px(5),
                               width=w - self.px(2 * MARGIN + 24),
                               height=box_h - self.px(10))
        self.placeholder.lift()

    # ================= 绘制 =================
    def _on_configure(self, event) -> None:
        """尺寸变化才重绘，避免 place 触发 Configure 造成死循环。"""
        if (event.width, event.height) != getattr(self, "_last_size", None):
            self._last_size = (event.width, event.height)
            self.redraw_all()

    def redraw_all(self) -> None:
        if self.W <= 1:
            return
        self.place_all()
        self._draw_shell()
        self.refresh_list()
        self._draw_footer()
        self._refresh_preview()

    def _card_image(self):
        """卡片底板图（圆角 + 透明四角）。按尺寸缓存，theme 内部还有一层像素级缓存。"""
        w, h = self.W, self.H
        key = (w, h, self.theme.pal.name, self.cfg.get("opacity"))
        if getattr(self, "_card_key", None) != key or getattr(self, "_card_img", None) is None:
            self._card_img = self.theme.card(w, h, self.px(14))
            self._card_key = key
        return self._card_img

    def _draw_shell(self) -> None:
        c, pal = self.canvas, self.theme.pal
        c.delete("all")
        c.configure(bg=pal.key)
        self.list_canvas.configure(bg=pal.card)
        self.header.configure(bg=pal.card)
        self.title_lbl.configure(bg=pal.card, fg=pal.text, font=self.fonts.h1)
        self.entry.configure(bg=pal.input_bg, fg=pal.text, insertbackground=pal.accent)
        img = self._card_image()
        c.create_image(0, 0, image=img, anchor="nw")

        # 未完成数量徽标（放在标题右边，用圆角图片保证和整体风格一致）
        counts = self.store.counts()
        if counts["active"]:
            text = str(counts["active"])
            w = self.fonts.tiny.measure(text) + self.px(12)
            h = self.px(17)
            badge = self.theme.round_rect(w, h, h // 2, pal.accent_soft)
            self._badge_img = badge
            self.badge_lbl.configure(image=badge, text=text, compound="center",
                                     fg=pal.accent, bg=pal.card)
            if not self.badge_lbl.winfo_ismapped():
                self.badge_lbl.pack(side="left")
        else:
            self.badge_lbl.pack_forget()

        self._draw_tabs()

    def _draw_tabs(self) -> None:
        c, pal = self.canvas, self.theme.pal
        c.delete("tabs")
        y0 = self.px(HEADER_H + 5)
        h = self.px(28)
        x0 = self.px(MARGIN)
        x1 = self.W - self.px(MARGIN)
        track = self.theme.round_rect(x1 - x0, h, self.px(9), pal.input_bg)
        self._track_img = track
        c.create_image(x0, y0, image=track, anchor="nw", tags=("tabs",))
        seg_w = (x1 - x0) // len(VIEWS)
        for i, (key, label) in enumerate(VIEWS):
            sx = x0 + i * seg_w
            active = (key == self.view)
            if active:
                pad = self.px(3)
                pill = self.theme.round_rect(seg_w - pad * 2, h - pad * 2,
                                             self.px(7), pal.card)
                self._seg_img = pill
                c.create_image(sx + pad, y0 + pad, image=pill, anchor="nw", tags=("tabs",))
            count = self._view_count(key)
            text = label + (f" {count}" if count and key != "all" else "")
            c.create_text(sx + seg_w // 2, y0 + h // 2, text=text, anchor="center",
                          fill=pal.text if active else pal.text_dim,
                          font=self.fonts.small if self.px(1) else self.fonts.small,
                          tags=("tabs", f"tab:{key}"))

    def _view_count(self, key: str) -> int:
        try:
            if key in ("today", "week", "quadrant"):
                return len(self.store.view(key))
            if key == "all":
                return 0
            if key == "done":
                return 0
        except Exception:
            pass
        return 0

    # ---- 列表 ----
    def refresh_list(self) -> None:
        self.app.refresh_fonts_if_needed()
        c = self.list_canvas
        c.delete("all")
        self.row_pos.clear()
        tasks = self._current_tasks()
        self._row_tasks = tasks
        w = c.winfo_width() or self.W
        y = self.px(4)

        if not tasks:
            self._draw_empty(w)
            c.configure(scrollregion=(0, 0, 0, y + self.px(60)))
            return

        if self.view == "quadrant":
            y = self._draw_quadrants(tasks, w, y)
        else:
            for t in tasks:
                y = self._draw_row(t, w, y)
        c.configure(scrollregion=(0, 0, 0, y + self.px(8)))

    def _current_tasks(self) -> list[Task]:
        try:
            return self.store.view(self.view, search=self.search, list_filter=self.list_filter)
        except Exception:
            return []

    def _draw_empty(self, w: int) -> None:
        c, pal = self.list_canvas, self.theme.pal
        msgs = {
            "today": ("今天没有待办", "享受清闲，或点下面加一条"),
            "week": ("本周很空", "点下面输入框添加任务"),
            "all": ("还没有任务", "试试输入「明天18:00 交作业 !高」"),
            "done": ("还没有完成的任务", "完成后会自动归档到这里"),
            "quadrant": ("暂无任务", ""),
        }
        title, sub = msgs.get(self.view, ("暂无任务", ""))
        if self.search:
            title, sub = f"没找到「{self.search}」", "换个关键词试试"
        cy = self.px(90)
        c.create_text(w // 2, cy, text=title, anchor="center",
                      fill=pal.text_dim, font=self.fonts.body)
        if sub:
            c.create_text(w // 2, cy + self.px(22), text=sub, anchor="center",
                          fill=pal.text_faint, font=self.fonts.small)

    # ---- 单行 ----
    def _meta_parts(self, task: Task) -> list[tuple[str, str]]:
        """返回 [(文本, 颜色), ...]，用 ' · ' 连接。"""
        pal = self.theme.pal
        parts: list[tuple[str, str]] = []
        due = task.due_dt
        if due is not None:
            label = nlp.humanize_due(due)
            if task.is_overdue():
                color = pal.danger
            elif task.is_due_today():
                color = pal.p_mid
            else:
                color = pal.text_faint
            parts.append((label, color))
        if task.repeat:
            parts.append((task.repeat_label, pal.text_faint))
        if task.list_id != "inbox":
            parts.append((self.store.list_name(task.list_id), pal.text_faint))
        if task.subtasks:
            done = sum(1 for s in task.subtasks if s.done)
            parts.append((f"子任务 {done}/{len(task.subtasks)}", pal.text_faint))
        elif task.note:
            parts.append(("备注", pal.text_faint))
        if task.postpone_count >= 2:
            parts.append((f"已延期{task.postpone_count}次", pal.p_mid))
        return parts

    def _row_height(self, task: Task) -> int:
        return self.px(46 if self._meta_parts(task) else 36)

    def _draw_row(self, task: Task, w: int, y: int) -> int:
        c, pal, th = self.list_canvas, self.theme.pal, self.theme
        h = self._row_height(task)
        tag = f"r:{task.id}"
        hovered = self.hover_id == task.id
        self.row_pos[task.id] = (y, h)

        if hovered:
            bg = th.round_rect(w - self.px(8), h - self.px(2), self.px(8), pal.card_hover)
            self._row_hover_img = bg
            c.create_image(self.px(4), y + self.px(1), image=bg, anchor="nw", tags=(tag,))

        # 优先级色条
        if task.priority != PRIORITY_NONE:
            bar = th.bar(self.px(3), self.px(20) if h > self.px(40) else self.px(16),
                         th.priority_colors[task.priority])
            self._row_bar_img = bar
            c.create_image(self.px(11), y + h // 2, image=bar, anchor="w", tags=(tag,))

        # 复选框
        state = "checked" if task.done else ("hover" if hovered else "empty")
        if task.subtasks and not task.done:
            done = sum(1 for s in task.subtasks if s.done)
            if 0 < done < len(task.subtasks):
                state = "partial"
        cb = th.checkbox(self.px(17), state)
        self._row_cb_img = cb
        cx = self.px(20)
        c.create_image(cx, y + h // 2, image=cb, anchor="w", tags=(tag, f"chk:{task.id}"))

        text_x = cx + self.px(17) + self.px(11)
        right_limit = w - self.px(14)
        avail = right_limit - text_x

        actions_w = self.px(52)
        if hovered:
            avail -= actions_w

        title_font = self.fonts.title_done if task.done else self.fonts.title
        parts = self._meta_parts(task)
        title_y = y + (self.px(11) if parts else h // 2)
        title = ellipsize(task.title, title_font, avail)
        c.create_text(text_x, title_y, text=title, anchor="nw",
                      fill=pal.done_text if task.done else pal.text,
                      font=title_font, tags=(tag, f"title:{task.id}"))

        if parts:
            self._draw_meta(parts, text_x, y + self.px(30), avail, task, tag)

        if hovered and not task.done:
            self._draw_row_actions(task, w, y, h, tag)
        return y + h

    def _draw_meta(self, parts, x: int, y: int, avail: int, task: Task, tag: str) -> None:
        c, pal = self.list_canvas, self.theme.pal
        font = self.fonts.small
        sep = "  ·  "
        sep_w = font.measure(sep)
        used = 0
        for i, (text, color) in enumerate(parts):
            prefix = sep if i else ""
            w_text = font.measure(prefix + text)
            if used + w_text > avail:
                # 放不下就截断最后一段
                remain = avail - used - (sep_w if not i else 0)
                if remain > font.measure("…") + 4:
                    t = ellipsize(prefix + text, font, avail - used)
                    c.create_text(x + used, y, text=t, anchor="nw", fill=color,
                                  font=font, tags=(tag,))
                break
            c.create_text(x + used, y, text=prefix + text, anchor="nw",
                          fill=color, font=font, tags=(tag,))
            used += w_text

    def _draw_row_actions(self, task: Task, w: int, y: int, h: int, tag: str) -> None:
        c, th = self.list_canvas, self.theme
        size = self.px(22)
        cy = y + h // 2 - size // 2
        x = w - self.px(14) - size
        # 编辑
        edit_bg = th.round_rect(size, size, self.px(6), self.theme.pal.card_hover)
        self._act_edit_img = edit_bg
        c.create_image(x, cy, image=edit_bg, anchor="nw", tags=(tag, f"act:edit:{task.id}"))
        ic = th.icon("edit", self.px(13), self.theme.pal.text_dim)
        self._act_edit_ic = ic
        c.create_image(x + size // 2, cy + size // 2, image=ic, anchor="center",
                       tags=(tag, f"act:edit:{task.id}"))
        # 删除
        x -= size + self.px(4)
        del_bg = th.round_rect(size, size, self.px(6), self.theme.pal.card_hover)
        self._act_del_img = del_bg
        c.create_image(x, cy, image=del_bg, anchor="nw", tags=(tag, f"act:del:{task.id}"))
        ic2 = th.icon("trash", self.px(13), self.theme.pal.danger)
        self._act_del_ic = ic2
        c.create_image(x + size // 2, cy + size // 2, image=ic2, anchor="center",
                       tags=(tag, f"act:del:{task.id}"))

    def _draw_quadrants(self, tasks: list[Task], w: int, y: int) -> int:
        c, pal = self.list_canvas, self.theme.pal
        now = __import__("datetime").datetime.now()
        buckets = [
            ("重要且紧急", pal.p_high, lambda t: t.priority >= PRIORITY_HIGH and (t.is_overdue() or t.is_due_today())),
            ("重要不紧急", pal.p_mid, lambda t: t.priority >= PRIORITY_HIGH and not (t.is_overdue() or t.is_due_today())),
            ("紧急不重要", pal.p_low, lambda t: t.priority < PRIORITY_HIGH and (t.is_overdue() or t.is_due_today())),
            ("其他", pal.text_faint, lambda t: t.priority < PRIORITY_HIGH and not (t.is_overdue() or t.is_due_today())),
        ]
        used: set[str] = set()
        for title, color, pred in buckets:
            group = [t for t in tasks if t.id not in used and pred(t)]
            for t in group:
                used.add(t.id)
            if not group:
                continue
            c.create_text(self.px(MARGIN + 8), y + self.px(6), text=f"{title} ({len(group)})",
                          anchor="nw", fill=color, font=self.fonts.small, tags=("qh",))
            y += self.px(24)
            for t in group:
                y = self._draw_row(t, w, y)
            y += self.px(8)
        return y

    # ---- 底栏 ----
    def _draw_footer(self) -> None:
        c, pal = self.canvas, self.theme.pal
        c.delete("footer")
        w, h = self.W, self.H
        counts = self.store.counts()
        total = counts["done_today"] + counts["active"]
        ratio = (counts["done_today"] / total) if total else 0.0
        y = h - self.px(FOOTER_H)
        text = f"{counts['done_today']}/{total} 已完成"
        streak = self.store.streak_days()
        if streak > 1:
            text += f" · 连续 {streak} 天"
        if counts["overdue"]:
            text += f" · 逾期 {counts['overdue']}"
        c.create_text(self.px(MARGIN + 12), y + self.px(FOOTER_H) // 2, text=text,
                      anchor="w", fill=pal.text_faint, font=self.fonts.tiny, tags=("footer",))
        bar_w = self.px(70)
        bar = self.theme.progress(bar_w, self.px(4), ratio)
        self._prog_img = bar
        c.create_image(w - self.px(MARGIN + 12) - bar_w, y + self.px(FOOTER_H) // 2,
                       image=bar, anchor="w", tags=("footer",))

    # ---- 输入框与实时解析 ----
    def _refresh_preview(self) -> None:
        c, pal = self.canvas, self.theme.pal
        c.delete("preview")
        raw = self.entry_var.get().strip()
        box_y = self.H - self.px(FOOTER_H) - self.px(6) - self.px(30)
        # 输入框底色
        box = self.theme.round_rect(self.W - self.px(2 * MARGIN), self.px(30),
                                    self.px(9), pal.input_bg)
        self._box_img = box
        c.create_image(self.px(MARGIN), box_y, image=box, anchor="nw", tags=("preview",))
        if raw:
            self.placeholder.place_forget()
        else:
            self.placeholder.configure(
                text="搜索任务…" if self.search_open else "添加任务…  例：明天18:00 交作业 !高")
            self.placeholder.place(x=self.px(MARGIN + 12), y=box_y + self.px(5),
                                   width=self.W - self.px(2 * MARGIN + 24),
                                   height=self.px(30) - self.px(10))
            self.placeholder.lift()
        if raw and not self.search_open:
            parsed = nlp.parse(raw)
            bits = []
            if parsed.due:
                bits.append("⏰ " + nlp.humanize_due(parsed.due))
            if parsed.priority is not None:
                bits.append("⚑ " + {3: "高", 2: "中", 1: "低", 0: "无"}[parsed.priority])
            if parsed.list_name:
                bits.append("# " + parsed.list_name)
            if bits:
                c.create_text(self.px(MARGIN + 12), box_y - self.px(10),
                              text="将记录为：" + "   ".join(bits), anchor="w",
                              fill=pal.accent, font=self.fonts.tiny, tags=("preview",))

    def _on_entry_key(self, _event=None) -> None:
        if self._preview_after:
            try:
                self.root.after_cancel(self._preview_after)
            except Exception:
                pass
        self._preview_after = self.root.after(30, self._refresh_preview)

    def _on_entry_enter(self, _event=None) -> str:
        raw = self.entry_var.get().strip()
        if not raw:
            return "break"
        if self.search_open:
            self.search = raw
            self.refresh_list()
            return "break"
        self.app.quick_add(raw)
        self.entry_var.set("")
        self._refresh_preview()
        self.list_canvas.yview_moveto(0)
        return "break"

    def _end_edit(self) -> None:
        self.entry.selection_clear()
        self.root.focus_set()
        self.app.on_edit_finished()

    def toggle_search(self) -> None:
        self.search_open = not self.search_open
        if self.search_open:
            self.entry_var.set(self.search)
            self.entry.focus_set()
            self.entry.icursor("end")
        else:
            self.search = ""
            self.entry_var.set("")
            self.refresh_list()
        self._refresh_preview()

    # ================= 交互 =================
    def _set_hover(self, task_id: Optional[str]) -> None:
        if task_id == self.hover_id:
            return
        old, self.hover_id = self.hover_id, task_id
        for tid in (old, task_id):
            if tid:
                self._redraw_row_by_id(tid)

    def _redraw_row_by_id(self, task_id: str) -> None:
        pos = self.row_pos.get(task_id)
        task = next((t for t in self._row_tasks if t.id == task_id), None)
        if not pos or task is None:
            return
        y, h = pos
        c = self.list_canvas
        c.delete(f"r:{task_id}")
        w = c.winfo_width() or self.W
        self._draw_row(task, w, y)

    def _on_list_motion(self, event) -> None:
        if self._dragging:
            self._on_drag_motion(event)
            return
        item = self.list_canvas.find_withtag("current")
        task_id = None
        if item:
            for tag in self.list_canvas.gettags(item[0]):
                if tag.startswith("r:"):
                    task_id = tag[2:]
                    break
        self._set_hover(task_id)

    def _on_wheel(self, event) -> str:
        self.list_canvas.yview_scroll(-(event.delta // 120) * 3, "units")
        return "break"

    def _tag_of(self, item_id) -> str:
        if not item_id:
            return ""
        for tag in self.list_canvas.gettags(item_id):
            if tag.startswith("act:") or tag.startswith("chk:"):
                return tag
        return ""

    def _task_from_item(self, item_id) -> Optional[Task]:
        if not item_id:
            return None
        for tag in self.list_canvas.gettags(item_id):
            if tag.startswith("r:"):
                tid = tag[2:]
                return next((t for t in self._row_tasks if t.id == tid), None)
        return None

    def _on_list_click(self, event) -> None:
        item = self.list_canvas.find_withtag("current")
        iid = item[0] if item else None
        tag = self._tag_of(iid)
        task = self._task_from_item(iid)
        self._drag_start = (event.x, event.y)
        self._press_task = task
        if task is None:
            return
        if tag.startswith("act:edit:"):
            self.app.open_editor(task)
            self._press_task = None
        elif tag.startswith("act:del:"):
            self.app.delete_task(task)
            self._press_task = None
        elif tag.startswith("chk:"):
            self.app.toggle_task(task)
            self._press_task = None

    def _on_list_double(self, event) -> None:
        item = self.list_canvas.find_withtag("current")
        task = self._task_from_item(item[0] if item else None)
        if task and not self._tag_of(item[0] if item else None).startswith("chk:"):
            self.app.open_editor(task)

    def _on_list_drag(self, event) -> None:
        if self._press_task is None or self._drag_start is None:
            return
        dx = abs(event.x - self._drag_start[0])
        dy = abs(event.y - self._drag_start[1])
        if not self._dragging and dy > self.px(5) and dy > dx:
            self._dragging = True
            self._drag_task = self._press_task
            self.list_canvas.configure(cursor="fleur")
        if self._dragging:
            self._on_drag_motion(event)

    def _on_drag_motion(self, event) -> None:
        c = self.list_canvas
        c.delete("drop")
        y = c.canvasy(event.y)
        target = None
        for tid, (ry, rh) in self.row_pos.items():
            if ry <= y <= ry + rh:
                target = tid
                break
        if target is None:
            for tid, (ry, rh) in sorted(self.row_pos.items(), key=lambda kv: kv[1][0]):
                if y < ry:
                    target = tid
                    break
        self._drop_target = target
        if target and self._drag_task and target != self._drag_task.id:
            ry, rh = self.row_pos[target]
            line = self.theme.round_rect(c.winfo_width() - self.px(16), self.px(2), self.px(1),
                                            self.theme.pal.accent)
            self._drop_img = line
            c.create_image(self.px(8), ry, image=line, anchor="nw", tags=("drop",))

    def _on_list_release(self, event) -> None:
        was_dragging = self._dragging
        target_id = getattr(self, "_drop_target", None)
        self.list_canvas.delete("drop")
        self.list_canvas.configure(cursor="")
        self._dragging = False
        task, self._drag_task = self._drag_task, None
        press, self._press_task = self._press_task, None
        self._drag_start = None
        if was_dragging and task and target_id and target_id != task.id:
            target = next((t for t in self._row_tasks if t.id == target_id), None)
            if target:
                self.store.reorder(task.id, target)
        elif press and not was_dragging and not self._tag_of(
                self.list_canvas.find_withtag("current")[0]
                if self.list_canvas.find_withtag("current") else None).startswith("act:"):
            # 单击整行 -> 打开编辑
            self.app.open_editor(press)

    def _on_list_right_click(self, event) -> None:
        item = self.list_canvas.find_withtag("current")
        task = self._task_from_item(item[0] if item else None)
        if task:
            self.app.show_context_menu(task, event.x_root, event.y_root)

    # ---- 背景（标签栏 / 空白区域）----
    def _on_bg_click(self, event) -> None:
        item = self.canvas.find_withtag("current")
        tags = self.canvas.gettags(item[0]) if item else ()
        for tag in tags:
            if tag.startswith("tab:"):
                self.view = tag[4:]
                self.cfg["last_view"] = self.view
                self.cfg.save()
                self.list_canvas.yview_moveto(0)
                self._draw_tabs()
                self.refresh_list()
                self._draw_footer()
                return
        self.app.on_background_click()

    # ---- 拖动窗口（绑在顶栏上）----
    def _start_window_drag(self, event) -> None:
        self._win_drag_origin = (event.x_root - self.root.winfo_x(),
                                 event.y_root - self.root.winfo_y())
        self._window_dragging = True

    def _do_window_drag(self, event) -> None:
        if not self._window_dragging or not self._win_drag_origin:
            return
        ox, oy = self._win_drag_origin
        self.app.move_window(event.x_root - ox, event.y_root - oy)

    def _end_window_drag(self, _event) -> None:
        if self._window_dragging:
            self._window_dragging = False
            self.app.on_window_moved()

    # ================= 对外 =================
    def set_view(self, view: str) -> None:
        if view in [v for v, _ in VIEWS]:
            self.view = view

    def apply_theme(self) -> None:
        self.redraw_all()

    def refresh(self) -> None:
        """数据变化后整体刷新（列表 + 底栏 + 徽标）。"""
        self._draw_shell()
        self.refresh_list()
        self._draw_footer()
        self._refresh_preview()
