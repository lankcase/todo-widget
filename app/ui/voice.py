"""语音 / 文字转待办面板。

流程：录音（或粘贴一段文字）→ 转写 → AI 拆成结构化待办 → 勾选后一键加入。
所有网络调用都在后台线程跑，结果通过队列回到 Tk 主线程（Tkinter 不能跨线程操作控件）。
"""

from __future__ import annotations

import datetime
import queue
import threading
import tkinter as tk
from typing import Any, Callable, Optional

from .. import ai as ai_mod
from .. import audio, nlp
from ..models import Task, dt_to_iso
from .common import CardWindow, ChipGroup, IconButton

WIDTH = 380


class VoicePanel(CardWindow):
    def __init__(self, app):
        super().__init__(app, app.theme, app.fonts, width=WIDTH, topmost=True)
        self.app = app
        self.cfg = app.config
        self.store = app.store
        self.ai = ai_mod.from_config(self.cfg)
        self.recorder: Optional[audio.Recorder] = None
        self._q: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self._busy = False
        self._tick_job = None
        self._candidates: list[dict] = []
        self._row_state: list[tk.BooleanVar] = []

        self.voice_chips: Optional[ChipGroup] = None
        self._build()
        self.finalize(position="center")
        self.after(120, self._poll)
        if not self.ai.configured:
            self._set_status("还没配置智谱 API Key，请点右上角齿轮 → 语音与 AI", error=True)

    # ---------------- 界面 ----------------
    def _build(self) -> None:
        pal, th, fonts = self.theme.pal, self.theme, self.fonts
        head = tk.Frame(self.body, bg=pal.card)
        head.pack(fill="x")
        tk.Label(head, text="语音记待办", bg=pal.card, fg=pal.text,
                 font=fonts.h1).pack(side="left")
        IconButton(head, th, "close", size=13, command=self.close,
                   bg=pal.card, tooltip="关闭 (Esc)").pack(side="right")

        tk.Label(self.body, text="说一段话或粘一段文字，自动拆成待办事项",
                 bg=pal.card, fg=pal.text_faint, font=fonts.small,
                 anchor="w").pack(fill="x", pady=(th.px(4), th.px(12)))

        # ---- 录音区 ----
        rec = tk.Frame(self.body, bg=pal.card)
        rec.pack(fill="x")
        self.rec_btn = tk.Label(rec, text="🎤  开始录音", bg=pal.accent, fg="#FFFFFF",
                                font=fonts.body, padx=th.px(18), pady=th.px(9),
                                cursor="hand2")
        self.rec_btn.pack(side="left")
        self.rec_btn.bind("<Button-1>", lambda _e: self.toggle_record())
        self.rec_hint = tk.Label(rec, text="最长 30 秒/段，自动分段", bg=pal.card,
                                 fg=pal.text_faint, font=fonts.tiny)
        self.rec_hint.pack(side="left", padx=(th.px(10), 0))

        # ---- 麦克风选择 ----
        self.mic_frame = tk.Frame(self.body, bg=pal.card)
        self.mic_frame.pack(fill="x", pady=(th.px(8), 0))
        self.mic_chips = ChipGroup(self.mic_frame, th, fonts, [("", "检测中…", None)],
                                   value="", bg=pal.card, chip_h=24, pad_x=10)
        self.mic_chips.pack(fill="x")
        threading.Thread(target=self._load_devices, daemon=True).start()

        # ---- 文字输入 ----
        tk.Label(self.body, text="或者直接粘贴文字（聊天记录 / 会议纪要）", bg=pal.card,
                 fg=pal.text_faint, font=fonts.tiny, anchor="w").pack(
            fill="x", pady=(th.px(12), th.px(4)))
        self.text = tk.Text(self.body, height=4, bd=0, relief="flat", highlightthickness=0,
                            bg=pal.input_bg, fg=pal.text, insertbackground=pal.accent,
                            font=fonts.small, wrap="word", padx=th.px(8), pady=th.px(6))
        self.text.pack(fill="x")

        # ---- 操作 ----
        act = tk.Frame(self.body, bg=pal.card)
        act.pack(fill="x", pady=(th.px(12), 0))
        self.go_btn = self._button(act, "整理成待办", self.run_text, primary=True)
        self.go_btn.pack(side="left")
        self.status = tk.Label(act, text="", bg=pal.card, fg=pal.text_faint,
                               font=fonts.tiny, anchor="w", wraplength=th.px(200),
                               justify="left")
        self.status.pack(side="left", padx=(th.px(10), 0), fill="x", expand=True)

        # ---- 预览 ----
        self.preview_frame = tk.Frame(self.body, bg=pal.card)
        self.preview_head = tk.Label(self.preview_frame, text="", bg=pal.card,
                                     fg=pal.text_dim, font=fonts.tiny, anchor="w")
        self.preview_head.pack(fill="x", pady=(0, th.px(6)))
        self.preview_list = tk.Frame(self.preview_frame, bg=pal.card)
        self.preview_list.pack(fill="x")
        self.preview_btns = tk.Frame(self.preview_frame, bg=pal.card)
        self.preview_btns.pack(fill="x", pady=(th.px(10), 0))

    def _button(self, parent, text: str, command: Callable, *,
                primary: bool = False, bg: Optional[str] = None,
                fg: Optional[str] = None) -> tk.Label:
        pal, th = self.theme.pal, self.theme
        b = bg or (pal.accent if primary else pal.input_bg)
        f = fg or ("#FFFFFF" if primary else pal.text_dim)
        lbl = tk.Label(parent, text=text, bg=b, fg=f, font=self.fonts.small,
                       padx=th.px(14), pady=th.px(6), cursor="hand2")
        lbl.bind("<Button-1>", lambda _e: command())
        return lbl

    # ---------------- 后台任务 ----------------
    def _set_status(self, text: str, error: bool = False) -> None:
        try:
            self.status.configure(text=text,
                                  fg=self.theme.pal.danger if error else self.theme.pal.text_dim)
        except Exception:
            pass

    def _run_async(self, fn: Callable[[], Any], tag: str) -> None:
        if self._busy:
            return
        self._busy = True

        def worker():
            try:
                result = fn()
            except Exception as exc:
                self._q.put(("error", (tag, str(exc))))
            else:
                self._q.put(("done", (tag, result)))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._q.get_nowait()
                tag, value = payload
                if kind == "error":
                    self._busy = False
                    self._set_status(f"{tag}失败：{value}", error=True)
                    self._set_go_enabled(True)
                elif tag == "transcribe":
                    self._busy = False
                    self._on_transcribed(value)
                elif tag == "extract":
                    self._busy = False
                    self._on_extracted(value)
                elif tag == "devices":
                    self._on_devices(value)
                elif tag == "test":
                    self._busy = False
                    ok, msg = value
                    self._set_status(msg, error=not ok)
                    self._set_go_enabled(True)
        except queue.Empty:
            pass
        try:
            self.after(120, self._poll)
        except Exception:
            pass

    def _load_devices(self) -> None:
        devs = audio.list_devices()
        self._q.put(("done", ("devices", devs)))

    def _on_devices(self, devs: list[str]) -> None:
        if not devs:
            self.mic_chips.set_options([("", "未检测到麦克风", None)], "")
            self._set_status("没找到 ffmpeg 或麦克风，语音功能不可用；文字整理仍可用", error=True)
            return
        current = self.cfg.get("mic_device", "")
        opts = [(d, d, None) for d in devs]
        if current not in devs:
            current = devs[0]
            self.cfg["mic_device"] = current
            self.cfg.save()
        self.mic_chips.set_options(opts, current)
        self.mic_chips.command = self._pick_device

    def _pick_device(self, name: str) -> None:
        self.cfg["mic_device"] = name
        self.cfg.save()

    # ---------------- 录音 ----------------
    def toggle_record(self) -> None:
        if self.recorder and self.recorder.recording:
            self._stop_record()
        else:
            self._start_record()

    def _start_record(self) -> None:
        if self._busy:
            return
        if not self.ai.configured:
            self._set_status("先填 API Key 才能转写（齿轮 → 语音与 AI）", error=True)
            return
        max_sec = int(self.cfg.get("max_record_seconds", 30) or 30)
        self.recorder = audio.Recorder(self.cfg.get("mic_device", ""), max_sec)
        if not self.recorder.start():
            self._set_status(self.recorder.error or "录音启动失败", error=True)
            self.recorder = None
            return
        self.rec_btn.configure(text="⏹  停止录音", bg=self.theme.pal.danger)
        self._set_status("正在录音…")
        self._tick()

    def _tick(self) -> None:
        if not self.recorder or not self.recorder.recording:
            return
        el = self.recorder.elapsed()
        limit = self.recorder.max_seconds
        self.rec_hint.configure(text=f"{el:04.1f}s / {limit}s")
        if el + 0.4 >= limit:
            self._stop_record()
            return
        self._tick_job = self.after(200, self._tick)

    def _stop_record(self) -> None:
        if self._tick_job:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None
        if not self.recorder:
            return
        self.rec_btn.configure(text="🎤  开始录音", bg=self.theme.pal.accent)
        self.rec_hint.configure(text="最长 30 秒/段，自动分段")
        path = self.recorder.stop()
        self.recorder = None
        if not path:
            self._set_status("没录到声音，检查麦克风是否选对", error=True)
            return
        self._set_status("正在转写…")
        self._set_go_enabled(False)
        self._run_async(lambda: self.ai.transcribe(path), "transcribe")

    def _on_transcribed(self, text: str) -> None:
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self._set_status("转写完成，正在拆分任务…")
        self._extract(text)

    # ---------------- 文字整理 ----------------
    def run_text(self) -> None:
        raw = self.text.get("1.0", "end").strip()
        if not raw:
            self._set_status("先录一段音或粘一段文字", error=True)
            return
        if not self.ai.configured:
            self._set_status("先填 API Key（齿轮 → 语音与 AI）", error=True)
            return
        self._extract(raw)

    def _extract(self, text: str) -> None:
        cats = [l.name for l in self.store.lists]
        self._set_status("AI 正在整理…")
        self._set_go_enabled(False)
        self._run_async(lambda: self.ai.extract_tasks(text, cats), "extract")

    def _on_extracted(self, tasks: list[dict]) -> None:
        self._set_go_enabled(True)
        self._candidates = tasks
        if not tasks:
            self._set_status("这段话里没找到需要做的事", error=False)
            self._render_preview()
            return
        self._set_status(f"整理出 {len(tasks)} 项，确认后加入")
        self._render_preview()

    # ---------------- 预览 ----------------
    def _render_preview(self) -> None:
        pal, th, fonts = self.theme.pal, self.theme, self.fonts
        for child in self.preview_list.winfo_children():
            child.destroy()
        for child in self.preview_btns.winfo_children():
            child.destroy()
        self._row_state = []

        if not self._candidates:
            self.preview_frame.pack_forget()
            self.finalize(position="none")
            return

        self.preview_head.configure(text=f"预览（{len(self._candidates)} 项，取消勾选可跳过）")
        for item in self._candidates:
            row = tk.Frame(self.preview_list, bg=pal.card)
            row.pack(fill="x", pady=1)
            var = tk.BooleanVar(value=True)
            self._row_state.append(var)
            tk.Checkbutton(row, variable=var, bg=pal.card, activebackground=pal.card,
                           selectcolor=pal.input_bg, bd=0, highlightthickness=0,
                           cursor="hand2").pack(side="left")
            col = tk.Frame(row, bg=pal.card)
            col.pack(side="left", fill="x", expand=True)
            tk.Label(col, text=item["title"], bg=pal.card, fg=pal.text,
                     font=fonts.small, anchor="w").pack(fill="x")
            bits = []
            if item.get("priority"):
                bits.append({3: "高", 2: "中", 1: "低", 0: "无"}[item["priority"]])
            if item.get("due"):
                bits.append(nlp.humanize_due(_parse_due(item["due"])))
            if item.get("category"):
                bits.append(item["category"])
            if item.get("note"):
                bits.append(item["note"][:20])
            if bits:
                tk.Label(col, text=" · ".join(bits), bg=pal.card, fg=pal.text_faint,
                         font=fonts.tiny, anchor="w").pack(fill="x")

        cnt = sum(1 for v in self._row_state if v.get())
        self._button(self.preview_btns, "取消", self.close).pack(side="right")
        self._button(self.preview_btns, f"加入 {cnt} 项待办", self._commit,
                     primary=True).pack(side="right", padx=(0, th.px(8)))
        self.preview_frame.pack(fill="x", pady=(th.px(12), 0))
        self.finalize(position="none")

    def _commit(self) -> None:
        added = 0
        now = datetime.datetime.now()
        for item, var in zip(self._candidates, self._row_state):
            if not var.get():
                continue
            task = Task.new(item["title"])
            task.priority = int(item.get("priority") or 0)
            if item.get("due"):
                task.due = item["due"]
            if item.get("note"):
                task.note = item["note"]
            cat = item.get("category")
            if cat:
                task.list_id = self.store.ensure_list(cat).id
            self.store.add(task, save=False)
            added += 1
        if added:
            self.store.save()
            self.app.toast(f"已加入 {added} 项待办")
        self.close()

    def _set_go_enabled(self, enabled: bool) -> None:
        try:
            self.go_btn.configure(
                bg=self.theme.pal.accent if enabled else self.theme.pal.track,
                fg="#FFFFFF" if enabled else self.theme.pal.text_faint)
        except Exception:
            pass

    def on_escape(self) -> None:
        self.close()

    def close(self) -> None:
        if self.recorder and self.recorder.recording:
            try:
                self.recorder.stop()
            except Exception:
                pass
        try:
            self.destroy()
        except Exception:
            pass
        self.app.on_voice_closed()


def _parse_due(value: str) -> Optional[datetime.datetime]:
    from ..models import parse_dt
    dt = parse_dt(value)
    if dt:
        return dt
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d")
    except Exception:
        return None
