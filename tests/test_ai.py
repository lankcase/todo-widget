"""AI 返回内容解析的测试（不联网，只测解析健壮性）。"""

import unittest
from datetime import datetime, timedelta

from app.ai import ZhipuAI, _extract_json, _normalize_due


class TestJsonExtraction(unittest.TestCase):
    def test_plain_json(self):
        data = _extract_json('{"tasks": [{"title": "交作业"}]}')
        self.assertEqual(data["tasks"][0]["title"], "交作业")

    def test_code_fence(self):
        raw = '```json\n{"tasks": [{"title": "交作业"}]}\n```'
        self.assertEqual(_extract_json(raw)["tasks"][0]["title"], "交作业")

    def test_json_with_chatter(self):
        raw = '好的，我整理了一下：\n{"tasks": [{"title": "交作业"}]}\n希望有帮助！'
        self.assertEqual(_extract_json(raw)["tasks"][0]["title"], "交作业")

    def test_garbage(self):
        self.assertIsNone(_extract_json("今天天气不错"))


class TestNormalizeDue(unittest.TestCase):
    def test_empty(self):
        for value in (None, "", "null", "无", "没有"):
            self.assertIsNone(_normalize_due(value))

    def test_date_only(self):
        self.assertEqual(_normalize_due("2026-10-08"), "2026-10-08")

    def test_datetime_variants(self):
        self.assertEqual(_normalize_due("2026-10-08T18:00"), "2026-10-08T18:00")
        self.assertEqual(_normalize_due("2026-10-08 18:00"), "2026-10-08T18:00")
        self.assertEqual(_normalize_due("2026-10-08 18:00:00"), "2026-10-08T18:00")
        self.assertEqual(_normalize_due("2026/10/08 18:00"), "2026-10-08T18:00")

    def test_relative_md_without_year_rolls_forward(self):
        """模型只给了月日时，补上今年的年份；若已过去则算明年。"""
        yesterday = datetime.now() - timedelta(days=3)
        got = _normalize_due(yesterday.strftime("%m-%d %H:%M"))
        self.assertIsNotNone(got)
        self.assertEqual(datetime.strptime(got, "%Y-%m-%dT%H:%M").year, datetime.now().year + 1)

    def test_natural_language_fallback(self):
        got = _normalize_due("明天下午三点")
        self.assertIsNotNone(got)
        self.assertIn("T15:00", got)


class TestParseTasks(unittest.TestCase):
    def test_standard(self):
        raw = ('{"tasks": ['
               '{"title": "交暑假作业", "priority": "高", "due": "2026-10-07T18:00", "category": "学习"},'
               '{"title": "买牛奶", "priority": "低", "due": null, "category": null}'
               ']}')
        tasks = ZhipuAI.parse_tasks(raw)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["title"], "交暑假作业")
        self.assertEqual(tasks[0]["priority"], 3)
        self.assertEqual(tasks[0]["due"], "2026-10-07T18:00")
        self.assertEqual(tasks[0]["category"], "学习")
        self.assertIsNone(tasks[1]["due"])
        self.assertEqual(tasks[1]["priority"], 1)

    def test_numeric_priority(self):
        tasks = ZhipuAI.parse_tasks('{"tasks": [{"title": "x", "priority": 2}]}')
        self.assertEqual(tasks[0]["priority"], 2)

    def test_out_of_range_priority_clamped(self):
        tasks = ZhipuAI.parse_tasks('{"tasks": [{"title": "x", "priority": 99}]}')
        self.assertEqual(tasks[0]["priority"], 3)

    def test_bare_list(self):
        tasks = ZhipuAI.parse_tasks('[{"title": "a"}, {"title": "b"}]')
        self.assertEqual([t["title"] for t in tasks], ["a", "b"])

    def test_strings_in_list(self):
        tasks = ZhipuAI.parse_tasks('{"tasks": ["交作业", "买菜"]}')
        self.assertEqual([t["title"] for t in tasks], ["交作业", "买菜"])

    def test_fallback_to_lines_when_not_json(self):
        """模型不按格式返回时，按行兜底，不能丢内容。"""
        raw = "1. 交暑假作业 明天18:00\n2. 给妈妈打电话\n3. 买牛奶"
        tasks = ZhipuAI.parse_tasks(raw)
        self.assertEqual(len(tasks), 3)
        self.assertIn("交暑假作业", tasks[0]["title"])
        self.assertIsNotNone(tasks[0]["due"])   # 行内的时间也要解析出来

    def test_skips_empty_titles(self):
        tasks = ZhipuAI.parse_tasks('{"tasks": [{"title": ""}, {"title": "  "}, {"title": "好的"}]}')
        self.assertEqual([t["title"] for t in tasks], ["好的"])

    def test_caps_at_twenty(self):
        raw = '{"tasks": [' + ",".join(f'{{"title": "任务{i}"}}' for i in range(40)) + ']}'
        self.assertLessEqual(len(ZhipuAI.parse_tasks(raw)), 20)

    def test_alternate_keys(self):
        """模型偶尔会用 task/list 这类别名。"""
        tasks = ZhipuAI.parse_tasks('{"items": [{"task": "倒垃圾", "deadline": "2026-10-08", "list": "生活"}]}')
        self.assertEqual(tasks[0]["title"], "倒垃圾")
        self.assertEqual(tasks[0]["category"], "生活")
        self.assertEqual(tasks[0]["due"], "2026-10-08")

    def test_empty_input(self):
        self.assertEqual(ZhipuAI.parse_tasks(""), [])
        self.assertEqual(ZhipuAI.parse_tasks("{}"), [])


class TestClientConfig(unittest.TestCase):
    def test_not_configured(self):
        client = ZhipuAI("")
        self.assertFalse(client.configured)

    def test_base_url_normalized(self):
        client = ZhipuAI("k", base_url="https://open.bigmodel.cn/api/paas/v4")
        self.assertTrue(client.base_url.endswith("/"))

    def test_hotwords_parsed(self):
        client = ZhipuAI("k", hotwords="二次根式, 班会 ，体检")
        self.assertEqual(client.hotwords, ["二次根式", "班会", "体检"])

    def test_defaults(self):
        client = ZhipuAI("k")
        self.assertEqual(client.asr_model, "glm-asr-2512")
        self.assertEqual(client.chat_model, "glm-4.7-flash")


if __name__ == "__main__":
    unittest.main()
