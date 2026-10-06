"""开机自启：在「启动」文件夹里放一个指向 pythonw 的快捷方式。

用 .lnk 而不是 .vbs：不会被杀软误报，而且能在「任务管理器 → 启动」里看见、可被用户禁用。
启动延迟由程序自己在 --startup 参数下等待，等桌面（Progman）就绪再贴上去。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import config as cfg_mod

LNK_NAME = "TodoWidget 待办清单.lnk"


def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_dir() / LNK_NAME


def project_root() -> Path:
    """程序目录（app/ 的上一级）。"""
    return Path(__file__).resolve().parent.parent


def main_script() -> Path:
    return project_root() / "main.py"


def pythonw() -> str:
    """优先用 pythonw.exe（无控制台窗口）；找不到就退回 python.exe。"""
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    if cand.exists():
        return str(cand)
    return str(exe)


def is_enabled() -> bool:
    return shortcut_path().exists()


def enable() -> tuple[bool, str]:
    """创建自启快捷方式。返回 (是否成功, 说明)。"""
    try:
        import win32com.client
    except Exception as exc:
        return False, f"缺少 pywin32，无法创建快捷方式：{exc}"
    try:
        startup_dir().mkdir(parents=True, exist_ok=True)
        shell = win32com.client.Dispatch("WScript.Shell")
        lnk = shell.CreateShortCut(str(shortcut_path()))
        lnk.TargetPath = pythonw()
        lnk.Arguments = f'"{main_script()}" --startup'
        lnk.WorkingDirectory = str(project_root())
        lnk.Description = "开机自动启动桌面待办清单"
        lnk.IconLocation = str(icon_path())
        lnk.save()
        return True, f"已创建：{shortcut_path()}"
    except Exception as exc:
        return False, f"创建快捷方式失败：{exc}"


def disable() -> tuple[bool, str]:
    try:
        p = shortcut_path()
        if p.exists():
            p.unlink()
            return True, "已关闭开机自启"
        return True, "开机自启本来就是关闭的"
    except Exception as exc:
        return False, f"删除快捷方式失败：{exc}"


def icon_path() -> Path:
    """生成 .ico（托盘和快捷方式都用它），只在缺失时生成一次。"""
    p = cfg_mod.data_dir() / "app.ico"
    if not p.exists():
        try:
            from .theme import Theme
            img = Theme("light", 1.0).app_icon(64)
            img.save(p, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])
        except Exception:
            return Path(sys.executable)   # 兜底：用 Python 自己的图标
    return p


def ensure_icon() -> Path:
    return icon_path()
