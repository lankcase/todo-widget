"""端到端冒烟测试：真的把程序跑起来，用真实操作验证功能链路。

单元测试验的是纯逻辑，这里验的是"装到一起还能不能用"：
快速添加 → 解析出时间/优先级/分类 → 列表出现 → 勾选完成 → 重复任务生成下一次
→ 延期 → 搜索 → 切换视图 → 编辑器保存 → 删除。

用法：python tools/smoke_test.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP_HOME = Path(tempfile.mkdtemp(prefix="todowidget-smoke-"))
os.environ["TODOWIDGET_HOME"] = str(TMP_HOME)

from app import win32tools as w32  # noqa: E402

w32.set_dpi_awareness()

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"  {'✓' if condition else '✗'} {name}" + (f"  [{detail}]" if detail and not condition else ""))


def main() -> int:
    from app.config import Config
    cfg = Config()
    cfg["desktop_mode"] = "top"          # 冒烟测试用置顶，避免影响真实桌面
    cfg["hotkeys_enabled"] = False       # 不抢用户的热键
    cfg["missed_summary"] = False
    cfg.save()

    from app.application import Application

    app = Application()
    app.start()
    root = app.root
    store = app.store
    mw = app.main

    def pump(n: int = 3) -> None:
        for _ in range(n):
            root.update_idletasks()
            root.update()

    try:
        print("\n[1] 快速添加 + 自然语言解析")
        before = len(store.tasks)
        app.quick_add("明天18:00 交暑假作业 !高 #学习")
        pump()
        check("任务数量 +1", len(store.tasks) == before + 1)
        t = store.tasks[-1]
        check("标题解析正确", t.title == "交暑假作业", t.title)
        check("优先级解析为 高", t.priority == 3, str(t.priority))
        check("日期解析为明天 18:00",
              t.due_dt is not None and t.due_dt.date() == (datetime.now() + timedelta(days=1)).date()
              and t.due_dt.hour == 18, str(t.due))
        check("自动创建了分类「学习」", store.list_name(t.list_id) == "学习", store.list_name(t.list_id))

        print("\n[2] 列表与视图")
        pump()
        # 明天到期的任务应出现在「本周」，而「今天」按设计只装今天到期和逾期的
        check("本周视图能拿到该任务", any(x.id == t.id for x in store.view("week")))
        check("今天视图不含明天的任务", not any(x.id == t.id for x in store.view("today")))
        mw.view = "week"
        mw.refresh_list()
        pump()
        items = mw.list_canvas.find_all()
        check("列表画布有内容", len(items) >= 4, f"{len(items)} 个图元")
        check("画出了该任务的复选框",
              bool(mw.list_canvas.find_withtag(f"chk:{t.id}")), "找不到 chk 标签")
        check("画出了该任务的标题",
              bool(mw.list_canvas.find_withtag(f"title:{t.id}")), "找不到 title 标签")
        check("画布滚动区域已设置",
              mw.list_canvas.cget("scrollregion") not in ("", "0 0 0 0"),
              str(mw.list_canvas.cget("scrollregion")))

        print("\n[3] 勾选完成")
        app.toggle_task(t)
        pump()
        check("任务标记为完成", store.get(t.id).done)
        check("完成视图能看到它",
              any(x.id == t.id for x in store.view("done")))
        c = store.counts()
        check("今日完成计数 = 1", c["done_today"] == 1, str(c))

        print("\n[4] 重复任务生成下一次")
        rep = app.quick_add("每天21:00 背单词")
        rep.repeat = {"freq": "daily", "interval": 1}
        store.update(rep)
        app.toggle_task(rep)
        pump()
        follow = [x for x in store.tasks if x.series_id == rep.id and not x.done]
        check("生成了下一次任务", len(follow) == 1, str(len(follow)))
        if follow:
            check("下一次在未来", follow[0].due_dt > datetime.now(), str(follow[0].due))

        print("\n[5] 撤销完成会同时撤销生成的下一次")
        app.toggle_task(store.get(rep.id))
        pump()
        left = [x for x in store.tasks
                if x.series_id == rep.id and x.id != rep.id and not x.done]
        check("重复链清干净了", len(left) == 0, str(len(left)))
        check("原任务本身恢复为未完成", not store.get(rep.id).done)

        print("\n[6] 延期")
        d = app.quick_add("买菜")
        store.postpone(d.id, minutes=10)
        pump()
        check("期限被推后", store.get(d.id).due_dt > datetime.now())
        check("延期计数 +1", store.get(d.id).postpone_count == 1)

        print("\n[7] 搜索")
        found = store.view("all", search="暑假")
        check("搜到了目标任务", len(found) == 1 and "暑假" in found[0].title, str([x.title for x in found]))

        print("\n[8] 视图切换")
        for view in ("today", "week", "all", "quadrant", "done"):
            mw.view = view
            mw.refresh_list()
            pump(2)
        check("五个视图切换都没抛异常", True)

        print("\n[9] 编辑器保存")
        from app.ui.editor import TaskEditor
        ed = TaskEditor(app, None)
        pump()
        ed.title_var.set("编辑器创建的任务")
        ed._priority = "2"
        ed._due_date = datetime.now().date() + timedelta(days=3)
        ed.time_var.set("14:30")
        ed._repeat = "weekly"
        ed.save()
        pump()
        made = [x for x in store.tasks if x.title == "编辑器创建的任务"]
        check("编辑器保存出新任务", len(made) == 1, str(len(made)))
        if made:
            check("优先级保存正确", made[0].priority == 2, str(made[0].priority))
            check("期限保存正确",
                  made[0].due_dt is not None and made[0].due_dt.hour == 14 and made[0].due_dt.minute == 30,
                  str(made[0].due))
            check("重复规则保存正确", made[0].repeat == {"freq": "weekly", "interval": 1},
                  str(made[0].repeat))

        print("\n[10] 编辑器必填校验")
        ed2 = TaskEditor(app, None)
        pump()
        ed2.title_var.set("   ")
        ed2.save()
        pump()
        check("空标题不会创建任务", ed2.result is None)
        ed2.close()
        pump()

        print("\n[11] 删除")
        n = len(store.tasks)
        app.config["confirm_delete"] = False
        app.delete_task(made[0])
        pump()
        check("删除生效", len(store.tasks) == n - 1, f"{n} -> {len(store.tasks)}")

        print("\n[12] 数据落盘与备份")
        store.save()
        data = (Path(TMP_HOME) / "data.json")
        check("data.json 存在", data.exists())
        store.backup_daily()
        check("备份目录有文件", len(list((Path(TMP_HOME) / "backups").glob("data-*.json"))) >= 1)

        print("\n[13] 贴桌面状态")
        check("贴桌面模式已应用", app.pinner is not None and app.pinner.status != "未初始化",
              app.pinner.status if app.pinner else "None")

        print("\n[14] 提醒调度")
        r = app.quick_add("该提醒了")
        r.due = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M")
        store.update(r)
        due = store.due_for_reminder()
        check("到期任务被调度器发现", any(x.id == r.id for x in due), str([x.title for x in due]))
        app._check_reminders_inner()
        pump()
        check("已提醒的任务不会重复弹", store.get(r.id).notified_at is not None)

    except Exception as exc:
        import traceback
        traceback.print_exc()
        FAIL.append(f"异常: {exc}")
    finally:
        try:
            app.store.save()
            app.service.remove_tray_icon()
            app.service.stop()
            root.destroy()
        except Exception:
            pass

    print(f"\n{'=' * 46}")
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
