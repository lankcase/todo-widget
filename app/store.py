"""任务仓库：JSON 持久化（原子写 + 每日备份）、增删改查、视图过滤、统计。"""

from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import config as cfg_mod
from .models import (
    DEFAULT_LISTS,
    PRIORITY_HIGH,
    PRIORITY_NONE,
    Task,
    TaskList,
    dt_to_iso,
    next_occurrence,
    now_iso,
    parse_dt,
)

BACKUP_KEEP = 7
DATA_VERSION = 1


class Store:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or cfg_mod.data_file()
        self.lock = threading.RLock()
        self.tasks: list[Task] = []
        self.lists: list[TaskList] = []
        self.meta: dict[str, Any] = {"version": DATA_VERSION, "stats": {}}
        self._listeners: list[Callable[[], None]] = []
        self.load()

    # ---------------- 监听（UI 刷新） ----------------
    def subscribe(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def notify(self) -> None:
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                pass

    # ---------------- 读写 ----------------
    def load(self) -> None:
        with self.lock:
            raw: dict[str, Any] = {}
            if self.path.exists():
                try:
                    text = self.path.read_text(encoding="utf-8")
                    raw = json.loads(text) if text.strip() else {}
                except Exception:
                    raw = self._recover_from_backup()
            self.meta = raw.get("meta") or {"version": DATA_VERSION, "stats": {}}
            self.tasks = [Task.from_dict(d) for d in (raw.get("tasks") or []) if d.get("title") is not None]
            lists = [TaskList.from_dict(d) for d in (raw.get("lists") or [])]
            if not lists:
                lists = [TaskList.from_dict(d) for d in DEFAULT_LISTS]
            # 保证收集箱一定存在
            if not any(l.id == "inbox" for l in lists):
                lists.insert(0, TaskList.from_dict(DEFAULT_LISTS[0]))
            self.lists = lists
            self._normalize_orders()

    def _recover_from_backup(self) -> dict[str, Any]:
        """主文件损坏时，回退到最近的可用备份。"""
        backups = sorted(self._backup_dir().glob("data-*.json"), reverse=True)
        for b in backups:
            try:
                raw = json.loads(b.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("tasks") is not None:
                    return raw
            except Exception:
                continue
        return {}

    def save(self) -> None:
        with self.lock:
            payload = {
                "meta": {**self.meta, "version": DATA_VERSION, "saved_at": now_iso()},
                "lists": [l.to_dict() for l in self.lists],
                "tasks": [t.to_dict() for t in self.tasks],
            }
            text = json.dumps(payload, ensure_ascii=False, indent=1)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)   # 原子替换，断电不会写坏
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    def _backup_dir(self) -> Path:
        d = self.path.parent / "backups"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def backup_daily(self, force: bool = False) -> Optional[Path]:
        """每天首次启动时留一份备份，保留最近 7 份。"""
        if not self.path.exists():
            return None
        stamp = datetime.now().strftime("%Y-%m-%d")
        target = self._backup_dir() / f"data-{stamp}.json"
        if target.exists() and not force:
            return target
        try:
            shutil.copy2(self.path, target)
        except Exception:
            return None
        backups = sorted(self._backup_dir().glob("data-*.json"))
        for old in backups[:-BACKUP_KEEP]:
            try:
                old.unlink()
            except OSError:
                pass
        return target

    # ---------------- 清单 ----------------
    def list_by_id(self, list_id: str) -> TaskList:
        for l in self.lists:
            if l.id == list_id:
                return l
        return self.lists[0]

    def list_name(self, list_id: str) -> str:
        return self.list_by_id(list_id).name

    def find_list_by_name(self, name: str) -> Optional[TaskList]:
        name = (name or "").strip()
        for l in self.lists:
            if l.name == name:
                return l
        return None

    def ensure_list(self, name: str) -> TaskList:
        found = self.find_list_by_name(name)
        if found:
            return found
        colors = ["#3B82F6", "#8B5CF6", "#10B981", "#F59E0B", "#EF4444", "#06B6D4", "#EC4899"]
        tl = TaskList(id="l" + datetime.now().strftime("%H%M%S%f")[:10],
                      name=name, color=colors[len(self.lists) % len(colors)])
        self.lists.append(tl)
        return tl

    def add_list(self, name: str, color: str = "#6B7280") -> TaskList:
        tl = self.ensure_list(name)
        tl.color = color
        return tl

    def rename_list(self, list_id: str, name: str) -> None:
        self.list_by_id(list_id).name = name

    def delete_list(self, list_id: str) -> None:
        if list_id == "inbox":
            return
        self.lists = [l for l in self.lists if l.id != list_id]
        for t in self.tasks:
            if t.list_id == list_id:
                t.list_id = "inbox"

    # ---------------- 任务 ----------------
    def _next_order(self) -> float:
        if not self.tasks:
            return 1.0
        return max(t.order for t in self.tasks) + 1.0

    def add(self, task: Task, save: bool = True) -> Task:
        with self.lock:
            if not task.order:
                task.order = self._next_order()
            self.tasks.append(task)
            if save:
                self.save()
        self.notify()
        return task

    def get(self, task_id: str) -> Optional[Task]:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def update(self, task: Task, save: bool = True) -> None:
        with self.lock:
            task.updated_at = now_iso()
            if save:
                self.save()
        self.notify()

    def delete(self, task_id: str, save: bool = True) -> None:
        with self.lock:
            self.tasks = [t for t in self.tasks if t.id != task_id]
            if save:
                self.save()
        self.notify()

    def complete(self, task_id: str, save: bool = True) -> Optional[Task]:
        """完成任务；若为重复任务则生成下一次。返回新生成的任务（若有）。"""
        with self.lock:
            task = self.get(task_id)
            if not task or task.done:
                return None
            ts = datetime.now()
            task.completed_at = ts.strftime("%Y-%m-%dT%H:%M")
            task.updated_at = dt_to_iso(ts) or now_iso()
            task.notified_at = None
            new_task: Optional[Task] = None
            if task.repeat and task.repeat.get("freq"):
                base = task.due_dt or ts
                nxt = next_occurrence(base, task.repeat, now=ts)
                series = task.series_id or task.id
                task.series_id = series
                new_task = Task.new(
                    title=task.title,
                    note=task.note,
                    priority=task.priority,
                    list_id=task.list_id,
                    repeat=dict(task.repeat),
                    series_id=series,
                    order=self._next_order(),
                )
                new_task.due = dt_to_iso(nxt)
                new_task.subtasks = [s.__class__.new(s.title) for s in task.subtasks]
                self.tasks.append(new_task)
            self.save()
        self.notify()
        return new_task

    def uncomplete(self, task_id: str, save: bool = True) -> None:
        with self.lock:
            task = self.get(task_id)
            if not task:
                return
            task.completed_at = None
            task.archived = False
            task.updated_at = now_iso()
            # 若是重复链上的任务，把同系列的下一次删掉（撤销误勾）
            if task.series_id:
                followups = [t for t in self.tasks
                             if t.series_id == task.series_id and t.id != task.id and not t.done]
                for f in followups:
                    if f.due and task.due and (f.due or "") > (task.due or ""):
                        self.tasks.remove(f)
                        break
            if save:
                self.save()
        self.notify()

    def postpone(self, task_id: str, minutes: int = 0, days: int = 0,
                 new_due: Optional[datetime] = None, save: bool = True) -> None:
        with self.lock:
            task = self.get(task_id)
            if not task:
                return
            now = datetime.now()
            if new_due is not None:
                target = new_due
            else:
                base = task.due_dt or now
                if base < now:
                    base = now           # 已逾期则从现在算起
                target = base + timedelta(minutes=minutes, days=days)
            task.due = dt_to_iso(target)
            task.remind_at = None        # 到点提醒跟随新期限
            task.notified_at = None      # 允许再次提醒
            task.postpone_count += 1
            task.updated_at = now_iso()
            if save:
                self.save()
        self.notify()

    def set_priority(self, task_id: str, priority: int) -> None:
        with self.lock:
            task = self.get(task_id)
            if task:
                task.priority = priority
                task.updated_at = now_iso()
                self.save()
        self.notify()

    def move_to_list(self, task_id: str, list_id: str) -> None:
        with self.lock:
            task = self.get(task_id)
            if task:
                task.list_id = list_id
                task.updated_at = now_iso()
                self.save()
        self.notify()

    def reorder(self, task_id: str, before: Optional[Task] = None) -> None:
        """把任务挪到 before 之前（before 为 None 表示挪到最后）。"""
        with self.lock:
            task = self.get(task_id)
            if not task:
                return
            ordered = self.ordered_tasks()
            ordered = [t for t in ordered if t.id != task_id]
            if before is None:
                task.order = (max([t.order for t in ordered], default=0.0) + 1.0)
            elif before.id == task_id:
                return
            else:
                idx = next((i for i, t in enumerate(ordered) if t.id == before.id), len(ordered))
                prev_order = ordered[idx - 1].order if idx > 0 else ordered[0].order - 2.0
                nxt_order = ordered[idx].order
                task.order = (prev_order + nxt_order) / 2
            self._normalize_orders(save=True)

    def _normalize_orders(self, save: bool = False) -> None:
        ordered = sorted(self.tasks, key=lambda t: (t.order, t.created_at))
        for i, t in enumerate(ordered, start=1):
            t.order = float(i)
        if save:
            self.save()

    def clear_completed(self, save: bool = True) -> int:
        with self.lock:
            before = len(self.tasks)
            self.tasks = [t for t in self.tasks if not t.done]
            removed = before - len(self.tasks)
            if removed and save:
                self.save()
        if removed:
            self.notify()
        return removed

    # ---------------- 查询 ----------------
    def ordered_tasks(self) -> list[Task]:
        return sorted(self.tasks, key=lambda t: t.sort_key())

    def view(self, name: str = "today", search: str = "", list_filter: str = "") -> list[Task]:
        now = datetime.now()
        out: list[Task] = []
        for t in self.ordered_tasks():
            if list_filter and t.list_id != list_filter:
                continue
            if search:
                # 搜索时连已完成的也一起翻出来 —— 否则"那条做过的任务去哪了"没法回答
                q = search.lower()
                if q in t.title.lower() or q in (t.note or "").lower():
                    out.append(t)
                continue
            if name == "done":
                if t.done:
                    out.append(t)
                continue
            if t.done:
                continue
            if name == "today":
                # 今天到期 + 已逾期 + 无期限但优先级高的也一并显示，避免漏事
                if t.due_dt is None:
                    if t.priority >= PRIORITY_HIGH:
                        out.append(t)
                elif (t.due_dt.date() <= now.date()):
                    out.append(t)
            elif name == "week":
                if t.due_dt is None:
                    out.append(t)
                elif t.is_due_this_week(now) or t.due_dt.date() < now.date():
                    out.append(t)
            elif name == "quadrant":
                out.append(t)
            elif name == "all":
                out.append(t)
            else:
                out.append(t)
        return out

    def counts(self) -> dict[str, int]:
        now = datetime.now()
        today_due = 0
        overdue = 0
        active = 0
        done_today = 0
        for t in self.tasks:
            if t.done:
                cad = parse_dt(t.completed_at)
                if cad and cad.date() == now.date():
                    done_today += 1
                continue
            active += 1
            if t.due_dt:
                if t.due_dt.date() < now.date():
                    overdue += 1
                elif t.due_dt.date() == now.date():
                    today_due += 1
        return {"active": active, "today": today_due, "overdue": overdue,
                "done_today": done_today, "total": len(self.tasks)}

    def streak_days(self) -> int:
        """连续完成天数（今天还没完成则从昨天往前算，不让当天未完成断掉连击）。"""
        days = set()
        for t in self.tasks:
            cad = parse_dt(t.completed_at)
            if cad:
                days.add(cad.date())
        if not days:
            return 0
        today = datetime.now().date()
        cursor = today if today in days else today - timedelta(days=1)
        streak = 0
        while cursor in days:
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    def due_for_reminder(self, now: Optional[datetime] = None) -> list[Task]:
        now = now or datetime.now()
        out = []
        for t in self.tasks:
            if t.done or t.archived or t.notified_at:
                continue
            remind = t.remind_dt
            if remind and remind <= now:
                out.append(t)
        return out

    def missed_reminders(self, since_minutes: int = 60) -> list[Task]:
        """启动时要汇总的"错过的提醒"。"""
        now = datetime.now()
        out = []
        for t in self.tasks:
            if t.done or t.archived or t.notified_at:
                continue
            due = t.due_dt
            if due and due < now:
                out.append(t)
        return sorted(out, key=lambda t: t.due_dt or now)

    # ---------------- 导入导出 ----------------
    def export_json(self, dest: Path) -> None:
        payload = {
            "meta": {**self.meta, "exported_at": now_iso()},
            "lists": [l.to_dict() for l in self.lists],
            "tasks": [t.to_dict() for t in self.tasks],
        }
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def export_csv(self, dest: Path) -> None:
        with dest.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["标题", "分类", "优先级", "期限", "重复", "完成", "备注"])
            for t in self.ordered_tasks():
                w.writerow([t.title, self.list_name(t.list_id), t.priority_label,
                            t.due or "", t.repeat_label,
                            t.completed_at or "", t.note])

    def import_json(self, src: Path, merge: bool = True) -> int:
        raw = json.loads(src.read_text(encoding="utf-8"))
        added = 0
        with self.lock:
            for l in (raw.get("lists") or []):
                tl = TaskList.from_dict(l)
                if not any(x.id == tl.id for x in self.lists):
                    self.lists.append(tl)
            existing = {t.id for t in self.tasks}
            for d in (raw.get("tasks") or []):
                t = Task.from_dict(d)
                if t.id in existing:
                    t.id = t.id + "i"
                if not merge:
                    self.tasks = []
                self.tasks.append(t)
                added += 1
            self._normalize_orders()
            self.save()
        self.notify()
        return added

    def stats_by_day(self, days: int = 7) -> list[tuple[str, int]]:
        today = datetime.now().date()
        buckets: dict[str, int] = {}
        for i in range(days):
            buckets[(today - timedelta(days=i)).isoformat()] = 0
        for t in self.tasks:
            cad = parse_dt(t.completed_at)
            if cad:
                key = cad.date().isoformat()
                if key in buckets:
                    buckets[key] += 1
        return [(k, buckets[k]) for k in sorted(buckets, reverse=True)]


def default_store() -> Store:
    st = Store()
    st.backup_daily()
    return st
