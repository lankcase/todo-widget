"""录音：用系统里现成的 ffmpeg 从麦克风采集（无需额外安装任何东西）。

ffmpeg 的查找顺序：imageio-ffmpeg 自带的 → PATH 里的 → 环境变量 FFMPEG_BINARY。
你的机器上 imageio-ffmpeg 已经装好（自带 ffmpeg 7.1），所以开箱即用。

停止录音的做法是往 ffmpeg 的 stdin 写一个 'q'，它会正常收尾并写完整 WAV 头；
同时用 -t 兜底，即使写 'q' 失败也会在到达上限时自动停。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

CREATE_NO_WINDOW = 0x08000000


def ffmpeg_path() -> Optional[str]:
    env = os.environ.get("FFMPEG_BINARY")
    if env and Path(env).exists():
        return env
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass
    found = shutil.which("ffmpeg")
    return found


def _run(args: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess:
    kwargs = {"capture_output": True, "timeout": timeout,
              "creationflags": CREATE_NO_WINDOW if sys.platform == "win32" else 0}
    return subprocess.run(args, **kwargs)


def list_devices() -> list[str]:
    """列出可用的录音设备名（dshow）。"""
    exe = ffmpeg_path()
    if not exe:
        return []
    try:
        proc = _run([exe, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"])
    except Exception:
        return []
    text = (proc.stderr or b"").decode("utf-8", errors="replace")
    devices: list[str] = []
    for line in text.splitlines():
        if "Alternative name" in line:
            continue
        # 格式：  [dshow @ xxx] "设备名" (audio)
        # 注意 (audio)/(video) 标记在引号后面，不是在前面
        m = re.search(r'"([^"]+)"\s*\(\s*(audio|video)\s*\)', line)
        if m and m.group(2) == "audio":
            name = m.group(1).strip()
            if name and name not in devices:
                devices.append(name)
    return devices


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "gbk", "cp936"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


class Recorder:
    """麦克风录音。start() 后一直录，stop() 返回生成的 wav 路径。"""

    def __init__(self, device: str = "", max_seconds: int = 30):
        self.device = device
        self.max_seconds = max(3, min(180, int(max_seconds or 30)))
        self.proc: Optional[subprocess.Popen] = None
        self.path: Optional[str] = None
        self.error = ""
        self.started_at = 0.0
        self._lock = threading.Lock()

    @property
    def recording(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def elapsed(self) -> float:
        return time.time() - self.started_at if self.started_at else 0.0

    def start(self) -> bool:
        exe = ffmpeg_path()
        if not exe:
            self.error = "找不到 ffmpeg（imageio-ffmpeg 或 PATH）"
            return False
        device = self.device
        if not device:
            devs = list_devices()
            if not devs:
                self.error = "没有检测到可用的麦克风"
                return False
            device = devs[0]
        tmp = Path(tempfile.gettempdir()) / f"todowidget-voice-{os.getpid()}.wav"
        args = [exe, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "dshow", "-i", f"audio={device}",
                "-t", str(self.max_seconds),
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                str(tmp)]
        try:
            self.proc = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        except Exception as exc:
            self.error = f"启动录音失败：{exc}"
            return False
        self.path = str(tmp)
        self.started_at = time.time()
        return True

    def stop(self, wait: float = 3.0) -> Optional[str]:
        """停止并返回 wav 路径；失败返回 None 并把原因写进 self.error。"""
        with self._lock:
            proc, self.proc = self.proc, None
        if not proc:
            return None
        try:
            if proc.stdin and proc.poll() is None:
                try:
                    proc.stdin.write(b"q")
                    proc.stdin.flush()
                except Exception:
                    pass
            proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
        err = b""
        try:
            err = proc.stderr.read() or b"" if proc.stderr else b""
        except Exception:
            pass
        self.started_at = 0.0
        path = Path(self.path) if self.path else None
        if path and path.exists() and path.stat().st_size > 2000:
            return str(path)
        self.error = _decode(err).strip()[-300:] or "录音为空（可能没选对麦克风）"
        return None


def duration_of(path: str) -> float:
    """读取音频时长（秒）。用 ffprobe 缺失时的 ffmpeg 兜底解析。"""
    try:
        import wave
        with wave.open(path, "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate() or 1
            return frames / rate
    except Exception:
        return 0.0


def split_wav(path: str, chunk_seconds: int = 28) -> list[str]:
    """把长录音切成若干段（智谱单次转写上限 30 秒）。"""
    total = duration_of(path)
    if total <= chunk_seconds:
        return [path]
    exe = ffmpeg_path()
    if not exe:
        return [path]
    out_dir = Path(tempfile.mkdtemp(prefix="todowidget-seg-"))
    pattern = str(out_dir / "seg-%02d.wav")
    try:
        _run([exe, "-hide_banner", "-loglevel", "error", "-y", "-i", path,
              "-f", "segment", "-segment_time", str(chunk_seconds),
              "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", pattern],
             timeout=60)
    except Exception:
        return [path]
    parts = sorted(str(p) for p in out_dir.glob("seg-*.wav"))
    return parts or [path]
