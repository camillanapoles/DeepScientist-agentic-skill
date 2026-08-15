"""SQLite-backed CRUD store for orchestration managed objects and events.

The database is the *single source of truth*. Every decision reads the
current object row, mutates it transactionally, and appends events to
the ``events`` table. Nothing in the orchestration layer keeps
authoritative state in memory.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from ..shared import generate_id, utc_now
from .models import (
    ACTION_NOOP,
    ENV_UNKNOWN,
    STATE_PENDING,
    ManagedObject,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    object_id   TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    environment TEXT NOT NULL,
    state       TEXT NOT NULL,
    runner      TEXT NOT NULL DEFAULT '',
    payload     TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_objects_environment ON objects(environment);
CREATE INDEX IF NOT EXISTS idx_objects_state ON objects(state);
CREATE INDEX IF NOT EXISTS idx_objects_kind ON objects(kind);

CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    object_id   TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    FOREIGN KEY(object_id) REFERENCES objects(object_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_events_object ON events(object_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id  TEXT PRIMARY KEY,
    object_id    TEXT NOT NULL,
    event_id     TEXT,
    previous_state TEXT NOT NULL,
    next_state   TEXT NOT NULL,
    rationale    TEXT NOT NULL DEFAULT '',
    actions      TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL,
    FOREIGN KEY(object_id) REFERENCES objects(object_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_decisions_object ON decisions(object_id, created_at);
"""


class OrchestrationStore:
    """Thin CRUD + event-log DAO over a single SQLite file.

    All writes are serialised with a lock because SQLite connections are
    used in check_same_thread=False mode; the lock keeps concurrent
    daemon threads from interleaving transactions.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we manage BEGIN/COMMIT
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _row_to_object(row: sqlite3.Row) -> ManagedObject:
        return ManagedObject(
            object_id=row["object_id"],
            kind=row["kind"],
            name=row["name"],
            environment=row["environment"],
            state=row["state"],
            runner=row["runner"],
            payload=json.loads(row["payload"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------ objects

    def create_object(
        self,
        *,
        kind: str,
        name: str,
        environment: str = ENV_UNKNOWN,
        state: str = STATE_PENDING,
        runner: str = "",
        payload: dict[str, Any] | None = None,
        object_id: str | None = None,
    ) -> ManagedObject:
        now = utc_now()
        obj_id = object_id or generate_id("obj")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    INSERT INTO objects
                        (object_id, kind, name, environment, state, runner, payload, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        obj_id,
                        kind,
                        name,
                        environment,
                        state,
                        runner,
                        json.dumps(payload or {}, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return self.get_object(obj_id)  # type: ignore[return-value]

    def get_object(self, object_id: str) -> ManagedObject | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM objects WHERE object_id = ?", (object_id,)
            ).fetchone()
        return self._row_to_object(row) if row else None

    def list_objects(
        self,
        *,
        kind: str | None = None,
        environment: str | None = None,
        state: str | None = None,
        limit: int = 500,
    ) -> list[ManagedObject]:
        clauses: list[str] = []
        params: list[Any] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if environment:
            clauses.append("environment = ?")
            params.append(environment)
        if state:
            clauses.append("state = ?")
            params.append(state)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM objects{where} ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._row_to_object(row) for row in rows]

    def update_object(
        self,
        object_id: str,
        *,
        state: str | None = None,
        environment: str | None = None,
        runner: str | None = None,
        payload_patch: dict[str, Any] | None = None,
        name: str | None = None,
        replace_payload: bool = False,
    ) -> ManagedObject | None:
        """Patch an object and append an internal ``object.updated`` event.

        ``payload_patch`` is shallow-merged into the existing payload by
        default. Pass ``replace_payload=True`` to overwrite entirely.
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM objects WHERE object_id = ?", (object_id,)
                ).fetchone()
                if row is None:
                    self._conn.execute("ROLLBACK")
                    return None
                current = self._row_to_object(row)
                new_payload = (
                    dict(payload_patch or {})
                    if replace_payload
                    else {**current.payload, **(payload_patch or {})}
                )
                new_state = state if state is not None else current.state
                new_environment = environment if environment is not None else current.environment
                new_runner = runner if runner is not None else current.runner
                new_name = name if name is not None else current.name
                now = utc_now()
                self._conn.execute(
                    """
                    UPDATE objects
                       SET state = ?, environment = ?, runner = ?, payload = ?,
                           name = ?, updated_at = ?
                     WHERE object_id = ?
                    """,
                    (
                        new_state,
                        new_environment,
                        new_runner,
                        json.dumps(new_payload, ensure_ascii=False),
                        new_name,
                        now,
                        object_id,
                    ),
                )
                self._append_event_locked(
                    object_id,
                    "object.updated",
                    {
                        "state": new_state,
                        "environment": new_environment,
                        "runner": new_runner,
                        "patched_keys": sorted((payload_patch or {}).keys()),
                    },
                    now=now,
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return self.get_object(object_id)

    def delete_object(self, object_id: str) -> bool:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    "DELETE FROM objects WHERE object_id = ?", (object_id,)
                )
                self._conn.execute("COMMIT")
                return cur.rowcount > 0
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # ------------------------------------------------------------------ events

    def _append_event_locked(
        self,
        object_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        now: str | None = None,
        event_id: str | None = None,
    ) -> str:
        evt_id = event_id or generate_id("evt")
        self._conn.execute(
            """
            INSERT INTO events (event_id, object_id, event_type, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                evt_id,
                object_id,
                event_type,
                json.dumps(payload, ensure_ascii=False),
                now or utc_now(),
            ),
        )
        return evt_id

    def append_event(
        self, object_id: str, event_type: str, payload: dict[str, Any] | None = None
    ) -> str:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                evt_id = self._append_event_locked(
                    object_id, event_type, payload or {}
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return evt_id

    def latest_decision(self, object_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT decision_id, object_id, event_id, previous_state,
                       next_state, rationale, actions, created_at
                  FROM decisions
                 WHERE object_id = ?
              ORDER BY created_at DESC, rowid DESC
                 LIMIT 1
                """,
                (object_id,),
            ).fetchone()
        if row is None:
            return None
        import json as _json
        return {
            "decision_id": row["decision_id"],
            "object_id": row["object_id"],
            "event_id": row["event_id"],
            "previous_state": row["previous_state"],
            "next_state": row["next_state"],
            "rationale": row["rationale"],
            "actions": _json.loads(row["actions"] or "[]"),
            "created_at": row["created_at"],
        }

    def list_events(
        self, object_id: str, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT event_id, object_id, event_type, payload, created_at
                  FROM events
                 WHERE object_id = ?
              ORDER BY created_at ASC, rowid ASC
                 LIMIT ?
                """,
                (object_id, limit),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "object_id": row["object_id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload"] or "{}"),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------ decisions

    def record_decision(
        self,
        *,
        object_id: str,
        event_id: str | None,
        previous_state: str,
        next_state: str,
        rationale: str,
        actions: Iterable[dict[str, Any]],
    ) -> str:
        decision_id = generate_id("dec")
        now = utc_now()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    INSERT INTO decisions
                        (decision_id, object_id, event_id, previous_state,
                         next_state, rationale, actions, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        decision_id,
                        object_id,
                        event_id,
                        previous_state,
                        next_state,
                        rationale,
                        json.dumps(list(actions), ensure_ascii=False),
                        now,
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return decision_id

    def list_decisions(
        self, object_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT decision_id, object_id, event_id, previous_state,
                       next_state, rationale, actions, created_at
                  FROM decisions
                 WHERE object_id = ?
              ORDER BY created_at ASC, rowid ASC
                 LIMIT ?
                """,
                (object_id, limit),
            ).fetchall()
        return [
            {
                "decision_id": row["decision_id"],
                "object_id": row["object_id"],
                "event_id": row["event_id"],
                "previous_state": row["previous_state"],
                "next_state": row["next_state"],
                "rationale": row["rationale"],
                "actions": json.loads(row["actions"] or "[]"),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def create_store(path: Path | str) -> OrchestrationStore:
    """Factory used by the daemon and tests."""
    return OrchestrationStore(Path(path))
