"""数据结构：任务、子任务、重复规则。"""

from __future__ import annotations

import calendar
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW, PRIORITY_NONE = 3, 2, 1, 0
PRIORITY_LABEL = {3: "高", 2: "中", 1: "低", 0: "无"}
PRIORITY_ORDER = {3: 0, 2: 1, 1: 2, 0: 3}

ISO = "%Y-%m-%dT%H:%M"


def now_iso() -> str:
    return datetime.now().strftime(ISO)


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, ISO)
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


def dt_to_iso(value: Optional[datetime]) -> Optional[str]:
    return value.strftime(ISO) if value else None


def add_months(dt: datetime, months: int) -> datetime:
    """按月推进，遇到 31 号而目标月只有 30 天时夹到月末。"""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


REPEAT_LABEL = {
    "daily": "每天",
    "weekdays": "工作日",
    "weekly": "每周",
    "monthly": "每月",
    "yearly": "每年",
    "interval": "自定义",
}

WEEKDAY_LABEL = "一二三四五六日"


def repeat_summary(rule: Optional[dict[str, Any]]) -> str:
    if not rule:
        return ""
    freq = rule.get("freq", "")
    if freq == "interval":
        n = int(rule.get("interval") or 1)
        unit = rule.get("unit", "day")
        unit_cn = {"day": "天", "week": "周", "month": "个月", "year": "年"}.get(unit, "天")
        return f"每 {n} {unit_cn}"
    if freq == "weekly":
        days = rule.get("weekdays") or []
        if days:
            return "每周 " + "、".join("周" + WEEKDAY_LABEL[d % 7] for d in sorted(days))
    return REPEAT_LABEL.get(freq, "")


def advance(dt: datetime, rule: dict[str, Any]) -> datetime:
    """按重复规则算出下一次时间。"""
    freq = rule.get("freq", "daily")
    interval = max(1, int(rule.get("interval") or 1))
    if freq == "daily":
        return dt + timedelta(days=interval)
    if freq == "weekdays":
        nxt = dt
        for _ in range(interval):
            nxt += timedelta(days=1)
            while nxt.weekday() >= 5:
                nxt += timedelta(days=1)
        return nxt
    if freq == "weekly":
        days = sorted(rule.get("weekdays") or [])
        if days:
            # 找下一个命中的星期几
            for step in range(1, 15):
                cand = dt + timedelta(days=step)
                if cand.weekday() in days:
                    return cand
            return dt + timedelta(days=7 * interval)
        return dt + timedelta(weeks=interval)
    if freq == "monthly":
        return add_months(dt, interval)
    if freq == "yearly":
        return dt.replace(year=dt.year + interval)
    if freq == "interval":
        unit = rule.get("unit", "day")
        if unit == "day":
            return dt + timedelta(days=interval)
        if unit == "week":
            return dt + timedelta(weeks=interval)
        if unit == "month":
            return add_months(dt, interval)
        if unit == "year":
            return dt.replace(year=dt.year + interval)
    return dt + timedelta(days=1)


def next_occurrence(previous_due: datetime, rule: dict[str, Any], now: Optional[datetime] = None) -> datetime:
    """算出下一次期限。若已错过多次（关机多天），一直推进到不早于 now，

    但保持原本的时分，避免补出一堆过期任务。
    """
    now = now or datetime.now()
    nxt = advance(previous_due, rule)
    guard = 0
    while nxt <= now and guard < 500:
        nxt = advance(nxt, rule)
        guard += 1
    return nxt


@dataclass
class Subtask:
    id: str
    title: str
    done: bool = False

    @staticmethod
    def new(title: str) -> "Subtask":
        return Subtask(id=uuid.uuid4().hex[:8], title=title)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "done": self.done}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Subtask":
        return Subtask(id=d.get("id") or uuid.uuid4().hex[:8],
                       title=d.get("title", ""),
                       done=bool(d.get("done")))


@dataclass
class Task:
    id: str
    title: str
    note: str = ""
    priority: int = PRIORITY_NONE
    due: Optional[str] = None            # "2026-10-06T18:00"
    remind_at: Optional[str] = None      # 实际提醒时刻；为空则用 due
    list_id: str = "inbox"
    repeat: Optional[dict[str, Any]] = None
    subtasks: list[Subtask] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    completed_at: Optional[str] = None
    archived: bool = False
    order: float = 0.0
    notified_at: Optional[str] = None     # 已提醒过的时间，避免重复弹窗
    postpone_count: int = 0               # 延期次数（用于统计和"总是拖延"提示）
    series_id: Optional[str] = None       # 重复任务属于同一条链

    # ---------- 构造 ----------
    @staticmethod
    def new(title: str, **kwargs: Any) -> "Task":
        ts = now_iso()
        return Task(id=uuid.uuid4().hex[:12], title=title, created_at=ts, updated_at=ts, **kwargs)

    # ---------- 属性 ----------
    @property
    def due_dt(self) -> Optional[datetime]:
        return parse_dt(self.due)

    @property
    def remind_dt(self) -> Optional[datetime]:
        return parse_dt(self.remind_at) or self.due_dt

    @property
    def done(self) -> bool:
        return self.completed_at is not None

    @property
    def priority_label(self) -> str:
        return PRIORITY_LABEL.get(self.priority, "无")

    @property
    def repeat_label(self) -> str:
        return repeat_summary(self.repeat)

    def set_due(self, value: Optional[datetime], keep_time: bool = True) -> None:
        self.due = dt_to_iso(value)
        if value is not None:
            self.remind_at = None  # 重置，让提醒跟随新的期限

    def is_overdue(self, now: Optional[datetime] = None) -> bool:
        if self.done or not self.due_dt:
            return False
        return self.due_dt < (now or datetime.now())

    def is_due_today(self, now: Optional[datetime] = None) -> bool:
        if not self.due_dt:
            return False
        return self.due_dt.date() == (now or datetime.now()).date()

    def is_due_this_week(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now()
        if not self.due_dt:
            return False
        start = now.date() - timedelta(days=now.weekday())
        end = start + timedelta(days=6)
        return start <= self.due_dt.date() <= end

    def sort_key(self) -> tuple:
        """列表排序：未完成在前 -> 逾期在前 -> 优先级 -> 期限 -> 手工顺序。"""
        overdue = 0 if self.is_overdue() else 1
        has_due = 0 if self.due_dt else 1
        due_ts = self.due_dt.timestamp() if self.due_dt else float("inf")
        return (1 if self.done else 0,
                overdue,
                has_due,
                PRIORITY_ORDER.get(self.priority, 3),
                due_ts,
                self.order,
                self.created_at)

    # ---------- 序列化 ----------
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "note": self.note,
            "priority": self.priority,
            "due": self.due,
            "remind_at": self.remind_at,
            "list_id": self.list_id,
            "repeat": self.repeat,
            "subtasks": [s.to_dict() for s in self.subtasks],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "archived": self.archived,
            "order": self.order,
            "notified_at": self.notified_at,
            "postpone_count": self.postpone_count,
            "series_id": self.series_id,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Task":
        return Task(
            id=d.get("id") or uuid.uuid4().hex[:12],
            title=d.get("title", ""),
            note=d.get("note", ""),
            priority=int(d.get("priority") or 0),
            due=d.get("due"),
            remind_at=d.get("remind_at"),
            list_id=d.get("list_id") or "inbox",
            repeat=d.get("repeat"),
            subtasks=[Subtask.from_dict(s) for s in (d.get("subtasks") or [])],
            created_at=d.get("created_at") or now_iso(),
            updated_at=d.get("updated_at") or now_iso(),
            completed_at=d.get("completed_at"),
            archived=bool(d.get("archived")),
            order=float(d.get("order") or 0.0),
            notified_at=d.get("notified_at"),
            postpone_count=int(d.get("postpone_count") or 0),
            series_id=d.get("series_id"),
        )


DEFAULT_LISTS: list[dict[str, Any]] = [
    {"id": "inbox", "name": "收集箱", "color": "#6B7280"},
    {"id": "work", "name": "工作", "color": "#3B82F6"},
    {"id": "study", "name": "学习", "color": "#8B5CF6"},
    {"id": "life", "name": "生活", "color": "#10B981"},
]


@dataclass
class TaskList:
    id: str
    name: str
    color: str = "#6B7280"

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "color": self.color}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "TaskList":
        return TaskList(id=d.get("id") or uuid.uuid4().hex[:8],
                        name=d.get("name", "新清单"),
                        color=d.get("color", "#6B7280"))
