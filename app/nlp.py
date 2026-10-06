"""自然语言快速录入解析。

把 "明天18:00 交暑假作业 !高 #学习" 解析成
（标题="交暑假作业", 期限=明天18:00, 优先级=高, 分类="学习"）

规则刻意保持可预测：只认明确的模式，认不出就原样当标题，绝不猜错时间。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW, PRIORITY_NONE = 3, 2, 1, 0

# 优先级写法 -> 数值
_PRIORITY_WORDS = {
    "高": PRIORITY_HIGH, "h": PRIORITY_HIGH, "high": PRIORITY_HIGH, "1": PRIORITY_HIGH, "！": PRIORITY_HIGH,
    "中": PRIORITY_MEDIUM, "m": PRIORITY_MEDIUM, "mid": PRIORITY_MEDIUM, "2": PRIORITY_MEDIUM,
    "低": PRIORITY_LOW, "l": PRIORITY_LOW, "low": PRIORITY_LOW, "3": PRIORITY_LOW,
    "无": PRIORITY_NONE, "n": PRIORITY_NONE, "0": PRIORITY_NONE,
}

_CN_NUM = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "十一": 11, "十二": 12,
}

# 时段默认小时
_PERIOD_HOUR = {
    "凌晨": 5, "早上": 8, "早晨": 8, "上午": 9, "中午": 12, "下午": 15,
    "傍晚": 18, "晚上": 20, "今晚": 20, "夜里": 22, "明早": 8,
}

_WEEKDAY_CN = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


@dataclass
class ParsedInput:
    title: str
    due: Optional[datetime] = None
    priority: Optional[int] = None
    list_name: Optional[str] = None
    matched: list[str] = field(default_factory=list)

    @property
    def has_meta(self) -> bool:
        return self.due is not None or self.priority is not None or self.list_name is not None


def _to_int(text: str) -> Optional[int]:
    if text.isdigit():
        return int(text)
    if text in _CN_NUM:
        return _CN_NUM[text]
    return None


def _apply_period(hour: int, period: Optional[str]) -> int:
    """把 '下午3点' 的 3 修正为 15。"""
    if not period:
        return hour
    if period in ("下午", "傍晚", "晚上", "今晚", "夜里") and 1 <= hour < 12:
        return hour + 12
    if period == "中午" and hour < 12 and hour != 12:
        return 12
    if period == "凌晨" and hour == 12:
        return 0
    return hour


def parse_when(text: str, now: Optional[datetime] = None) -> tuple[Optional[datetime], str, list[str]]:
    """解析日期/时间部分。返回 (时间, 剩余文本, 命中片段)。"""
    now = now or datetime.now()
    hits: list[str] = []
    rest = text
    base_date: Optional[datetime] = None
    hour: Optional[int] = None
    minute: Optional[int] = None
    period: Optional[str] = None
    explicit_time = False

    def take(pattern: str, flags: int = 0):
        """在 rest 中查找并摘除第一个匹配，返回 match 或 None。"""
        nonlocal rest
        m = re.search(pattern, rest, flags)
        if m:
            rest = (rest[: m.start()] + " " + rest[m.end():]).strip()
            hits.append(m.group(0))
        return m

    # 1) 绝对日期 2026-10-08 / 2026年10月8日
    m = take(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?")
    if m:
        try:
            base_date = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            base_date = None

    # 2) 月日 10月8日 / 10-8 / 10/8 （避免误吃 18:00 这类时间）
    if base_date is None:
        m = take(r"(?<!\d)(\d{1,2})\s*[月/-]\s*(\d{1,2})\s*日?(?!\d)(?!:)")
        if m:
            mm, dd = int(m.group(1)), int(m.group(2))
            try:
                cand = datetime(now.year, mm, dd)
                if cand.date() < now.date() - timedelta(days=1):
                    cand = datetime(now.year + 1, mm, dd)  # 已过去则视为明年
                base_date = cand
            except ValueError:
                base_date = None

    # 3) 相对天数 / 周几 / 本周下周
    if base_date is None:
        m = take(r"(\d{1,3}|[一二两三四五六七八九十]+)\s*天\s*(?:之?后|以后|后)")
        if m:
            n = _to_int(m.group(1))
            if n is not None:
                base_date = now + timedelta(days=n)

    if base_date is None:
        m = take(r"(下{1,2}|本|这)?\s*(?:周|星期|礼拜)\s*([一二三四五六日天])")
        if m:
            offset_weeks = {"下": 1, "下下": 2, "本": 0, "这": 0, None: 0}.get(m.group(1), 0)
            target = _WEEKDAY_CN[m.group(2)]
            cur = now.weekday()
            delta = target - cur
            if offset_weeks == 0 and delta < 0:
                delta += 7                      # 本周已过 -> 下周同一天
            base_date = now + timedelta(days=delta + 7 * offset_weeks)

    if base_date is None:
        m = take(r"(\d{1,2})\s*(?:个)?\s*(小时|钟头)\s*(?:之?后|以后|后)")
        if m:
            n = _to_int(m.group(1)) or 0
            return now + timedelta(hours=n), rest, hits

    if base_date is None:
        m = take(r"(\d{1,3}|[一二两三四五六七八九十]+)\s*分钟\s*(?:之?后|以后|后)")
        if m:
            n = _to_int(m.group(1)) or 0
            return now + timedelta(minutes=n), rest, hits

    # 4) 相对日：今天/明天/后天/大后天
    if base_date is None:
        m = take(r"(大后天|后天|明天|明日|今天|今日|今晚|明早|今晚)")
        if m:
            word = m.group(1)
            if word in ("今天", "今日"):
                base_date = now
            elif word in ("明天", "明日"):
                base_date = now + timedelta(days=1)
            elif word == "后天":
                base_date = now + timedelta(days=2)
            elif word == "大后天":
                base_date = now + timedelta(days=3)
            elif word == "今晚":
                base_date = now
                period = "今晚"
            elif word == "明早":
                base_date = now + timedelta(days=1)
                period = "明早"

    # 5) 时段词（下午/晚上…），可能是 下午3点，也可能只是 下午
    if period is None:
        m = take(r"(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|夜里)")
        if m:
            period = m.group(1)

    # 6) 时间 HH:MM / H点M分 / H点半
    m = take(r"(?<!\d)(\d{1,2})\s*[:：]\s*(\d{1,2})(?!\d)")
    if m:
        hour, minute, explicit_time = int(m.group(1)), int(m.group(2)), True
    else:
        m = take(r"(?<!\d)(\d{1,2})\s*点\s*(半|一刻|三刻|(\d{1,2})\s*分?)?")
        if m:
            hour, explicit_time = int(m.group(1)), True
            if m.group(2) == "半":
                minute = 30
            elif m.group(2) == "一刻":
                minute = 15
            elif m.group(2) == "三刻":
                minute = 45
            elif m.group(3):
                minute = int(m.group(3))

    if hour is not None:
        hour = _apply_period(hour % 24, period)
        minute = minute or 0
    elif period is not None and base_date is not None:
        # 只说了"明天下午"这种，给个该时段的默认点
        hour, minute = _PERIOD_HOUR.get(period, 9), 0
    elif period is not None and base_date is None:
        # 只有"下午"，视为今天的下午默认时间
        hour, minute = _PERIOD_HOUR.get(period, 9), 0
        base_date = now

    if base_date is None and hour is None:
        return None, rest, hits

    if base_date is None:
        # 只有时间没日期：今天的该时刻已过则顺延到明天
        candidate = now.replace(hour=hour, minute=minute or 0, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate, rest, hits

    result = base_date.replace(hour=hour if hour is not None else 9,
                               minute=(minute or 0) if hour is not None else 0,
                               second=0, microsecond=0)
    if not explicit_time and hour is None:
        result = result.replace(hour=9, minute=0)
    return result, rest, hits


def parse(text: str, now: Optional[datetime] = None) -> ParsedInput:
    """解析一行快速录入文本。"""
    now = now or datetime.now()
    work = text.strip()
    priority: Optional[int] = None
    list_name: Optional[str] = None

    # 优先级：!高 / !h / !1 / !high —— 长单词放在前面，避免 !high 只吃掉 h
    m = re.search(r"[!！]\s*(high|mid|low|[高中低无hmn0-3])", work, re.IGNORECASE)
    if m:
        key = m.group(1).lower()
        if key in _PRIORITY_WORDS:
            priority = _PRIORITY_WORDS[key]
            work = (work[: m.start()] + " " + work[m.end():]).strip()

    # 分类：#学习
    m = re.search(r"[#＃]\s*([^\s#＃!！]+)", work)
    if m:
        list_name = m.group(1).strip()
        work = (work[: m.start()] + " " + work[m.end():]).strip()

    due, remaining, _ = parse_when(work, now=now)
    title = re.sub(r"\s{2,}", " ", remaining).strip(" -—,，。;；")
    if not title:
        title = text.strip()
    return ParsedInput(title=title, due=due, priority=priority, list_name=list_name)


def humanize_due(due: Optional[datetime], now: Optional[datetime] = None) -> str:
    """把期限显示成 '今天 18:00' / '明天' / '逾期 2 天' 这类短文本。"""
    if due is None:
        return ""
    now = now or datetime.now()
    delta_days = (due.date() - now.date()).days
    has_time = not (due.hour == 0 and due.minute == 0)
    if delta_days == 0:
        head = "今天"
    elif delta_days == 1:
        head = "明天"
    elif delta_days == 2:
        head = "后天"
    elif delta_days == -1:
        head = "昨天"
    elif delta_days < 0:
        return f"逾期 {abs(delta_days)} 天"
    elif delta_days < 7:
        head = "周" + "一二三四五六日"[due.weekday()]
    else:
        head = f"{due.month}月{due.day}日"
    if has_time:
        return f"{head} {due.hour:02d}:{due.minute:02d}"
    return head
