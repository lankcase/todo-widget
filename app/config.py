"""配置读写。

位置：%APPDATA%\\TodoWidget\\config.json
任务数据同目录下的 data.json，挪动程序目录不会丢数据。
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

APP_NAME = "TodoWidget"


def data_dir() -> Path:
    # 便携模式：设置了 TODOWIDGET_HOME 就把数据放在那个目录里（也方便测试隔离）
    override = os.environ.get("TODOWIDGET_HOME")
    if override:
        p = Path(override)
        p.mkdir(parents=True, exist_ok=True)
        return p
    base = os.environ.get("APPDATA") or str(Path.home())
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_file() -> Path:
    return data_dir() / "data.json"


def config_file() -> Path:
    return data_dir() / "config.json"


def log_file() -> Path:
    return data_dir() / "error.log"


DEFAULTS: dict[str, Any] = {
    # ---- 外观 ----
    "theme": "light",            # light | dark
    "opacity": 0.97,             # 0.5 ~ 1.0
    "font_scale": 1.0,           # 0.85 ~ 1.4
    "density": "comfortable",    # compact | comfortable
    "width": 330,
    "height": 480,
    "pos_x": None,               # None = 自动放到右上角
    "pos_y": None,
    # ---- 贴桌面 ----
    "desktop_mode": "bottom",    # bottom | embed | top
    "click_raise": True,         # 点击时临时浮起以便输入
    "remember_pos": True,
    # ---- 提醒 ----
    "remind_default_offset": 0,  # 默认提前多少分钟提醒（0 = 到点）
    "reminder_sound": False,     # 自绘弹窗之外的额外提示（默认关）
    "reminder_toast": False,     # Windows 系统通知（默认关）
    "dnd_enabled": False,        # 免打扰时段
    "dnd_start": "23:00",
    "dnd_end": "07:00",
    "missed_summary": True,      # 启动时汇总关机期间错过的提醒
    # ---- 系统集成 ----
    "autostart": False,
    "autostart_delay": 8,        # 开机延迟多少秒启动（等桌面就绪）
    "hotkey_toggle": "ctrl+alt+t",
    "hotkey_voice": "ctrl+alt+v",
    "hotkeys_enabled": True,
    # ---- 行为 ----
    "week_start": 0,             # 0=周一
    "confirm_delete": True,
    "default_view": "today",     # today | week | all | quadrant | done
    # ---- AI（语音转待办）----
    "ai_api_key": "",
    "ai_base_url": "https://open.bigmodel.cn/api/paas/v4/",
    "ai_asr_model": "glm-asr-2512",
    "ai_chat_model": "glm-4.7-flash",
    "mic_device": "",            # 空 = 用系统默认麦克风
    "max_record_seconds": 30,    # 智谱单次转写上限 30 秒
    "ai_hotwords": "",           # 逗号分隔的专业术语，提高识别率
    # ---- 窗口内状态 ----
    "last_view": "today",
    "lists_collapsed": False,
}


def _merge_defaults(loaded: dict[str, Any]) -> dict[str, Any]:
    """用默认值补齐缺失的键，但保留用户已有值。"""
    out = copy.deepcopy(DEFAULTS)
    for k, v in loaded.items():
        if k in DEFAULTS:
            out[k] = v
        else:
            out[k] = v  # 保留未知键，方便未来版本回退
    return out


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or config_file()
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                raw = self.path.read_text(encoding="utf-8")
                loaded = json.loads(raw) if raw.strip() else {}
                if isinstance(loaded, dict):
                    self._data = _merge_defaults(loaded)
        except Exception:
            # 配置损坏时不要崩，用默认值继续（原文件保留不动，便于排查）
            self._data = copy.deepcopy(DEFAULTS)

    def save(self) -> None:
        try:
            payload = json.dumps(self._data, ensure_ascii=False, indent=2)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload)
                os.replace(tmp, self.path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception:
            pass  # 配置写失败不应中断程序

    # 字典式访问
    def __getitem__(self, key: str) -> Any:
        return self._data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def update(self, **kwargs: Any) -> None:
        self._data.update(kwargs)

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)
