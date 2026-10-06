"""设置面板：外观 / 贴桌面 / 提醒 / 语音与 AI / 系统 / 数据。

用顶部小标签切换分区，避免面板长成一条。所有改动即时保存并即时生效。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog
from typing import Callable, Optional

from .. import autostart
from .. import config as cfg_mod
from .. import win32tools as w32
from ..models import Task
from .common import CardWindow, ChipGroup, IconButton, Switch

WIDTH = 420

SECTIONS = [("look", "外观"), ("desktop", "桌面"), ("remind", "提醒"),
            ("ai", "语音与AI"), ("system", "系统"), ("data", "数据")]

MODES = [("bottom", "贴桌面·置底（推荐）"), ("embed", "嵌入桌面层（实验）"), ("top", "常驻置顶")]

MODE_NOTE = {
    "bottom": "窗口压在所有窗口之下，不挡任何东西；点一下会临时浮到最前让你输入，"
              "按 Win+D 显示桌面时它也在。",
    "embed": "把窗口挂进系统桌面层，按 Win+D 也不会消失。但部分系统上会因为"
             "动态壁纸（Wallpaper Engine 等）独占渲染而显示不出来——程序启动时会自检，"
             "一旦发现显示不出来会自动切回「贴桌面·置底」并提示你。",
    "top": "一直在最前面，适合需要一直盯着看的场景，但会挡住别的窗口。",
}
OFFSETS = [("0", "到点"), ("5", "提前5分"), ("15", "提前15分"), ("30", "提前30分"), ("60", "提前1小时")]


class SettingsPanel(CardWindow):
    def __init__(self, app):
        super().__init__(app, app.theme, app.fonts, width=WIDTH, topmost=True)
        self.app = app
        self.cfg = app.config
        self.store = app.store
        self.section = "look"
        self._rows: list[tk.Widget] = []
        self._build()
        self.finalize(position="center")
        self.after(50, self._resize)

    # ---------------- 骨架 ----------------
    def _build(self) -> None:
        pal, th, fonts = self.theme.pal, self.theme, self.fonts
        head = tk.Frame(self.body, bg=pal.card)
        head.pack(fill="x")
        tk.Label(head, text="设置", bg=pal.card, fg=pal.text,
                 font=fonts.h1).pack(side="left")
        IconButton(head, th, "close", size=13, command=self.close,
                   bg=pal.card, tooltip="关闭 (Esc)").pack(side="right")

        self.tabs = ChipGroup(self.body, th, fonts,
                              [(k, label, None) for k, label in SECTIONS],
                              value=self.section, command=self._switch, chip_h=28)
        self.tabs.pack(fill="x", pady=(th.px(10), th.px(12)))

        self.content = tk.Frame(self.body, bg=pal.card)
        self.content.pack(fill="both", expand=True)
        self._render_section()

    def _switch(self, key: str) -> None:
        self.section = key
        self._render_section()

    def _clear(self) -> None:
        for w in self.content.winfo_children():
            w.destroy()

    def _render_section(self) -> None:
        self._clear()
        builder = {"look": self._sec_look, "desktop": self._sec_desktop,
                   "remind": self._sec_remind, "ai": self._sec_ai,
                   "system": self._sec_system, "data": self._sec_data}.get(self.section)
        if builder:
            builder()
        self._resize()

    def _resize(self) -> None:
        try:
            self.update_idletasks()
            w = max(self.theme.px(WIDTH), self.body.winfo_reqwidth()) + self.pad * 2
            h = self.body.winfo_reqheight() + self.pad * 2
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            if h > sh - 80:
                h = sh - 80
            img = self.theme.card(w, h, self.theme.px(14))
            self._card = img
            self.bg.delete("all")
            self.bg.create_image(0, 0, image=img, anchor="nw")
            self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(20, (sh - h) // 3)}")
        except Exception:
            pass

    # ---------------- 通用控件 ----------------
    def _row(self, pady=(0, 8)) -> tk.Frame:
        f = tk.Frame(self.content, bg=self.theme.pal.card)
        f.pack(fill="x", pady=pady)
        return f

    def _hint(self, parent, text: str, pady=(0, 10)) -> tk.Label:
        lbl = tk.Label(parent, text=text, bg=self.theme.pal.card,
                       fg=self.theme.pal.text_faint, font=self.fonts.tiny,
                       anchor="w", justify="left", wraplength=self.theme.px(WIDTH - 60))
        lbl.pack(fill="x", pady=pady)
        return lbl

    def _switch_row(self, label: str, key: str, hint: str = "",
                    on_change: Optional[Callable[[bool], None]] = None) -> Switch:
        pal = self.theme.pal
        row = self._row((0, 6))
        sw = Switch(row, self.theme, bool(self.cfg.get(key)), bg=pal.card,
                    command=lambda v: self._set(key, v, on_change))
        sw.pack(side="right")
        tk.Label(row, text=label, bg=pal.card, fg=pal.text,
                 font=self.fonts.body).pack(side="left")
        if hint:
            tk.Label(row, text=hint, bg=pal.card, fg=pal.text_faint,
                     font=self.fonts.tiny, anchor="w").pack(side="left",
                                                            padx=(self.theme.px(8), 0))
        return sw

    def _entry_row(self, label: str, key: str, *, width: int = 26,
                   show: Optional[str] = None) -> tk.Entry:
        pal = self.theme.pal
        row = self._row((0, 6))
        tk.Label(row, text=label, bg=pal.card, fg=pal.text,
                 font=self.fonts.small).pack(side="left")
        var = tk.StringVar(value=str(self.cfg.get(key, "")))
        entry = tk.Entry(row, textvariable=var, bd=0, relief="flat", highlightthickness=0,
                         bg=pal.input_bg, fg=pal.text, insertbackground=pal.accent,
                         font=self.fonts.small, width=width, show=show)
        entry.pack(side="right", ipady=self.theme.px(4), padx=(self.theme.px(8), 0))
        var.trace_add("write", lambda *_: self._set(key, var.get()))
        return entry

    def _chips_row(self, label: str, key: str, options: list) -> None:
        pal = self.theme.pal
        self._label_block(label)
        chips = ChipGroup(self.content, self.theme, self.fonts, options,
                          value=str(self.cfg.get(key)), command=lambda v: self._set(key, v))
        chips.pack(fill="x", pady=(0, self.theme.px(10)))

    def _label_block(self, text: str) -> None:
        tk.Label(self.content, text=text, bg=self.theme.pal.card,
                 fg=self.theme.pal.text_dim, font=self.fonts.tiny,
                 anchor="w").pack(fill="x", pady=(self.theme.px(6), self.theme.px(2)))

    def _button(self, parent, text: str, command: Callable, *,
                primary: bool = False, danger: bool = False) -> tk.Label:
        pal, th = self.theme.pal, self.theme
        bg = pal.accent if primary else (pal.input_bg)
        fg = "#FFFFFF" if primary else (pal.danger if danger else pal.text_dim)
        lbl = tk.Label(parent, text=text, bg=bg, fg=fg, font=self.fonts.small,
                       padx=th.px(14), pady=th.px(6), cursor="hand2")
        lbl.bind("<Button-1>", lambda _e: command())
        return lbl

    def _set(self, key: str, value, on_change: Optional[Callable[[bool], None]] = None) -> None:
        self.cfg[key] = value
        self.cfg.save()
        if on_change:
            on_change(value)

    # ---------------- 各分区 ----------------
    def _sec_look(self) -> None:
        pal, th = self.theme.pal, self.theme
        self._label_block("主题")
        ChipGroup(self.content, th, self.fonts,
                  [("light", "浅色", None), ("dark", "深色", None)],
                  value=self.cfg.get("theme"),
                  command=self._set_theme).pack(fill="x", pady=(0, th.px(12)))

        self._label_block("透明度")
        row = self._row((0, 4))
        var = tk.DoubleVar(value=float(self.cfg.get("opacity", 0.97)) * 100)
        scale = tk.Scale(row, from_=40, to=100, orient="horizontal", variable=var,
                         showvalue=False, bd=0, highlightthickness=0, relief="flat",
                         bg=pal.card, troughcolor=pal.track, activebackground=pal.accent,
                         sliderrelief="flat", length=th.px(220), command=self._on_opacity)
        scale.pack(side="left")
        self.opacity_lbl = tk.Label(row, text=f"{int(var.get())}%", bg=pal.card,
                                    fg=pal.text_dim, font=self.fonts.small)
        self.opacity_lbl.pack(side="left", padx=(th.px(8), 0))

        self._label_block("窗口宽度（越小越不占地方）")
        row2 = self._row((0, 4))
        wvar = tk.IntVar(value=int(self.cfg.get("width", 330)))
        tk.Scale(row2, from_=260, to=460, orient="horizontal", variable=wvar,
                 showvalue=False, bd=0, highlightthickness=0, relief="flat",
                 bg=pal.card, troughcolor=pal.track, activebackground=pal.accent,
                 sliderrelief="flat", length=th.px(220),
                 command=self._on_width).pack(side="left")
        self.width_lbl = tk.Label(row2, text=f"{wvar.get()} px", bg=pal.card,
                                  fg=pal.text_dim, font=self.fonts.small)
        self.width_lbl.pack(side="left", padx=(th.px(8), 0))

        self._label_block("窗口高度")
        row3 = self._row((0, 10))
        hvar = tk.IntVar(value=int(self.cfg.get("height", 480)))
        tk.Scale(row3, from_=320, to=800, orient="horizontal", variable=hvar,
                 showvalue=False, bd=0, highlightthickness=0, relief="flat",
                 bg=pal.card, troughcolor=pal.track, activebackground=pal.accent,
                 sliderrelief="flat", length=th.px(220),
                 command=self._on_height).pack(side="left")
        self.height_lbl = tk.Label(row3, text=f"{hvar.get()} px", bg=pal.card,
                                   fg=pal.text_dim, font=self.fonts.small)
        self.height_lbl.pack(side="left", padx=(th.px(8), 0))
        self._hint(self.content, "提示：拖窗口标题栏也能移动位置，位置会自动记住。")

    def _set_theme(self, value: str) -> None:
        self.cfg["theme"] = value
        self.cfg.save()
        self.app.apply_settings()
        self._restyle_self()

    def _restyle_self(self) -> None:
        """设置面板自己也跟着换主题，否则会白底看着别扭。"""
        pal = self.theme.pal
        try:
            self.configure(bg=pal.key)
            self.wm_attributes("-transparentcolor", pal.key)
            self.bg.configure(bg=pal.key)
            self.body.configure(bg=pal.card)
        except Exception:
            pass
        self.section = self.section
        self.tabs.set_options([(k, label, None) for k, label in SECTIONS], self.section)
        self._render_section()

    def _on_opacity(self, value) -> None:
        v = float(value) / 100.0
        self.cfg["opacity"] = round(v, 3)
        self.cfg.save()
        self.app.set_opacity(v)
        try:
            self.opacity_lbl.configure(text=f"{int(float(value))}%")
        except Exception:
            pass

    def _on_width(self, value) -> None:
        self.cfg["width"] = int(float(value))
        self.cfg.save()
        self.app._apply_geometry()
        try:
            self.width_lbl.configure(text=f"{int(float(value))} px")
        except Exception:
            pass

    def _on_height(self, value) -> None:
        self.cfg["height"] = int(float(value))
        self.cfg.save()
        self.app._apply_geometry()
        try:
            self.height_lbl.configure(text=f"{int(float(value))} px")
        except Exception:
            pass

    def _sec_desktop(self) -> None:
        th = self.theme
        self._label_block("贴在桌面的方式")
        ChipGroup(self.content, th, self.fonts, MODES, value=self.cfg.get("desktop_mode"),
                  command=self._set_mode).pack(fill="x", pady=(0, th.px(10)))
        info = w32.DesktopPinner.probe_desktop()
        cur = self.app.pinner.mode if self.app.pinner else "?"
        status = self.app.pinner.status if self.app.pinner else "未初始化"
        self._hint(self.content,
                   f"当前：{status}\n"
                   f"桌面结构：Progman={'有' if info.get('progman') else '无'}，"
                   f"图标层={'有' if info.get('defview') else '无'}，"
                   f"壁纸层={'有' if info.get('wallpaper_worker') else '无'}")
        self._switch_row("点击后浮到最前（方便输入）", "click_raise",
                         on_change=lambda _v: self.app.apply_settings())
        self._switch_row("记住窗口位置", "remember_pos")
        self._hint(self.content,
                   "· 贴桌面·置底：窗口压在所有窗口下面，不挡任何东西，点一下临时浮到最前。\n"
                   "· 嵌入桌面层：真正成为桌面的一部分，按 Win+D 也不会消失，\n"
                   "  但和动态壁纸（Wallpaper Engine）可能冲突，失败会自动回退。\n"
                   "· 常驻置顶：一直在最前面。")

    def _set_mode(self, value: str) -> None:
        self.cfg["desktop_mode"] = value
        self.cfg.save()
        self.app._set_mode(value)
        self._render_section()

    def _sec_remind(self) -> None:
        th = self.theme
        self._label_block("默认提前多久提醒")
        ChipGroup(self.content, th, self.fonts, OFFSETS,
                  value=str(self.cfg.get("remind_default_offset", 0)),
                  command=lambda v: self._set_offset(v)).pack(fill="x", pady=(0, th.px(10)))
        self._hint(self.content, "只影响新建任务时的默认值，单条任务可以在编辑器里单独设。")

        self._switch_row("开机后汇总错过的提醒", "missed_summary")
        self._switch_row("免打扰时段", "dnd_enabled")
        row = self._row((0, 6))
        tk.Label(row, text="从", bg=th.pal.card, fg=th.pal.text,
                 font=self.fonts.small).pack(side="left")
        self._time_entry(row, "dnd_start")
        tk.Label(row, text="到", bg=th.pal.card, fg=th.pal.text,
                 font=self.fonts.small).pack(side="left", padx=(th.px(8), 0))
        self._time_entry(row, "dnd_end")
        tk.Label(row, text="（这段时间内不弹提醒）", bg=th.pal.card, fg=th.pal.text_faint,
                 font=self.fonts.tiny).pack(side="left", padx=(th.px(8), 0))

        self._switch_row("提醒时播放提示音", "reminder_sound")
        self._switch_row("同时发系统通知", "reminder_toast")
        self._hint(self.content, "提醒主要是弹窗形式（带完成/延后按钮），上面两项是可选的额外提醒方式。")

    def _time_entry(self, parent, key: str) -> None:
        var = tk.StringVar(value=str(self.cfg.get(key, "")))
        e = tk.Entry(parent, textvariable=var, bd=0, relief="flat", highlightthickness=0,
                     bg=self.theme.pal.input_bg, fg=self.theme.pal.text,
                     insertbackground=self.theme.pal.accent, font=self.fonts.small,
                     width=6, justify="center")
        e.pack(side="left", ipady=self.theme.px(3), padx=(self.theme.px(4), 0))
        var.trace_add("write", lambda *_: self._set(key, var.get()))

    def _set_offset(self, value: str) -> None:
        self._set("remind_default_offset", int(value))

    def _sec_ai(self) -> None:
        pal, th = self.theme.pal, self.theme
        configured = bool(str(self.cfg.get("ai_api_key", "")).strip())
        tk.Label(self.content,
                 text="已配置 ✓" if configured else "尚未配置 API Key",
                 bg=pal.card, fg=pal.accent if configured else pal.p_mid,
                 font=self.fonts.small, anchor="w").pack(fill="x", pady=(0, th.px(8)))

        self._label_block("智谱 API Key")
        row = self._row((0, 4))
        var = tk.StringVar(value=str(self.cfg.get("ai_api_key", "")))
        entry = tk.Entry(row, textvariable=var, bd=0, relief="flat", highlightthickness=0,
                         bg=pal.input_bg, fg=pal.text, insertbackground=pal.accent,
                         font=self.fonts.small)
        entry.pack(side="left", fill="x", expand=True, ipady=th.px(4))
        var.trace_add("write", lambda *_: self._set("ai_api_key", var.get().strip()))
        self._button(row, "测试", self._test_ai).pack(side="left", padx=(th.px(6), 0))
        self._hint(self.content,
                   "去 open.bigmodel.cn 注册后创建 API Key 填在这里。\n"
                   "转写模型按量计费（很便宜）；任务拆解默认用 glm-4.7-flash，目前免费。\n"
                   "没填 Key 也不影响其它所有功能。", pady=(th.px(4), th.px(10)))

        self.ai_status = tk.Label(self.content, text="", bg=pal.card, fg=pal.text_dim,
                                  font=self.fonts.tiny, anchor="w", justify="left",
                                  wraplength=th.px(WIDTH - 60))
        self.ai_status.pack(fill="x", pady=(0, th.px(8)))

        self._entry_row("接口地址", "ai_base_url", width=28)
        self._entry_row("转写模型", "ai_asr_model", width=16)
        self._entry_row("拆解模型", "ai_chat_model", width=16)
        self._entry_row("录音上限(秒)", "max_record_seconds", width=6)
        self._entry_row("热词（逗号分隔）", "ai_hotwords", width=22)
        self._hint(self.content,
                   "热词能明显提升人名/专业词的识别率，比如：二次根式,班会,体检。\n"
                   "智谱单次转写上限 30 秒，长录音会自动分段拼接。")

        self._label_block("麦克风")
        self.mic_chips = ChipGroup(self.content, th, self.fonts,
                                   [("", "点这里检测设备", None)], value="")
        self.mic_chips.pack(fill="x", pady=(0, th.px(8)))
        self._button(self.content, "检测麦克风", self._load_mics).pack(anchor="w")

    def _test_ai(self) -> None:
        from .. import ai as ai_mod
        client = ai_mod.from_config(self.cfg)
        if not client.configured:
            self.ai_status.configure(text="还没填 API Key", fg=self.theme.pal.danger)
            return
        self.ai_status.configure(text="正在测试连接…", fg=self.theme.pal.text_dim)
        import threading
        import queue as _q
        q: "_q.Queue" = _q.Queue()

        def work():
            q.put(client.test_connection())

        threading.Thread(target=work, daemon=True).start()

        def poll():
            try:
                ok, msg = q.get_nowait()
            except Exception:
                self.after(150, poll)
                return
            self.ai_status.configure(text=msg,
                                     fg=self.theme.pal.accent if ok else self.theme.pal.danger)

        self.after(150, poll)

    def _load_mics(self) -> None:
        from .. import audio
        devs = audio.list_devices()
        if not devs:
            self.mic_chips.set_options([("", "未检测到（缺 ffmpeg 或麦克风）", None)], "")
            return
        cur = self.cfg.get("mic_device", "")
        if cur not in devs:
            cur = devs[0]
            self.cfg["mic_device"] = cur
            self.cfg.save()
        self.mic_chips.set_options([(d, d, None) for d in devs], cur)

        def pick(name: str) -> None:
            self.cfg["mic_device"] = name
            self.cfg.save()

        self.mic_chips.command = pick

    def _sec_system(self) -> None:
        th = self.theme
        enabled = autostart.is_enabled()
        row = self._row((0, 6))
        sw = Switch(row, th, enabled, bg=th.pal.card, command=self._toggle_autostart)
        sw.pack(side="right")
        tk.Label(row, text="开机自动启动", bg=th.pal.card, fg=th.pal.text,
                 font=self.fonts.body).pack(side="left")
        self._hint(self.content,
                   f"自启方式：在「启动」文件夹里放一个快捷方式（指向 pythonw，不会有黑框）。\n"
                   f"当前状态：{'已开启' if enabled else '未开启'}。也可以在"
                   f"「任务管理器 → 启动应用」里看到并禁用。")

        self._entry_row("开机延迟启动(秒)", "autostart_delay", width=6)
        self._switch_row("启用全局热键", "hotkeys_enabled",
                         on_change=lambda _v: self.app.apply_settings())
        self._entry_row("唤出/收起窗口", "hotkey_toggle", width=16)
        self._entry_row("语音记待办", "hotkey_voice", width=16)
        self._hint(self.content,
                   "格式如 ctrl+alt+t、ctrl+shift+space。改完立即生效；\n"
                   "如果提示被占用，换一个组合即可。")

    def _toggle_autostart(self, value: bool) -> None:
        if value:
            ok, msg = autostart.enable()
        else:
            ok, msg = autostart.disable()
        self.cfg["autostart"] = value
        self.cfg.save()
        if not ok:
            self.app.toast(msg, 4000)
        else:
            self.app.toast("已开启开机自启" if value else "已关闭开机自启")
        self._render_section()

    def _sec_data(self) -> None:
        th = self.theme
        counts = self.store.counts()
        d = cfg_mod.data_dir()
        self._hint(self.content,
                   f"数据文件：{d}\\data.json\n"
                   f"任务 {counts['total']} 条（未完成 {counts['active']}，今日完成 {counts['done_today']}）\n"
                   f"每天首次启动会自动备份到 backups\\，保留最近 7 份。\n"
                   f"用 JSON 存是为了你随时能自己打开看、备份、手改。", pady=(0, th.px(12)))

        row = self._row((0, 6))
        self._button(row, "打开数据文件夹", self._open_dir).pack(side="left")
        self._button(row, "立即备份", self._backup_now).pack(side="left", padx=(th.px(6), 0))

        self._label_block("导入 / 导出")
        row2 = self._row((0, 6))
        self._button(row2, "导出 JSON", lambda: self._export("json")).pack(side="left")
        self._button(row2, "导出 CSV", lambda: self._export("csv")).pack(side="left",
                                                                        padx=(th.px(6), 0))
        self._button(row2, "导入 JSON", self._import).pack(side="left", padx=(th.px(6), 0))

        self._label_block("清理")
        row3 = self._row((0, 6))
        done = sum(1 for t in self.store.tasks if t.done)
        self._button(row3, f"清空已完成（{done} 条）", self._clear_done,
                     danger=True).pack(side="left")
        self._switch_row("删除前先确认", "confirm_delete")

    def _open_dir(self) -> None:
        import os
        try:
            os.startfile(str(cfg_mod.data_dir()))
        except Exception as exc:
            self.app.toast(f"打不开：{exc}")

    def _backup_now(self) -> None:
        path = self.store.backup_daily(force=True)
        self.app.toast(f"已备份到 {path.name}" if path else "备份失败")

    def _export(self, kind: str) -> None:
        d = cfg_mod.data_dir()
        if kind == "json":
            path = filedialog.asksaveasfilename(
                title="导出为 JSON", defaultextension=".json",
                initialfile="todowidget-backup.json", initialdir=str(d),
                filetypes=[("JSON 文件", "*.json")])
            if not path:
                return
            from pathlib import Path
            self.store.export_json(Path(path))
        else:
            path = filedialog.asksaveasfilename(
                title="导出为 CSV", defaultextension=".csv",
                initialfile="todowidget.csv", initialdir=str(d),
                filetypes=[("CSV 文件", "*.csv")])
            if not path:
                return
            from pathlib import Path
            self.store.export_csv(Path(path))
        self.app.toast("导出完成")

    def _import(self) -> None:
        path = filedialog.askopenfilename(title="选择要导入的 JSON",
                                          filetypes=[("JSON 文件", "*.json")])
        if not path:
            return
        from pathlib import Path
        try:
            n = self.store.import_json(Path(path))
            self.app.toast(f"已导入 {n} 条任务")
        except Exception as exc:
            self.app.toast(f"导入失败：{exc}", 4000)
        self._render_section()

    def _clear_done(self) -> None:
        from tkinter import messagebox
        n = sum(1 for t in self.store.tasks if t.done)
        if not n:
            self.app.toast("没有已完成的任务")
            return
        if messagebox.askyesno("确认", f"确定清空 {n} 条已完成的任务吗？此操作不可撤销。",
                               parent=self):
            self.store.clear_completed()
            self.app.toast(f"已清空 {n} 条")
            self._render_section()

    # ---------------- 收尾 ----------------
    def on_escape(self) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.destroy()
        except Exception:
            pass
        self.app.on_settings_closed()
