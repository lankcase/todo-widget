"""自然语言录入解析的测试。所有断言都固定一个"现在"，避免依赖真实时间。"""

import unittest
from datetime import datetime

from app import nlp

NOW = datetime(2026, 10, 6, 10, 0)  # 2026-10-06 是周二


class TestParseWhen(unittest.TestCase):
    def assertDue(self, text, expected):
        p = nlp.parse(text, now=NOW)
        self.assertIsNotNone(p.due, f"{text!r} 没有解析出时间")
        self.assertEqual(p.due, expected, f"{text!r} -> {p.due}")

    def test_relative_days(self):
        self.assertDue("明天18:00 交作业", datetime(2026, 10, 7, 18, 0))
        self.assertDue("今天 18:00 交作业", datetime(2026, 10, 6, 18, 0))
        self.assertDue("后天 9点 复查", datetime(2026, 10, 8, 9, 0))
        self.assertDue("大后天 开会", datetime(2026, 10, 9, 9, 0))
        self.assertDue("3天后 回访", datetime(2026, 10, 9, 9, 0))

    def test_weekday(self):
        self.assertDue("周五 交周报", datetime(2026, 10, 9, 9, 0))
        self.assertDue("星期五 交周报", datetime(2026, 10, 9, 9, 0))
        self.assertDue("下周一 开例会", datetime(2026, 10, 12, 9, 0))
        self.assertDue("周日 休息", datetime(2026, 10, 11, 9, 0))

    def test_absolute_date(self):
        self.assertDue("10月8日 考试", datetime(2026, 10, 8, 9, 0))
        self.assertDue("10-08 考试", datetime(2026, 10, 8, 9, 0))
        self.assertDue("2026-12-31 年终总结", datetime(2026, 12, 31, 9, 0))

    def test_time_only(self):
        # 今天该时刻还没到 -> 今天
        self.assertDue("18:00 交作业", datetime(2026, 10, 6, 18, 0))
        # 已经过了 -> 顺延到明天
        self.assertDue("9:00 交作业", datetime(2026, 10, 7, 9, 0))

    def test_period_shift(self):
        self.assertDue("今晚8点 写作业", datetime(2026, 10, 6, 20, 0))
        self.assertDue("下午3点半 开会", datetime(2026, 10, 6, 15, 30))
        self.assertDue("下午3点15分 开会", datetime(2026, 10, 6, 15, 15))
        self.assertDue("早上7点 跑步", datetime(2026, 10, 7, 7, 0))  # 今天7点已过 -> 明天早上
        self.assertDue("中午 吃饭", datetime(2026, 10, 6, 12, 0))
        self.assertDue("明早 跑步", datetime(2026, 10, 7, 8, 0))

    def test_relative_hours_minutes(self):
        p = nlp.parse("30分钟后 喝水", now=NOW)
        self.assertEqual(p.due, datetime(2026, 10, 6, 10, 30))
        p = nlp.parse("2小时后 取快递", now=NOW)
        self.assertEqual(p.due, datetime(2026, 10, 6, 12, 0))

    def test_no_time(self):
        p = nlp.parse("买菜", now=NOW)
        self.assertIsNone(p.due)
        self.assertEqual(p.title, "买菜")
        self.assertFalse(p.has_meta)


class TestParseMeta(unittest.TestCase):
    def test_priority(self):
        for token, value in [("!高", nlp.PRIORITY_HIGH), ("!h", nlp.PRIORITY_HIGH),
                             ("!high", nlp.PRIORITY_HIGH), ("!1", nlp.PRIORITY_HIGH),
                             ("!中", nlp.PRIORITY_MEDIUM), ("!低", nlp.PRIORITY_LOW),
                             ("!low", nlp.PRIORITY_LOW)]:
            p = nlp.parse(f"{token} 写作业", now=NOW)
            self.assertEqual(p.priority, value, f"{token} 解析错误")
            self.assertEqual(p.title, "写作业", f"{token} 残留到标题里了")

    def test_list_tag(self):
        p = nlp.parse("#学习 写作业", now=NOW)
        self.assertEqual(p.list_name, "学习")
        self.assertEqual(p.title, "写作业")

    def test_combined(self):
        p = nlp.parse("明天18:00 交暑假作业 !高 #学习", now=NOW)
        self.assertEqual(p.title, "交暑假作业")
        self.assertEqual(p.due, datetime(2026, 10, 7, 18, 0))
        self.assertEqual(p.priority, nlp.PRIORITY_HIGH)
        self.assertEqual(p.list_name, "学习")

    def test_title_not_mangled(self):
        """不含任何标记时，标题必须原样保留。"""
        for text in ["给妈妈打电话", "复习数学第三章", "买牛奶和面包"]:
            self.assertEqual(nlp.parse(text, now=NOW).title, text)

    def test_ambiguous_number_is_not_a_time(self):
        """'第3章' 不能被当成时间。"""
        p = nlp.parse("复习第3章", now=NOW)
        self.assertIsNone(p.due)
        self.assertEqual(p.title, "复习第3章")


class TestHumanize(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(nlp.humanize_due(datetime(2026, 10, 6, 18, 0), now=NOW), "今天 18:00")
        self.assertEqual(nlp.humanize_due(datetime(2026, 10, 7, 0, 0), now=NOW), "明天")
        self.assertEqual(nlp.humanize_due(datetime(2026, 10, 9, 9, 0), now=NOW), "周五 09:00")
        self.assertEqual(nlp.humanize_due(datetime(2026, 10, 4, 9, 0), now=NOW), "逾期 2 天")
        self.assertEqual(nlp.humanize_due(None, now=NOW), "")


if __name__ == "__main__":
    unittest.main()
