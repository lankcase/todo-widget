"""界面通用件：字体集、文本截断、悬停绑定、自适应高度的画布列表。"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, Optional


class FontSet:
    """统一管理字体对象。用 Font 对象而不是元组，方便测量文本宽度做截断。"""

    def __init__(self, root: tk.Misc, theme):
        self.theme = theme
        self.title = tkfont.Font(root=root, **self._spec(10.5, "bold"))
        self.title_done = tkfont.Font(root=root, **{**self._spec(10.5), "overstrike": 1})
        self.body = tkfont.Font(root=root, **self._spec(10))
        self.small = tkfont.Font(root=root, **self._spec(8.5))
        self.tiny = tkfont.Font(root=root, **self._spec(7.5))
        self.h1 = tkfont.Font(root=root, **self._spec(12, "bold"))
        self.mono = tkfont.Font(root=root, **self._spec(9))

    def _spec(self, size: float, weight: str = "normal", family: str = "body") -> dict:
        t = self.theme
        fam = getattr(t, f"f_{family}", "TkDefaultFont") if t.fonts_resolved else "TkDefaultFont"
        # 正数 = 点，Tk 会按屏幕 DPI 自动缩放，不需要我们手工乘
        return {"family": fam, "size": max(7, int(round(size))), "weight": weight}

    def scaled(self, base: tkfont.Font, size: float) -> tkfont.Font:
        return base


def ellipsize(text: str, font: tkfont.Font, max_width: int) -> str:
    """按像素宽度截断文本，超出部分用省略号。"""
    if not text:
        return ""
    if font.measure(text) <= max_width:
        return text
    ell = "…"
    ell_w = font.measure(ell)
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid]) + ell_w <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ell if lo > 0 else ell


def wrap_two_lines(text: str, font: tkfont.Font, max_width: int) -> list[str]:
    """把长标题折成最多两行，第二行以省略号结尾。"""
    if font.measure(text) <= max_width:
        return [text]
    # 按字符贪心折行（中文没有空格，逐字折最稳）
    first = ellipsize(text, font, max_width)
    if first.endswith("…"):
        cut = len(first) - 1
        rest = text[cut:]
        return [first[:-1], ellipsize(rest, font, max_width)]
    return [first]


def bind_hover(widget: tk.Misc, enter: Callable[[], None], leave: Callable[[], None]) -> None:
    widget.bind("<Enter>", lambda _e: enter(), add="+")
    widget.bind("<Leave>", lambda _e: leave(), add="+")


class SmoothList:
    """列表区域：一个独立 Canvas，自带平滑滚动与裁剪。

    行内容全部画在 Canvas 上（而不是每行一个控件），几百条任务也依然流畅。
    """

    def __init__(self, parent: tk.Misc, bg: str, on_scroll: Optional[Callable[[], None]] = None):
        self.canvas = tk.Canvas(parent, bg=bg, highlightthickness=0, bd=0, takefocus=0)
        self.canvas.configure(yscrollincrement=4)
        self._target = 0.0
        self._animating = False
        self.on_scroll = on_scroll

    def pack(self, **kwargs) -> None:
        self.canvas.pack(**kwargs)

    # ---- 滚动 ----
    def bind_wheel(self) -> None:
        self.canvas.bind("<MouseWheel>", self._on_wheel, add="+")
        self.canvas.bind("<Button-4>", lambda e: self.scroll_by(-3), add="+")
        self.canvas.bind("<Button-5>", lambda e: self.scroll_by(3), add="+")

    def _on_wheel(self, event) -> str:
        delta = event.delta
        step = -(delta // 120) * 3
        self.scroll_by(step)
        return "break"

    def scroll_by(self, units: int) -> None:
        self.canvas.yview_scroll(units, "units")

    def reset(self) -> None:
        self.canvas.yview_moveto(0)

    def clear(self) -> None:
        self.canvas.delete("all")

    def configure_scrollregion(self, height: int) -> None:
        self.canvas.configure(scrollregion=(0, 0, 0, max(height, 1)))

    @property
    def width(self) -> int:
        return self.canvas.winfo_width()

    @property
    def height(self) -> int:
        return self.canvas.winfo_height()


def draw_text(canvas: tk.Canvas, x: int, y: int, text: str, *,
              font, fill: str, anchor: str = "nw", tags=(), width: Optional[int] = None):
    return canvas.create_text(x, y, text=text, font=font, fill=fill,
                              anchor=anchor, tags=tags, width=width)


def rounded_pill(canvas: tk.Canvas, theme, x: int, y: int, text: str, font,
                 fg: str, bg: str, pad_x: int = 8, height: int = 18, tags=()):
    """画一个小圆角色块 + 文字，返回 (x2, item_ids)。"""
    w = font.measure(text) + pad_x * 2
    img = theme.round_rect(w, height, height // 2, bg)
    iid = canvas.create_image(x, y, image=img, anchor="nw", tags=tags)
    tid = canvas.create_text(x + w // 2, y + height // 2, text=text, font=font,
                             fill=fg, anchor="center", tags=tags)
    canvas._pill_images = getattr(canvas, "_pill_images", {})
    canvas._pill_images[(x, y, text)] = img   # 防止被垃圾回收
    return x + w, [iid, tid]


class ChipGroup(tk.Canvas):
    """一排可点选的圆角标签（优先级、重复、分类都用它）。支持自动换行。"""

    def __init__(self, parent: tk.Misc, theme, fonts: "FontSet", options: list,
                 *, value=None, command: Optional[Callable[[str], None]] = None,
                 bg: Optional[str] = None, chip_h: float = 26, gap: float = 6,
                 pad_x: float = 12):
        self.theme = theme
        self.fonts = fonts
        self.options = options           # [(key, label, color|None), ...]
        self.value = value
        self.command = command
        self.bg = bg or theme.pal.card
        self.chip_h = theme.px(chip_h)
        self.gap = theme.px(gap)
        self.pad_x = theme.px(pad_x)
        self._layout: list[tuple[str, int, int, int]] = []
        super().__init__(parent, bg=self.bg, highlightthickness=0, bd=0, takefocus=0)
        self._images: dict[int, tk.PhotoImage] = {}
        self.bind("<Button-1>", self._on_click)
        self.bind("<Configure>", lambda _e: self.redraw())
        self.redraw()

    def _measure(self, label: str) -> int:
        return self.fonts.small.measure(label) + self.pad_x * 2

    def preferred_height(self, width: int) -> int:
        if width <= 1:
            return self.chip_h
        x = 0
        rows = 1
        for _key, label, _c in self.options:
            w = self._measure(label)
            if x + w > width and x > 0:
                rows += 1
                x = 0
            x += w + self.gap
        return rows * self.chip_h + (rows - 1) * self.gap

    def redraw(self) -> None:
        self.delete("all")
        self._images.clear()
        width = self.winfo_width() or 260
        x = y = 0
        self._layout.clear()
        for key, label, color in self.options:
            w = self._measure(label)
            if x + w > width and x > 0:
                x = 0
                y += self.chip_h + self.gap
            selected = (key == self.value)
            if selected:
                fill = color or self.theme.pal.accent
                fg = "#FFFFFF"
            else:
                fill = self.theme.pal.input_bg
                fg = self.theme.pal.text_dim
            img = self.theme.round_rect(w, self.chip_h, self.chip_h // 2, fill)
            self._images[len(self._layout)] = img
            self.create_image(x, y, image=img, anchor="nw")
            self.create_text(x + w // 2, y + self.chip_h // 2, text=label,
                             font=self.fonts.small, fill=fg, anchor="center")
            self._layout.append((key, x, y, w))
            x += w + self.gap
        self.configure(height=y + self.chip_h)

    def _on_click(self, event) -> None:
        for key, x, y, w in self._layout:
            if x <= event.x <= x + w and y <= event.y <= y + self.chip_h:
                self.value = key
                self.redraw()
                if self.command:
                    self.command(key)
                return

    def set_value(self, value) -> None:
        self.value = value
        self.redraw()

    def set_options(self, options: list, value=None) -> None:
        self.options = options
        self.value = value
        self.redraw()


class CalendarGrid(tk.Canvas):
    """内联月历。点日期回调，不用弹出窗口，省掉一堆失焦关闭的边界问题。"""

    WEEK = "一二三四五六日"

    def __init__(self, parent: tk.Misc, theme, fonts: "FontSet",
                 on_pick: Callable, bg: Optional[str] = None):
        self.theme = theme
        self.fonts = fonts
        self.on_pick = on_pick
        self.bg = bg or theme.pal.card
        super().__init__(parent, bg=self.bg, highlightthickness=0, bd=0, takefocus=0)
        self._images: dict = {}
        self._cells: list[tuple[int, int, int]] = []   # (x, y, day)
        self._view = None                              # (year, month)
        self._selected = None                          # date
        self.bind("<Button-1>", self._on_click)
        self.goto_today()

    def goto_today(self) -> None:
        import datetime
        t = datetime.date.today()
        self._view = (t.year, t.month)
        self.redraw()

    def set_selected(self, date_obj) -> None:
        if date_obj:
            self._selected = date_obj
            self._view = (date_obj.year, date_obj.month)
        else:
            self._selected = None
        self.redraw()

    def redraw(self) -> None:
        import calendar
        import datetime
        self.delete("all")
        self._images.clear()
        self._cells.clear()
        pal, th = self.theme.pal, self.theme
        if not self._view:
            self.goto_today()
        year, month = self._view
        pad = th.px(8)
        cell_w = th.px(32)
        cell_h = th.px(28)
        width = cell_w * 7 + pad * 2
        # 高度按实际内容算：标题行 + 星期行 + 6 周
        self.configure(width=width, height=th.px(12 + 20 + 16 + 6 * 28 + 6))

        # 顶部：< 2026年10月 >
        hy = th.px(12)
        self.create_text(width // 2, hy, text=f"{year} 年 {month} 月", anchor="center",
                         fill=pal.text, font=self.fonts.small)
        for label, dx in (("‹", pad + th.px(6)), ("›", width - pad - th.px(6))):
            self.create_text(dx, hy, text=label, anchor="center", fill=pal.text_dim,
                             font=self.fonts.body, tags=(f"nav:{label}",))

        # 星期表头
        wy = hy + th.px(20)
        for i, wd in enumerate(self.WEEK):
            fill = pal.danger if i >= 5 else pal.text_faint
            self.create_text(pad + cell_w * i + cell_w // 2, wy, text=wd, anchor="center",
                             fill=fill, font=self.fonts.tiny)

        first = datetime.date(year, month, 1)
        start = first.weekday()
        days_in_month = calendar.monthrange(year, month)[1]
        today = datetime.date.today()
        gy = wy + th.px(16)
        for day in range(1, days_in_month + 1):
            idx = start + day - 1
            col, row = idx % 7, idx // 7
            if row >= 6:
                break
            cx = pad + col * cell_w
            cy = gy + row * cell_h
            date_obj = datetime.date(year, month, day)
            selected = (self._selected == date_obj)
            if selected or date_obj == today:
                fill = pal.accent if selected else pal.accent_soft
                img = th.round_rect(cell_w - th.px(4), cell_h - th.px(4), th.px(7), fill)
                self._images[day] = img
                self.create_image(cx + th.px(2), cy + th.px(2), image=img, anchor="nw")
            fg = "#FFFFFF" if selected else (
                pal.accent if date_obj == today else
                (pal.danger if col >= 5 else pal.text))
            self.create_text(cx + cell_w // 2, cy + cell_h // 2, text=str(day),
                             anchor="center", fill=fg, font=self.fonts.small)
            self._cells.append((cx, cy, day))

    def _on_click(self, event) -> None:
        import datetime
        for tag in self.find_withtag("current"):
            tags = self.gettags(tag)
            for t in tags:
                if t.startswith("nav:"):
                    y, m = self._view
                    if t.endswith("‹"):
                        m -= 1
                        if m == 0:
                            y, m = y - 1, 12
                    else:
                        m += 1
                        if m == 13:
                            y, m = y + 1, 1
                    self._view = (y, m)
                    self.redraw()
                    return
        for x, y, day in self._cells:
            if x <= event.x <= x + self.theme.px(32) and y <= event.y <= y + self.theme.px(28):
                self._selected = datetime.date(self._view[0], self._view[1], day)
                self.redraw()
                self.on_pick(self._selected)
                return


class CardWindow(tk.Toplevel):
    """无边框圆角卡片窗口的公共部分：贴桌面/置顶都可以用。

    用法：先往 self.body 里 pack 控件，最后调 finalize() 计算尺寸并显示。
    """

    PAD = 16

    def __init__(self, app_or_root, theme, fonts: "FontSet", *, width: float = 340,
                 topmost: bool = True, draggable: bool = True, pad: int = 16):
        root = getattr(app_or_root, "root", app_or_root)
        super().__init__(root)
        self.theme = theme
        self.fonts = fonts
        self.pad = theme.px(pad)
        self.width_logical = width
        self.withdraw()
        self.overrideredirect(True)
        self.configure(bg=theme.pal.key)
        try:
            self.wm_attributes("-transparentcolor", theme.pal.key)
        except Exception:
            pass
        if topmost:
            self.attributes("-topmost", True)
        self.bg = tk.Canvas(self, bg=theme.pal.key, highlightthickness=0, bd=0)
        self.body = tk.Frame(self, bg=theme.pal.card)
        self.bg.place(x=0, y=0, relwidth=1, relheight=1)
        self.body.place(x=self.pad, y=self.pad)
        if draggable:
            self._bind_drag()
        self.bind("<Escape>", lambda _e: self.on_escape() if hasattr(self, "on_escape") else self.destroy())

    def _bind_drag(self) -> None:
        self.body.bind("<Button-1>", self._drag_start, add="+")
        self.body.bind("<B1-Motion>", self._drag_move, add="+")

    def _drag_start(self, event) -> None:
        self._drag_origin = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _drag_move(self, event) -> None:
        if getattr(self, "_drag_origin", None):
            ox, oy = self._drag_origin
            self.geometry(f"+{event.x_root - ox}+{event.y_root - oy}")

    def finalize(self, *, position: str = "center", margin: int = 24) -> None:
        self.update_idletasks()
        w = max(self.theme.px(self.width_logical), self.body.winfo_reqwidth()) + self.pad * 2
        h = self.body.winfo_reqheight() + self.pad * 2
        img = self.theme.card(w, h, self.theme.px(14))
        self._card = img
        self.bg.delete("all")
        self.bg.create_image(0, 0, image=img, anchor="nw")
        self.geometry(f"{w}x{h}{self._placement(position, w, h, margin)}")
        self.deiconify()
        self.lift()
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass

    def _placement(self, position: str, w: int, h: int, margin: int) -> str:
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        if position == "bottom-right":
            return f"+{sw - w - margin}+{sh - h - margin - self.theme.px(44)}"
        if position == "center":
            return f"+{(sw - w) // 2}+{int((sh - h) * 0.28)}"
        return "+0+0"


class Switch(tk.Canvas):
    """开关（圆角轨道 + 白色旋钮）。Tk 没有原生开关控件，自绘一个。"""

    def __init__(self, parent: tk.Misc, theme, value: bool = False,
                 command: Optional[Callable[[bool], None]] = None,
                 bg: Optional[str] = None, w: float = 40, h: float = 22):
        self.theme = theme
        self.value = bool(value)
        self.command = command
        self.bg = bg or theme.pal.card
        self._w, self._h = theme.px(w), theme.px(h)
        super().__init__(parent, width=self._w, height=self._h, bg=self.bg,
                         highlightthickness=0, bd=0, takefocus=0, cursor="hand2")
        self._images: dict = {}
        self.bind("<Button-1>", lambda _e: self.toggle())
        self.redraw()

    def toggle(self) -> None:
        self.set(not self.value)

    def set(self, value: bool, fire: bool = True) -> None:
        self.value = bool(value)
        self.redraw()
        if fire and self.command:
            self.command(self.value)

    def redraw(self) -> None:
        th, pal = self.theme, self.theme.pal
        self.delete("all")
        self._images.clear()
        track_color = pal.accent if self.value else pal.track
        track = th.round_rect(self._w, self._h, self._h // 2, track_color)
        self._images["t"] = track
        self.create_image(0, 0, image=track, anchor="nw")
        pad = th.px(2)
        knob_size = self._h - pad * 2
        knob = th.dot(knob_size, "#FFFFFF" if self.value else pal.text_faint, keyed=True)
        self._images["k"] = knob
        x = self._w - pad - knob_size if self.value else pad
        self.create_image(x, pad, image=knob, anchor="nw")


class Tooltip:
    """轻量提示气泡。鼠标悬停一段时间后在指针下方显示一行小字。"""

    _active: Optional["Tooltip"] = None

    def __init__(self, widget: tk.Misc, theme, text: str, delay: int = 450):
        self.widget = widget
        self.theme = theme
        self.text = text
        self.delay = delay
        self.tip: Optional[tk.Toplevel] = None
        self._after: Optional[str] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Button-1>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None

    def _show(self) -> None:
        if self.tip or not self.text:
            return
        if Tooltip._active:
            Tooltip._active._hide()
        Tooltip._active = self
        t = tk.Toplevel(self.widget)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        th = self.theme
        frame = tk.Frame(t, bg=th.pal.text, padx=0, pady=0)
        frame.pack()
        tk.Label(frame, text=self.text, bg=th.pal.text, fg=th.pal.card,
                 font=(getattr(th, "f_body", "TkDefaultFont"), 8),
                 padx=th.px(8), pady=th.px(4)).pack()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + th.px(6)
        t.geometry(f"+{x - th.px(10)}+{y}")
        self.tip = t

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None
        if Tooltip._active is self:
            Tooltip._active = None


class IconButton(tk.Canvas):
    """画布上的小图标按钮，带圆角悬停底色。"""

    def __init__(self, parent: tk.Misc, theme, name: str, *, size: int = 16,
                 command: Optional[Callable[[], None]] = None,
                 bg: Optional[str] = None, color: Optional[str] = None,
                 hover_color: Optional[str] = None, pad: int = 5,
                 tooltip: str = ""):
        self.theme = theme
        self.bg = bg or theme.pal.card
        self.color = color or theme.pal.text_dim
        self.hover_color = hover_color or theme.pal.text
        w = theme.px(size + pad * 2)
        super().__init__(parent, width=w, height=w, bg=self.bg,
                         highlightthickness=0, bd=0, takefocus=0,
                         cursor="hand2")
        self._size = theme.px(size)
        self._pad = theme.px(pad)
        self._bg_item = None
        size = theme.px(size)
        pad = theme.px(pad)
        self._icon_idle = theme.icon(name, size, self.color)
        self._icon_hover = theme.icon(name, size, self.hover_color)
        self._id = self.create_image(self._pad, self._pad, image=self._icon_idle, anchor="nw")
        self._hover_bg = theme.round_rect(w, w, theme.px(6), theme.pal.card_hover)
        if command:
            self.bind("<Button-1>", lambda _e: command())
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self._tooltip = Tooltip(self, theme, tooltip) if tooltip else None

    def _on_enter(self, _e=None) -> None:
        if self._bg_item is None:
            self._bg_item = self.create_image(0, 0, image=self._hover_bg, anchor="nw")
            self.tag_lower(self._bg_item)
        self.itemconfig(self._id, image=self._icon_hover)

    def _on_leave(self, _e=None) -> None:
        if self._bg_item is not None:
            self.delete(self._bg_item)
            self._bg_item = None
        self.itemconfig(self._id, image=self._icon_idle)

