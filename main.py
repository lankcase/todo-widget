"""TodoWidget 启动入口。

命令行参数：
    --startup    开机自启时使用（会先等几秒，等桌面就绪再贴上去）
    --debug      启动后打印诊断信息并保持窗口可见
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _log_error(exc: BaseException) -> None:
    try:
        from app import config as cfg_mod
        path = cfg_mod.log_file()
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            f.write("".join(traceback.format_exception(exc)))
    except Exception:
        pass


def main() -> int:
    args = sys.argv[1:]
    startup = "--startup" in args

    # DPI 感知必须在创建任何窗口之前设置
    from app import win32tools as w32
    dpi_mode = w32.set_dpi_awareness()

    # 单实例：已经在跑就直接退出（放在延迟之前，避免重复启动白等）
    instance = w32.SingleInstance()
    if instance.already_running:
        return 0

    if startup:
        try:
            from app.config import Config
            delay = int(Config().get("autostart_delay", 8) or 0)
        except Exception:
            delay = 8
        # 分段睡，万一被要求退出也能较快响应
        deadline = time.time() + max(0, min(60, delay))
        while time.time() < deadline:
            time.sleep(0.5)

    try:
        from app.application import Application
        app = Application()
        app.start()
        _diag = os.environ.get("TODOWIDGET_DIAG")
        if _diag:
            try:
                from app import win32tools as _w
                Path(_diag).write_text(
                    _w.dpi_report(app.hwnd, extra={
                        "dpi_mode_arg": dpi_mode,
                        "app_dpi": str(app.dpi),
                        "scale": str(app.theme.scale),
                        "window": f"{app.root.winfo_width()}x{app.root.winfo_height()}",
                        "already_running": str(instance.already_running),
                    }), encoding="utf-8")
            except Exception:
                pass
        app.root.after(2600, app.show_missed_summary)
        if "--debug" in args:
            app.startup_report = {
                "dpi": dpi_mode,
                "scale": app.theme.scale,
                "hwnd": hex(app.hwnd),
                "desktop_mode": app.pinner.mode if app.pinner else "",
                "desktop_status": app.pinner.status if app.pinner else "",
                "desktop_probe": w32.DesktopPinner.probe_desktop(),
            }
        app.root.mainloop()
    except BaseException as exc:      # 顶层兜底，把崩溃写进日志而不是静默消失
        _log_error(exc)
        raise
    finally:
        instance.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
