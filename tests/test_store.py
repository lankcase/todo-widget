"""存储层测试：原子写、备份、重复任务推进、视图过滤、统计。"""

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.models import Task, dt_to_iso, now_iso, next_occurrence
from app.store import Store


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="todowidget-test-"))
        self.store = Store(self.tmp / "data.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestPersistence(StoreTestCase):
    def test_roundtrip(self):
        t = Task.new("交作业", priority=3, list_id="study")
        t.due = "2026-10-07T18:00"
        self.store.add(t)
        self.store.save()

        again = Store(self.tmp / "data.json")
        self.assertEqual(len(again.tasks), 1)
        self.assertEqual(again.tasks[0].title, "交作业")
        self.assertEqual(again.tasks[0].priority, 3)
        self.assertEqual(again.tasks[0].due, "2026-10-07T18:00")

    def test_atomic_write_leaves_valid_json(self):
        for i in range(20):
            self.store.add(Task.new(f"任务{i}"), save=False)
            self.store.save()
        raw = json.loads((self.tmp / "data.json").read_text(encoding="utf-8"))
        self.assertEqual(len(raw["tasks"]), 20)
        # 临时文件不应残留
        self.assertEqual(list(self.tmp.glob("*.tmp")), [])

    def test_corrupt_file_recovers_from_backup(self):
        self.store.add(Task.new("重要任务"))
        self.store.save()
        self.store.backup_daily(force=True)
        (self.tmp / "data.json").write_text("{坏掉的 json", encoding="utf-8")
        recovered = Store(self.tmp / "data.json")
        self.assertEqual([t.title for t in recovered.tasks], ["重要任务"])

    def test_backup_keeps_seven(self):
        self.store.add(Task.new("x"))
        self.store.save()
        bdir = self.tmp / "backups"
        bdir.mkdir(exist_ok=True)
        for i in range(12):
            (bdir / f"data-2026-09-{i + 1:02d}.json").write_text("{}", encoding="utf-8")
        self.store.backup_daily()
        self.assertLessEqual(len(list(bdir.glob("data-*.json"))), 7)


class TestRepeat(StoreTestCase):
    def test_daily_creates_next(self):
        t = Task.new("喝水", repeat={"freq": "daily", "interval": 1})
        t.due = dt_to_iso(datetime.now().replace(second=0, microsecond=0))
        self.store.add(t)
        nxt = self.store.complete(t.id)
        self.assertIsNotNone(nxt)
        self.assertNotEqual(nxt.id, t.id)
        self.assertEqual(nxt.series_id, t.id)
        self.assertTrue(nxt.due_dt > datetime.now())
        self.assertFalse(nxt.done)

    def test_next_occurrence_catches_up(self):
        """关机多天后，重复任务应推进到未来，而不是补一堆过期任务。"""
        rule = {"freq": "daily", "interval": 1}
        past = datetime.now() - timedelta(days=10)
        nxt = next_occurrence(past, rule)
        self.assertGreater(nxt, datetime.now())
        self.assertLessEqual(nxt - datetime.now(), timedelta(days=1))

    def test_weekdays_skips_weekend(self):
        rule = {"freq": "weekdays", "interval": 1}
        friday = datetime(2026, 10, 9, 9, 0)
        nxt = next_occurrence(friday, rule)
        self.assertLess(nxt.weekday(), 5)

    def test_monthly_clamps_day(self):
        from app.models import add_months
        jan31 = datetime(2026, 1, 31, 9, 0)
        self.assertEqual(add_months(jan31, 1).day, 28 if 2026 % 4 else 29)
        self.assertEqual(add_months(datetime(2026, 3, 31), 1).day, 30)

    def test_complete_then_uncomplete_removes_followup(self):
        t = Task.new("每周总结", repeat={"freq": "weekly", "interval": 1})
        t.due = dt_to_iso(datetime.now())
        self.store.add(t)
        self.store.complete(t.id)
        self.assertEqual(len(self.store.tasks), 2)
        self.store.uncomplete(t.id)
        self.assertEqual(len(self.store.tasks), 1)
        self.assertFalse(self.store.get(t.id).done)

    def test_non_repeat_creates_nothing(self):
        t = Task.new("一次性任务")
        self.store.add(t)
        self.assertIsNone(self.store.complete(t.id))
        self.assertEqual(len(self.store.tasks), 1)


class TestViews(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.now = datetime.now()
        a = Task.new("今天到期", priority=2)
        a.due = dt_to_iso(self.now + timedelta(hours=2))
        b = Task.new("已逾期", priority=1)
        b.due = dt_to_iso(self.now - timedelta(days=2))
        c = Task.new("下个月", priority=1)
        c.due = dt_to_iso(self.now + timedelta(days=30))
        d = Task.new("没期限低优先级", priority=1)
        e = Task.new("没期限高优先级", priority=3)
        f = Task.new("已完成")
        for t in (a, b, c, d, e, f):
            self.store.add(t, save=False)
        self.store.complete(f.id, save=False)
        self.store.save()

    def test_today_view(self):
        titles = [t.title for t in self.store.view("today")]
        self.assertIn("今天到期", titles)
        self.assertIn("已逾期", titles)          # 逾期的必须出现，否则会漏事
        self.assertIn("没期限高优先级", titles)   # 高优先级无期限也提醒
        self.assertNotIn("下个月", titles)
        self.assertNotIn("已完成", titles)
        self.assertNotIn("没期限低优先级", titles)

    def test_week_view(self):
        titles = [t.title for t in self.store.view("week")]
        self.assertIn("今天到期", titles)
        self.assertIn("已逾期", titles)
        self.assertIn("没期限低优先级", titles)   # 本周视图把无期限任务都收进来
        self.assertNotIn("下个月", titles)        # 30 天后的任务不该出现在本周
        self.assertNotIn("已完成", titles)

    def test_done_view(self):
        self.assertEqual([t.title for t in self.store.view("done")], ["已完成"])

    def test_search(self):
        self.assertEqual([t.title for t in self.store.view("all", search="逾期")], ["已逾期"])

    def test_list_filter(self):
        self.store.move_to_list(self.store.tasks[0].id, "work")
        got = self.store.view("all", list_filter="work")
        self.assertEqual(len(got), 1)

    def test_sorting_overdue_first(self):
        titles = [t.title for t in self.store.view("today")]
        self.assertEqual(titles[0], "已逾期", f"逾期任务应排在前面，实际: {titles}")


class TestReminders(StoreTestCase):
    def test_due_for_reminder(self):
        past = Task.new("该提醒了")
        past.due = dt_to_iso(datetime.now() - timedelta(minutes=1))
        future = Task.new("还没到")
        future.due = dt_to_iso(datetime.now() + timedelta(hours=1))
        self.store.add(past, save=False)
        self.store.add(future, save=False)
        due = self.store.due_for_reminder()
        self.assertEqual([t.title for t in due], ["该提醒了"])

        past.notified_at = now_iso()
        self.assertEqual(self.store.due_for_reminder(), [])

    def test_remind_at_override(self):
        t = Task.new("提前提醒")
        t.due = dt_to_iso(datetime.now() + timedelta(hours=1))
        t.remind_at = dt_to_iso(datetime.now() - timedelta(minutes=1))
        self.store.add(t)
        self.assertEqual([x.title for x in self.store.due_for_reminder()], ["提前提醒"])

    def test_postpone_clears_notified(self):
        t = Task.new("延期任务")
        t.due = dt_to_iso(datetime.now() - timedelta(minutes=1))
        t.notified_at = now_iso()
        self.store.add(t)
        self.store.postpone(t.id, minutes=10)
        self.assertIsNone(t.notified_at)
        self.assertEqual(t.postpone_count, 1)
        self.assertTrue(t.due_dt > datetime.now())


class TestStats(StoreTestCase):
    def test_counts_and_streak(self):
        a = Task.new("今天完成1")
        self.store.add(a, save=False)
        self.store.complete(a.id, save=False)
        yield_task = Task.new("今天完成2")
        self.store.add(yield_task, save=False)
        self.store.complete(yield_task.id, save=False)
        old = Task.new("昨天完成")
        old.completed_at = dt_to_iso(datetime.now() - timedelta(days=1))
        self.store.add(old, save=False)
        self.store.save()

        c = self.store.counts()
        self.assertEqual(c["done_today"], 2)
        self.assertEqual(c["active"], 0)
        self.assertEqual(self.store.streak_days(), 2)

    def test_streak_not_broken_by_pending_today(self):
        """今天还没完成，但昨天完成了，连击不该断（给人一点宽容）。"""
        t = Task.new("昨天完成")
        t.completed_at = dt_to_iso(datetime.now() - timedelta(days=1))
        self.store.add(t)
        self.assertEqual(self.store.streak_days(), 1)


class TestImportExport(StoreTestCase):
    def test_json_roundtrip(self):
        for i in range(3):
            self.store.add(Task.new(f"任务{i}"), save=False)
        self.store.save()
        dest = self.tmp / "out.json"
        self.store.export_json(dest)

        other = Store(self.tmp / "other.json")
        added = other.import_json(dest)
        self.assertEqual(added, 3)
        self.assertEqual(len(other.tasks), 3)

    def test_csv_export(self):
        t = Task.new("交作业", priority=3, list_id="study")
        t.due = "2026-10-07T18:00"
        self.store.add(t)
        dest = self.tmp / "out.csv"
        self.store.export_csv(dest)
        text = dest.read_text(encoding="utf-8-sig")
        self.assertIn("交作业", text)
        self.assertIn("高", text)


class TestTaskModel(unittest.TestCase):
    def test_priority_sort(self):
        high = Task.new("高", priority=3)
        low = Task.new("低", priority=1)
        self.assertLess(high.sort_key(), low.sort_key())

    def test_overdue_flag(self):
        t = Task.new("逾期")
        t.due = dt_to_iso(datetime.now() - timedelta(hours=1))
        self.assertTrue(t.is_overdue())
        t.completed_at = now_iso()
        self.assertFalse(t.is_overdue(), "已完成的任务不该算逾期")


if __name__ == "__main__":
    unittest.main()
