"""SQLite-backed store for scheduled tasks + run history.

Tasks/runs are stored as JSON blobs with a few indexed columns (next_run, enabled) so the
scheduler can cheaply find what's due. `next_run` is computed with croniter, honoring the
task's timezone. Thread-safe (check_same_thread=False + a lock) since the scheduler and the
request handlers touch it from different threads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from .models import ScheduledTask, TaskRun

# Runs kept per task. History exists for the task-detail view and for standing-approval
# resolution of RECENT runs; an unbounded table only grows the DB forever (a 5-minute
# cron writes ~100k rows/year). Old runs age out on write.
RUN_HISTORY_KEEP = 200


def compute_next_run(
    task: ScheduledTask, *, after: Optional[float] = None
) -> Optional[float]:
    """Next fire time (epoch seconds), or None when the task is exhausted. A one-shot
    keeps its (possibly past) fire time until it has been attempted — the scheduler's
    catch-up pass fires it once; attempted (success or error) means done."""
    sched = task.schedule
    now = after if after is not None else _epoch_now()
    if sched.kind == "once":
        if not sched.fire_at:
            return None
        try:
            dt = datetime.fromisoformat(sched.fire_at)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz(sched.timezone))
        ts = dt.timestamp()
        # A one-shot that was never attempted stays due even when its time has
        # passed — that's the declared run-once-catch-up policy (missed while the
        # server was down, or re-saved after the moment). Silently recomputing to
        # None made a disable/enable cycle kill the task with no trace. Anything
        # already attempted (last_run set, success or error) is done: one-shots
        # never retry.
        never_attempted = task.run_count == 0 and task.last_run is None
        return ts if never_attempted else None
    # cron
    from croniter import croniter

    if not sched.cron or not croniter.is_valid(sched.cron):
        return None
    if task.max_runs is not None and task.run_count >= task.max_runs:
        return None
    base = datetime.fromtimestamp(now, tz=_tz(sched.timezone))
    return croniter(sched.cron, base).get_next(datetime).timestamp()


def _tz(name: str):
    """Resolve a schedule timezone. 'local'/empty → the machine's local zone (right for a
    local-first tool: when you say '8:05 PM' you mean *your* clock, not UTC)."""
    if not name or name.lower() == "local":
        return datetime.now().astimezone().tzinfo
    try:
        return ZoneInfo(name)
    except Exception:
        return datetime.now().astimezone().tzinfo


def _epoch_now() -> float:
    return datetime.now(timezone.utc).timestamp()


class TaskStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    next_run REAL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runs_task ON task_runs(task_id, started_at DESC);
                """)
            self._conn.commit()

    # -- tasks ------------------------------------------------------------------
    def save(self, task: ScheduledTask) -> ScheduledTask:
        task.updated_at = _epoch_now()
        task.next_run = compute_next_run(task) if task.enabled else None
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO scheduled_tasks (id, enabled, next_run, data) VALUES (?, ?, ?, ?)",
                (
                    task.id,
                    1 if task.enabled else 0,
                    task.next_run,
                    json.dumps(task.to_dict()),
                ),
            )
            self._conn.commit()
        return task

    def get(self, task_id: str) -> Optional[ScheduledTask]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM scheduled_tasks WHERE id=?", (task_id,)
            ).fetchone()
        return ScheduledTask.from_dict(json.loads(row["data"])) if row else None

    def list(self) -> list[ScheduledTask]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM scheduled_tasks ORDER BY next_run IS NULL, next_run"
            ).fetchall()
        return [ScheduledTask.from_dict(json.loads(r["data"])) for r in rows]

    def delete(self, task_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM scheduled_tasks WHERE id=?", (task_id,)
            )
            self._conn.execute("DELETE FROM task_runs WHERE task_id=?", (task_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def due(self, *, now: Optional[float] = None) -> list[ScheduledTask]:
        now = now if now is not None else _epoch_now()
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM scheduled_tasks WHERE enabled=1 AND next_run IS NOT NULL AND next_run<=? ORDER BY next_run",
                (now,),
            ).fetchall()
        return [ScheduledTask.from_dict(json.loads(r["data"])) for r in rows]

    # -- runs -------------------------------------------------------------------
    def add_run(self, run: TaskRun) -> TaskRun:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO task_runs (run_id, task_id, started_at, data) VALUES (?, ?, ?, ?)",
                (run.run_id, run.task_id, run.started_at, json.dumps(run.to_dict())),
            )
            # Retention on write: drop everything past the newest RUN_HISTORY_KEEP for
            # this task (run_id breaks started_at ties so the cut is deterministic).
            # A run still marked running never ages out — a suspended run's parked
            # approval resolves through find_run, and deleting the row would orphan it.
            self._conn.execute(
                "DELETE FROM task_runs WHERE task_id=? "
                "AND json_extract(data, '$.status') != 'running' "
                "AND run_id NOT IN ("
                "SELECT run_id FROM task_runs WHERE task_id=? "
                "ORDER BY started_at DESC, run_id DESC LIMIT ?)",
                (run.task_id, run.task_id, RUN_HISTORY_KEEP),
            )
            self._conn.commit()
        return run

    def find_run(self, run_id: str) -> Optional[TaskRun]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM task_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return TaskRun.from_dict(json.loads(row["data"])) if row else None

    def task_for_run_session(self, session_id: str) -> Optional[ScheduledTask]:
        """The owning task of a run session ('__run__<run_id>'), or None. How standing
        scoped approvals resolve which automation a live approval belongs to (§25)."""
        if not session_id.startswith("__run__"):
            return None
        run = self.find_run(session_id[len("__run__") :])
        return self.get(run.task_id) if run else None

    def runs(self, task_id: str, *, limit: int = 50) -> list[TaskRun]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM task_runs WHERE task_id=? ORDER BY started_at DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
        return [TaskRun.from_dict(json.loads(r["data"])) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
