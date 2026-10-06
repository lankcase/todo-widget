"""主题：配色、字体、以及用 Pillow 超采样预渲染的圆角/圆形素材。

tkinter 的 Canvas 没有抗锯齿，直接画圆角会有明显锯齿。这里的做法是把圆角卡片、
复选框圆圈等用 Pillow 以 4 倍分辨率绘制再缩小，得到平滑边缘，然后缓存成 PhotoImage。

圆角透明原理：窗口用 `-transparentcolor` 指定一个"透明键色"，
透明键色特意选成和卡片底色极接近的颜色，这样圆角边缘的抗锯齿过渡不会被看出色边。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from PIL import Image, ImageDraw, ImageTk

SS = 4  # 超采样倍数


@dataclass(frozen=True)
class Palette:
    name: str
    card: str            # 卡片底色
    key: str             # 透明键色（必须与 card 极接近，且 UI 里绝不会用到）
    card_alt: str        # 次级底色（悬停/hover）
    card_hover: str
    border: str
    text: str
    text_dim: str
    text_faint: str
    accent: str
    accent_soft: str
    danger: str
    p_high: str
    p_mid: str
    p_low: str
    p_none: str
    done_text: str
    track: str           # 进度条底槽
    input_bg: str


LIGHT = Palette(
    name="light",
    card="#FFFFFF",
    key="#FDFDFE",
    card_alt="#FAFAFB",
    card_hover="#F3F4F6",
    border="#E8EAED",
    text="#1F2328",
    text_dim="#5F6672",
    text_faint="#9AA1AC",
    accent="#3B82F6",
    accent_soft="#E8F0FE",
    danger="#EF4444",
    p_high="#EF4444",
    p_mid="#F59E0B",
    p_low="#3B82F6",
    p_none="#C9CED6",
    done_text="#A8AEB8",
    track="#EDEFF2",
    input_bg="#F5F6F8",
)

DARK = Palette(
    name="dark",
    card="#22252A",
    key="#23262B",
    card_alt="#282C32",
    card_hover="#2E333A",
    border="#353A42",
    text="#E9EBEF",
    text_dim="#A2A9B4",
    text_faint="#767D88",
    accent="#60A5FA",
    accent_soft="#2B3A52",
    danger="#F87171",
    p_high="#F87171",
    p_mid="#FBBF24",
    p_low="#60A5FA",
    p_none="#5A616B",
    done_text="#6B7280",
    track="#2E333A",
    input_bg="#2A2E34",
)

PRIORITY_COLORS_LIGHT = {3: "#EF4444", 2: "#F59E0B", 1: "#3B82F6", 0: "#C9CED6"}
PRIORITY_COLORS_DARK = {3: "#F87171", 2: "#FBBF24", 1: "#60A5FA", 0: "#5A616B"}


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def blend(c1: str, c2: str, t: float) -> str:
    """在两个颜色之间插值，t=0 返回 c1，t=1 返回 c2。"""
    a, b = _hex_to_rgb(c1), _hex_to_rgb(c2)
    out = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return "#%02X%02X%02X" % out


class Theme:
    """配色 + 素材缓存。必须在 Tk 根窗口创建之后再实例化。

    尺寸约定：这里所有绘制方法的宽高/半径/字号都收**物理像素**，
    逻辑尺寸请调用方自己用 theme.px() 换算好再传进来。
    这样就不会出现"调用方换了算、Theme 又换一次算"的双重缩放。
    """

    def __init__(self, name: str = "light", scale: float = 1.0):
        self.scale = scale
        self._cache: dict[tuple, ImageTk.PhotoImage] = {}
        self.fonts_resolved = False
        self.set_palette(name)

    # ---------------- 配色 ----------------
    def set_palette(self, name: str) -> None:
        self.pal = DARK if name == "dark" else LIGHT
        self.priority_colors = PRIORITY_COLORS_DARK if name == "dark" else PRIORITY_COLORS_LIGHT
        self._cache.clear()

    @property
    def is_dark(self) -> bool:
        return self.pal.name == "dark"

    def px(self, value: float) -> int:
        """逻辑像素 -> 物理像素。"""
        return max(1, int(round(value * self.scale)))

    # ---------------- 字体 ----------------
    def resolve_fonts(self, available: set[str]) -> None:
        """从系统已装字体里挑一套顺眼的中英文字体。"""
        def pick(candidates: list[str], fallback: str) -> str:
            for c in candidates:
                if c in available:
                    return c
            return fallback

        self.f_title = pick(["Microsoft YaHei UI", "微软雅黑", "Microsoft YaHei UI Light",
                             "Segoe UI Variable Display", "Segoe UI"], "TkDefaultFont")
        self.f_body = pick(["Microsoft YaHei UI", "微软雅黑", "Segoe UI"], "TkDefaultFont")
        self.f_num = pick(["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"], "TkDefaultFont")
        self.f_icon = pick(["Segoe UI Symbol", "Segoe UI Emoji", "Microsoft YaHei UI"], "TkDefaultFont")
        self.fonts_resolved = True

    def font(self, size: float, weight: str = "normal", family: str = "body") -> tuple:
        fam = getattr(self, f"f_{family}", "TkDefaultFont") if self.fonts_resolved else "TkDefaultFont"
        return (fam, max(7, int(round(size * self.scale))), weight)

    # ---------------- 素材 ----------------
    def _cached(self, key: tuple, builder) -> ImageTk.PhotoImage:
        img = self._cache.get(key)
        if img is None:
            img = ImageTk.PhotoImage(builder())
            self._cache[key] = img
        return img

    def card(self, w: float, h: float, radius: float = 12, *,
             fill: Optional[str] = None, outline: Optional[str] = None,
             outline_width: float = 1.0) -> ImageTk.PhotoImage:
        """圆角矩形（抗锯齿）。四角用透明键色，配合窗口的 -transparentcolor 实现真圆角。"""
        w, h = int(w), int(h)
        radius = int(radius)
        fill = fill or self.pal.card
        key = (self.pal.name, "card", w, h, radius, fill, outline, outline_width)
        if outline is not None:
            outline = outline
        else:
            outline = self.pal.border

        def build() -> Image.Image:
            img = Image.new("RGB", (w * SS, h * SS), _hex_to_rgb(self.pal.key))
            d = ImageDraw.Draw(img)
            ow = max(1, int(outline_width)) * SS
            d.rounded_rectangle(
                (0, 0, w * SS - 1, h * SS - 1),
                radius=radius * SS,
                fill=_hex_to_rgb(fill),
                outline=_hex_to_rgb(outline) if ow else None,
                width=ow,
            )
            return img.resize((w, h), Image.LANCZOS)

        return self._cached(key, build)

    def round_rect(self, w: float, h: float, radius: float, fill: str, *,
                   keyed: bool = False, outline: Optional[str] = None) -> ImageTk.PhotoImage:
        """任意圆角色块（小标签、进度条等）。keyed=True 时四角透明。"""
        w, h = int(w), int(h)
        radius = int(radius)
        cache_key = (self.pal.name, "rr", w, h, radius, fill, keyed, outline)
        base = self.pal.key if keyed else fill

        def build() -> Image.Image:
            img = Image.new("RGB", (w * SS, h * SS), _hex_to_rgb(base))
            d = ImageDraw.Draw(img)
            ow = max(1, int(self.scale)) * SS if outline else 0
            d.rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=radius * SS,
                                fill=_hex_to_rgb(fill),
                                outline=_hex_to_rgb(outline) if outline else None, width=ow)
            return img.resize((w, h), Image.LANCZOS)

        return self._cached(cache_key, build)

    def checkbox(self, size: float, state: str = "empty") -> ImageTk.PhotoImage:
        """复选框：empty / hover / checked / checked_hover / partial"""
        size = int(size)
        cache_key = (self.pal.name, "cb", size, state)

        def build() -> Image.Image:
            s = size * SS
            img = Image.new("RGB", (s, s), _hex_to_rgb(self.pal.card))
            d = ImageDraw.Draw(img)
            ring = max(1, int(round(1.4 * self.scale)))
            if state in ("checked", "checked_hover"):
                fill = self.pal.accent if state == "checked" else blend(self.pal.accent, "#000000", 0.12)
                d.ellipse((0, 0, s - 1, s - 1), fill=_hex_to_rgb(fill))
                # 白色对勾
                w = max(2, int(round(1.8 * self.scale)))
                pts = [(0.28 * s, 0.52 * s), (0.44 * s, 0.68 * s), (0.73 * s, 0.34 * s)]
                d.line(pts, fill=(255, 255, 255), width=int(w * SS), joint="curve")
            elif state == "partial":
                d.ellipse((0, 0, s - 1, s - 1), outline=_hex_to_rgb(self.pal.accent), width=int(ring * SS))
                pad = 0.3 * s
                d.ellipse((pad, pad, s - pad, s - pad), fill=_hex_to_rgb(self.pal.accent))
            else:
                color = self.pal.text_faint if state == "empty" else self.pal.accent
                d.ellipse((0, 0, s - 1, s - 1), outline=_hex_to_rgb(color), width=int(ring * SS))
            return img.resize((size, size), Image.LANCZOS)

        return self._cached(cache_key, build)

    def dot(self, size: float, color: str, keyed: bool = False) -> ImageTk.PhotoImage:
        """实心圆。keyed=True 时圆外是透明键色（用于叠在别的色块上面，比如开关旋钮）。"""
        size = int(size)
        cache_key = (self.pal.name, "dot", size, color, keyed)

        def build() -> Image.Image:
            s = size * SS
            img = Image.new("RGB", (s, s), _hex_to_rgb(self.pal.key if keyed else self.pal.card))
            d = ImageDraw.Draw(img)
            d.ellipse((0, 0, s - 1, s - 1), fill=_hex_to_rgb(color))
            return img.resize((size, size), Image.LANCZOS)

        return self._cached(cache_key, build)

    def bar(self, w: float, h: float, color: str) -> ImageTk.PhotoImage:
        """圆头竖条（优先级色条）。"""
        w = max(2, int(w))
        h = int(h)
        cache_key = (self.pal.name, "bar", w, h, color)

        def build() -> Image.Image:
            img = Image.new("RGB", (w * SS, h * SS), _hex_to_rgb(self.pal.card))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=(w * SS) // 2,
                                fill=_hex_to_rgb(color))
            return img.resize((w, h), Image.LANCZOS)

        return self._cached(cache_key, build)

    def progress(self, w: float, h: float, ratio: float) -> ImageTk.PhotoImage:
        """进度条（底槽 + 已完成部分）。"""
        w, h = int(w), int(h)
        h = max(3, h)
        ratio = max(0.0, min(1.0, ratio))
        cache_key = (self.pal.name, "prog", w, h, round(ratio, 3))

        def build() -> Image.Image:
            img = Image.new("RGB", (w * SS, h * SS), _hex_to_rgb(self.pal.track))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=(h * SS) // 2,
                                fill=_hex_to_rgb(self.pal.track))
            if ratio > 0:
                fw = max(h, int(w * SS * ratio))
                d.rounded_rectangle((0, 0, fw - 1, h * SS - 1), radius=(h * SS) // 2,
                                    fill=_hex_to_rgb(self.pal.accent))
            return img.resize((w, h), Image.LANCZOS)

        return self._cached(cache_key, build)

    def icon(self, name: str, size: float, color: str) -> ImageTk.PhotoImage:
        """极简线性图标，用 Pillow 画，保证在高分屏上也是平滑的。"""
        size = int(size)
        cache_key = (self.pal.name, "icon", name, size, color)
        rgb = _hex_to_rgb(color)

        def build() -> Image.Image:
            s = size * SS
            img = Image.new("RGB", (s, s), _hex_to_rgb(self.pal.card))
            d = ImageDraw.Draw(img)
            lw = max(2, int(round(1.5 * self.scale * SS)))
            m = 0.16 * s  # 内边距

            def line(pts, width=lw, cap="round"):
                d.line([(x * s, y * s) for x, y in pts], fill=rgb, width=width, joint=cap)

            if name == "mic":
                d.rounded_rectangle((0.36 * s, 0.16 * s, 0.64 * s, 0.58 * s),
                                    radius=0.14 * s, outline=rgb, width=lw)
                d.arc((0.22 * s, 0.34 * s, 0.78 * s, 0.78 * s), start=0, end=180, fill=rgb, width=lw)
                line([(0.5, 0.76), (0.5, 0.88)])
            elif name == "gear":
                import math
                d.ellipse((0.3 * s, 0.3 * s, 0.7 * s, 0.7 * s), outline=rgb, width=lw)
                for i in range(8):
                    a = i * math.pi / 4
                    x1, y1 = 0.5 * s + math.cos(a) * 0.32 * s, 0.5 * s + math.sin(a) * 0.32 * s
                    x2, y2 = 0.5 * s + math.cos(a) * 0.45 * s, 0.5 * s + math.sin(a) * 0.45 * s
                    d.line([(x1, y1), (x2, y2)], fill=rgb, width=lw)
            elif name == "plus":
                line([(0.5, 0.24), (0.5, 0.76)])
                line([(0.24, 0.5), (0.76, 0.5)])
            elif name == "close":
                line([(0.28, 0.28), (0.72, 0.72)])
                line([(0.72, 0.28), (0.28, 0.72)])
            elif name == "min":
                line([(0.26, 0.5), (0.74, 0.5)])
            elif name == "pin":
                # 图钉：斜着的一根针 + 针帽 + 底下的横线
                d.ellipse((0.3 * s, 0.14 * s, 0.7 * s, 0.4 * s), outline=rgb, width=lw)
                line([(0.5, 0.4), (0.5, 0.66)])
                line([(0.3, 0.68), (0.7, 0.68)])
                line([(0.5, 0.68), (0.5, 0.86)])
            elif name == "desktop":
                d.rounded_rectangle((0.16 * s, 0.2 * s, 0.84 * s, 0.66 * s),
                                    radius=0.08 * s, outline=rgb, width=lw)
                line([(0.36, 0.82), (0.64, 0.82)])
                line([(0.5, 0.66), (0.5, 0.82)])
            elif name == "bell":
                d.arc((0.26 * s, 0.18 * s, 0.74 * s, 0.7 * s), start=180, end=360, fill=rgb, width=lw)
                line([(0.26, 0.44), (0.26, 0.68)])
                line([(0.74, 0.44), (0.74, 0.68)])
                line([(0.2, 0.68), (0.8, 0.68)])
                d.arc((0.42 * s, 0.66 * s, 0.58 * s, 0.84 * s), start=0, end=180, fill=rgb, width=lw)
            elif name == "clock":
                d.ellipse((m, m, s - m, s - m), outline=rgb, width=lw)
                line([(0.5, 0.3), (0.5, 0.52), (0.68, 0.62)])
            elif name == "flag":
                line([(0.3, 0.16), (0.3, 0.84)])
                d.polygon([(0.3 * s, 0.2 * s), (0.78 * s, 0.33 * s), (0.3 * s, 0.5 * s)], fill=rgb)
            elif name == "search":
                d.ellipse((0.16 * s, 0.16 * s, 0.68 * s, 0.68 * s), outline=rgb, width=lw)
                line([(0.64, 0.64), (0.84, 0.84)])
            elif name == "repeat":
                d.arc((0.2 * s, 0.2 * s, 0.8 * s, 0.8 * s), start=30, end=330, fill=rgb, width=lw)
                d.polygon([(0.78 * s, 0.1 * s), (0.9 * s, 0.3 * s), (0.66 * s, 0.3 * s)], fill=rgb)
            elif name == "trash":
                line([(0.22, 0.3), (0.78, 0.3)])
                d.rounded_rectangle((0.3 * s, 0.3 * s, 0.7 * s, 0.84 * s),
                                    radius=0.06 * s, outline=rgb, width=lw)
                line([(0.42, 0.2), (0.58, 0.2)])
            elif name == "edit":
                line([(0.24, 0.76), (0.7, 0.3)])
                d.polygon([(0.66 * s, 0.18 * s), (0.82 * s, 0.34 * s), (0.74 * s, 0.42 * s),
                           (0.58 * s, 0.26 * s)], fill=rgb)
            elif name == "check":
                line([(0.22, 0.52), (0.42, 0.72), (0.78, 0.3)], width=int(lw * 1.3))
            elif name == "inbox":
                d.rounded_rectangle((0.14 * s, 0.24 * s, 0.86 * s, 0.78 * s),
                                    radius=0.1 * s, outline=rgb, width=lw)
                line([(0.14, 0.52), (0.36, 0.52), (0.44, 0.64), (0.56, 0.64), (0.64, 0.52), (0.86, 0.52)])
            return img.resize((size, size), Image.LANCZOS)

        return self._cached(cache_key, build)

    def app_icon(self, size: int = 64, accent: str = "#3B82F6") -> Image.Image:
        """程序图标（托盘 / 快捷方式用）：圆角方块 + 对勾。"""
        s = size * SS
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.24),
                            fill=_hex_to_rgb(accent) + (255,))
        w = int(s * 0.085)
        d.line([(0.26 * s, 0.53 * s), (0.44 * s, 0.71 * s), (0.75 * s, 0.31 * s)],
               fill=(255, 255, 255, 255), width=w, joint="curve")
        return img.resize((size, size), Image.LANCZOS)

    def tray_icon(self, size: int = 32, badge: int = 0, accent: str = "#3B82F6") -> Image.Image:
        """托盘图标：对勾方块，右上角带未完成数量角标。"""
        img = self.app_icon(size, accent)
        if badge > 0:
            d = ImageDraw.Draw(img)
            text = "9+" if badge > 9 else str(badge)
            r = size * 0.44
            cx, cy = size - r * 0.72, r * 0.72
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(239, 68, 68, 255),
                      outline=(255, 255, 255, 255), width=max(1, size // 20))
            # 用内置位图字体画数字，避免依赖具体字体文件
            try:
                from PIL import ImageFont
                font = ImageFont.load_default(size=int(r * 1.25))
                bbox = d.textbbox((0, 0), text, font=font)
                d.text((cx - (bbox[2] - bbox[0]) / 2 - bbox[0],
                        cy - (bbox[3] - bbox[1]) / 2 - bbox[1]),
                       text, font=font, fill=(255, 255, 255, 255))
            except Exception:
                pass
        return img
