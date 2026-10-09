"""Opt-in, bounded SQLite storage for sanitized observability metadata."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .contract import (
    MODEL_ROLES,
    RESULT_STATUSES,
    TASK_TYPES,
    configured_model_catalog,
    parse_observed_result,
    parse_trace_event,
    safe_model_name,
)
from .traces import (
    MAX_EVENTS_PER_RUN,
    MAX_RUNS,
    TraceStore,
    build_observability_analytics,
    trace_store,
)

SCHEMA_VERSION = 1
EXPORT_VERSION = 1

TABLE_SPECS = {
    "schema_info": (("schema_version",), "schema_version"),
    "observed_runs": (
        (
            "run_id",
            "contract_version",
            "first_seen_at",
            "last_seen_at",
            "completed_at",
            "result_status",
            "task_type",
        ),
        "run_id",
    ),
    "trace_events": (
        (
            "run_id",
            "sequence",
            "timestamp",
            "event_type",
            "component",
            "status",
            "duration_ms",
            "metadata_json",
            "stored_at",
        ),
        "run_id, sequence",
    ),
    "run_model_attribution": (
        ("run_id", "role", "attribution_kind", "model_name"),
        "run_id, role, attribution_kind",
    ),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info (
    schema_version INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS observed_runs (
    run_id TEXT PRIMARY KEY,
    contract_version INTEGER NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    completed_at TEXT,
    result_status TEXT,
    task_type TEXT
);
CREATE TABLE IF NOT EXISTS trace_events (
    run_id TEXT NOT NULL REFERENCES observed_runs(run_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    component TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms INTEGER,
    metadata_json TEXT NOT NULL,
    stored_at TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence)
);
CREATE TABLE IF NOT EXISTS run_model_attribution (
    run_id TEXT NOT NULL REFERENCES observed_runs(run_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    attribution_kind TEXT NOT NULL CHECK (attribution_kind IN ('configured', 'observed')),
    model_name TEXT NOT NULL,
    PRIMARY KEY (run_id, role, attribution_kind)
);
CREATE INDEX IF NOT EXISTS trace_events_stored_at_idx ON trace_events(stored_at);
CREATE INDEX IF NOT EXISTS observed_runs_last_seen_idx ON observed_runs(last_seen_at);
"""

_RETENTION_CANDIDATES_CTE = """
WITH candidate_runs AS (
    SELECT run_id FROM observed_runs WHERE last_seen_at < ?
    UNION
    SELECT run_id FROM (
        SELECT run_id FROM observed_runs
        ORDER BY last_seen_at DESC, run_id DESC
        LIMIT -1 OFFSET ?
    )
)
"""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


class SQLiteTraceStore:
    """TraceStore-compatible durable store with bounded history."""

    def __init__(
        self,
        path: Path,
        *,
        max_runs: int,
        retention_days: int,
        max_events_per_run: int = MAX_EVENTS_PER_RUN,
        initialize: bool = True,
    ) -> None:
        self.path = path.resolve()
        self.max_runs = max_runs
        self.retention_days = retention_days
        self.max_events_per_run = max_events_per_run
        self._lock = Lock()
        if initialize:
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _connect_read_only(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"{self.path.as_uri()}?mode=ro",
            timeout=5,
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            versions = connection.execute(
                "SELECT schema_version FROM schema_info"
            ).fetchall()
            if not versions:
                connection.execute(
                    "INSERT INTO schema_info(schema_version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
            elif [row[0] for row in versions] != [SCHEMA_VERSION]:
                raise ImproperlyConfigured("Unsupported observability SQLite schema version.")
        os.chmod(self.path, 0o600)

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        versions = connection.execute(
            "SELECT schema_version FROM schema_info ORDER BY schema_version"
        ).fetchall()
        if [row[0] for row in versions] != [SCHEMA_VERSION]:
            raise ImproperlyConfigured("Unsupported observability SQLite schema version.")

    def add_event(self, value: object) -> bool:
        event = parse_trace_event(value)
        stored_at = _utc_now()
        metadata = _canonical_json(event["metadata"])
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO observed_runs(
                    run_id, contract_version, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id) DO NOTHING
                """,
                (event["run_id"], event["contract_version"], stored_at, stored_at),
            )
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO trace_events(
                    run_id, sequence, timestamp, event_type, component, status,
                    duration_ms, metadata_json, stored_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["run_id"],
                    event["sequence"],
                    event["timestamp"],
                    event["event_type"],
                    event["component"],
                    event["status"],
                    event["duration_ms"],
                    metadata,
                    stored_at,
                ),
            )
            inserted = cursor.rowcount == 1
            if inserted:
                connection.execute(
                    "UPDATE observed_runs SET last_seen_at = ? WHERE run_id = ?",
                    (stored_at, event["run_id"]),
                )
                self._enforce_retention(connection, event["run_id"])
            return inserted

    def finish_run(self, run_id: str, value: object, configured: object = None) -> None:
        result = parse_observed_result(value)
        completed_at = _utc_now()
        catalog = configured_model_catalog(configured)
        with self._lock, self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM observed_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if not exists:
                return
            connection.execute(
                """
                UPDATE observed_runs
                SET completed_at = ?, last_seen_at = ?, result_status = ?, task_type = ?
                WHERE run_id = ?
                """,
                (completed_at, completed_at, result["status"], result["task_type"], run_id),
            )
            for role, model in catalog.items():
                connection.execute(
                    """
                    INSERT OR REPLACE INTO run_model_attribution(
                        run_id, role, attribution_kind, model_name
                    ) VALUES (?, ?, 'configured', ?)
                    """,
                    (run_id, role, model),
                )
            for role, model in result["model_roles"].items():
                connection.execute(
                    """
                    INSERT OR REPLACE INTO run_model_attribution(
                        run_id, role, attribution_kind, model_name
                    ) VALUES (?, ?, 'observed', ?)
                    """,
                    (run_id, role, model),
                )
            self._enforce_retention(connection)

    def _enforce_retention(
        self, connection: sqlite3.Connection, current_run_id: str | None = None
    ) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat()
        connection.execute("DELETE FROM observed_runs WHERE last_seen_at < ?", (cutoff,))
        connection.execute(
            """
            DELETE FROM observed_runs
            WHERE run_id IN (
                SELECT run_id FROM observed_runs
                ORDER BY last_seen_at DESC, run_id DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (self.max_runs,),
        )
        if current_run_id is not None:
            connection.execute(
                """
                DELETE FROM trace_events
                WHERE run_id = ? AND sequence NOT IN (
                    SELECT sequence FROM trace_events WHERE run_id = ?
                    ORDER BY sequence DESC LIMIT ?
                )
                """,
                (current_run_id, current_run_id, self.max_events_per_run),
            )

    def _retention_plan(self, connection: sqlite3.Connection, *, cutoff: str) -> dict[str, Any]:
        parameters = (cutoff, self.max_runs)
        old_runs = connection.execute(
            "SELECT COUNT(*) FROM observed_runs WHERE last_seen_at < ?", (cutoff,)
        ).fetchone()[0]
        overflow_runs = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT run_id FROM observed_runs
                ORDER BY last_seen_at DESC, run_id DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (self.max_runs,),
        ).fetchone()[0]
        candidate_runs = connection.execute(
            _RETENTION_CANDIDATES_CTE + "SELECT COUNT(*) FROM candidate_runs",
            parameters,
        ).fetchone()[0]
        events_from_candidate_runs = connection.execute(
            _RETENTION_CANDIDATES_CTE
            + """
            SELECT COUNT(*) FROM trace_events
            WHERE run_id IN (SELECT run_id FROM candidate_runs)
            """,
            parameters,
        ).fetchone()[0]
        excess_events = connection.execute(
            _RETENTION_CANDIDATES_CTE
            + """
            , ranked_events AS (
                SELECT
                    run_id,
                    sequence,
                    ROW_NUMBER() OVER (
                        PARTITION BY run_id ORDER BY sequence DESC
                    ) AS retained_position
                FROM trace_events
                WHERE run_id NOT IN (SELECT run_id FROM candidate_runs)
            )
            SELECT COUNT(*) FROM ranked_events WHERE retained_position > ?
            """,
            (*parameters, self.max_events_per_run),
        ).fetchone()[0]
        return {
            "cutoff": cutoff,
            "max_runs": self.max_runs,
            "max_events_per_run": self.max_events_per_run,
            "retention_days": self.retention_days,
            "old_runs": old_runs,
            "overflow_runs": overflow_runs,
            "candidate_runs": candidate_runs,
            "candidate_events": events_from_candidate_runs + excess_events,
            "events_from_candidate_runs": events_from_candidate_runs,
            "excess_events": excess_events,
        }

    def maintenance_status(self) -> dict[str, Any]:
        """Return bounded, read-only health and retention information."""
        cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat()
        with self._lock, self._connect_read_only() as connection:
            self._validate_schema(connection)
            table_counts = {}
            for table in TABLE_SPECS:
                table_counts[table] = connection.execute(
                    f"SELECT COUNT(*) FROM {table}"  # noqa: S608
                ).fetchone()[0]
            active_runs = connection.execute(
                "SELECT COUNT(*) FROM observed_runs WHERE completed_at IS NULL"
            ).fetchone()[0]
            quick_check_rows = connection.execute("PRAGMA quick_check(1)").fetchall()
            quick_check = [str(row[0])[:128] for row in quick_check_rows]
            foreign_key_violations = sum(1 for _ in connection.execute("PRAGMA foreign_key_check"))
            retention = self._retention_plan(connection, cutoff=cutoff)
        try:
            database_bytes = self.path.stat().st_size
        except OSError:
            database_bytes = None
        healthy = quick_check == ["ok"] and foreign_key_violations == 0
        return {
            "backend": "sqlite",
            "healthy": healthy,
            "schema_version": SCHEMA_VERSION,
            "expected_schema_version": SCHEMA_VERSION,
            "database_bytes": database_bytes,
            "table_counts": table_counts,
            "active_runs": active_runs,
            "quick_check": quick_check,
            "foreign_key_violations": foreign_key_violations,
            "retention": retention,
        }

    def prune_retention(self, *, confirm: bool = False) -> dict[str, Any]:
        """Preview or apply the configured bounded-retention policy atomically."""
        cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat()
        connect = self._connect if confirm else self._connect_read_only
        with self._lock, connect() as connection:
            if confirm:
                connection.execute("BEGIN IMMEDIATE")
            self._validate_schema(connection)
            plan = self._retention_plan(connection, cutoff=cutoff)
            if confirm:
                parameters = (cutoff, self.max_runs)
                connection.execute(
                    _RETENTION_CANDIDATES_CTE
                    + """
                    DELETE FROM observed_runs
                    WHERE run_id IN (SELECT run_id FROM candidate_runs)
                    """,
                    parameters,
                )
                connection.execute(
                    """
                    DELETE FROM trace_events
                    WHERE (run_id, sequence) IN (
                        SELECT run_id, sequence FROM (
                            SELECT
                                run_id,
                                sequence,
                                ROW_NUMBER() OVER (
                                    PARTITION BY run_id ORDER BY sequence DESC
                                ) AS retained_position
                            FROM trace_events
                        )
                        WHERE retained_position > ?
                    )
                    """,
                    (self.max_events_per_run,),
                )
            return {**plan, "applied": confirm}

    def _loaded_runs(self, limit: int | None = None) -> list[dict[str, Any]]:
        limit = self.max_runs if limit is None else min(limit, self.max_runs)
        with self._connect() as connection:
            run_rows = connection.execute(
                """
                SELECT * FROM observed_runs
                ORDER BY last_seen_at DESC, run_id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            loaded = []
            for row in run_rows:
                run_id = row["run_id"]
                event_rows = connection.execute(
                    "SELECT * FROM trace_events WHERE run_id = ? ORDER BY sequence", (run_id,)
                ).fetchall()
                model_rows = connection.execute(
                    """
                    SELECT role, attribution_kind, model_name
                    FROM run_model_attribution WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchall()
                loaded.append({"run": dict(row), "events": event_rows, "models": model_rows})
            return loaded

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return parse_trace_event(
            {
                "contract_version": 1,
                "run_id": row["run_id"],
                "sequence": row["sequence"],
                "timestamp": row["timestamp"],
                "event_type": row["event_type"],
                "component": row["component"],
                "status": row["status"],
                "duration_ms": row["duration_ms"],
                "metadata": json.loads(row["metadata_json"]),
            }
        )

    def snapshot(self, configured: object = None) -> dict[str, Any]:
        current_catalog = configured_model_catalog(configured)
        public_runs = []
        stored_events = 0
        for loaded in self._loaded_runs(MAX_RUNS):
            store = TraceStore(
                max_runs=1,
                max_events_per_run=self.max_events_per_run,
                max_total_events=self.max_events_per_run,
            )
            for row in loaded["events"]:
                store.add_event(self._event_from_row(row))
            stored_events += len(loaded["events"])
            observed = {
                row["role"]: row["model_name"]
                for row in loaded["models"]
                if row["attribution_kind"] == "observed"
            }
            configured_for_run = {
                row["role"]: row["model_name"]
                for row in loaded["models"]
                if row["attribution_kind"] == "configured"
            }
            run_row = loaded["run"]
            if run_row["result_status"] is not None:
                store.finish_run(
                    run_row["run_id"],
                    {
                        "status": run_row["result_status"],
                        "task_type": run_row["task_type"],
                        "model_roles": observed,
                    },
                )
            run_snapshot = store.snapshot(configured_for_run or current_catalog)["runs"]
            if run_snapshot:
                public_runs.append(run_snapshot[0])
        with self._connect() as connection:
            stored_events = connection.execute("SELECT COUNT(*) FROM trace_events").fetchone()[0]
        return {
            "contract_version": 1,
            "source": "sqlite_history",
            "observed": bool(public_runs),
            "retention": {
                "scope": "sqlite",
                "max_runs": self.max_runs,
                "max_events_per_run": self.max_events_per_run,
                "retention_days": self.retention_days,
                "stored_events": stored_events,
                "dropped_events": 0,
                "dropped_events_observed": False,
            },
            "analytics": build_observability_analytics(public_runs),
            "runs": public_runs,
        }

    def model_snapshot(self, configured: object) -> dict[str, Any]:
        store = TraceStore(
            max_runs=self.max_runs,
            max_events_per_run=self.max_events_per_run,
            max_total_events=self.max_runs * self.max_events_per_run,
        )
        for loaded in reversed(self._loaded_runs()):
            for row in loaded["events"]:
                store.add_event(self._event_from_row(row))
            observed = {
                row["role"]: row["model_name"]
                for row in loaded["models"]
                if row["attribution_kind"] == "observed"
            }
            run_row = loaded["run"]
            if run_row["result_status"] is not None:
                store.finish_run(
                    run_row["run_id"],
                    {
                        "status": run_row["result_status"],
                        "task_type": run_row["task_type"],
                        "model_roles": observed,
                    },
                )
        payload = store.model_snapshot(configured)
        payload["source"] = "sqlite_history"
        payload["retention"] = self.snapshot(configured)["retention"]
        return payload

    def reset(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM observed_runs")


_active_store: SQLiteTraceStore | None = None
_active_key: tuple[object, ...] | None = None
_active_lock = Lock()


def get_trace_store() -> TraceStore | SQLiteTraceStore:
    """Return the configured store, creating SQLite only when explicitly enabled."""
    backend = getattr(settings, "OBSERVABILITY_STORAGE_BACKEND", "memory")
    if backend == "memory":
        return trace_store
    if backend != "sqlite":
        raise ImproperlyConfigured("REALMS_OBSERVABILITY_STORAGE must be memory or sqlite.")
    raw_path = getattr(settings, "OBSERVABILITY_SQLITE_PATH", None)
    if not raw_path:
        raise ImproperlyConfigured(
            "REALMS_OBSERVABILITY_SQLITE_PATH is required when SQLite history is enabled."
        )
    key = (
        str(Path(raw_path).resolve()),
        settings.OBSERVABILITY_SQLITE_MAX_RUNS,
        settings.OBSERVABILITY_SQLITE_RETENTION_DAYS,
    )
    global _active_key, _active_store
    with _active_lock:
        if _active_store is None or _active_key != key:
            if not 1 <= settings.OBSERVABILITY_SQLITE_MAX_RUNS <= 100_000:
                raise ImproperlyConfigured(
                    "REALMS_OBSERVABILITY_SQLITE_MAX_RUNS must be between 1 and 100000."
                )
            if not 1 <= settings.OBSERVABILITY_SQLITE_RETENTION_DAYS <= 3_650:
                raise ImproperlyConfigured(
                    "REALMS_OBSERVABILITY_SQLITE_RETENTION_DAYS must be between 1 and 3650."
                )
            _active_store = SQLiteTraceStore(
                Path(raw_path),
                max_runs=settings.OBSERVABILITY_SQLITE_MAX_RUNS,
                retention_days=settings.OBSERVABILITY_SQLITE_RETENTION_DAYS,
            )
            _active_key = key
        return _active_store


def storage_source() -> str:
    return (
        "sqlite_history"
        if getattr(settings, "OBSERVABILITY_STORAGE_BACKEND", "memory") == "sqlite"
        else "live_process"
    )


def table_rows(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Read the fixed public export table set in deterministic order."""
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        version = connection.execute("SELECT schema_version FROM schema_info").fetchone()
        if version is None or version[0] != SCHEMA_VERSION:
            raise ImproperlyConfigured("Unsupported observability SQLite schema version.")
        result = {}
        for table, (columns, ordering) in TABLE_SPECS.items():
            selected = ", ".join(columns)
            rows = connection.execute(
                f"SELECT {selected} FROM {table} ORDER BY {ordering}"  # noqa: S608
            ).fetchall()
            result[table] = [dict(row) for row in rows]
        return _sanitize_export_rows(result)


def _safe_stored_timestamp(value: object, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise ImproperlyConfigured("Invalid data in observability SQLite history.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ImproperlyConfigured(
            "Invalid data in observability SQLite history."
        ) from error
    if parsed.tzinfo is None:
        raise ImproperlyConfigured("Invalid data in observability SQLite history.")
    return value


def _canonical_run_id(value: object) -> str:
    try:
        run_id = str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise ImproperlyConfigured(
            "Invalid data in observability SQLite history."
        ) from error
    if value != run_id:
        raise ImproperlyConfigured("Invalid data in observability SQLite history.")
    return run_id


def _sanitize_export_rows(
    tables: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    if tables["schema_info"] != [{"schema_version": SCHEMA_VERSION}]:
        raise ImproperlyConfigured("Invalid data in observability SQLite history.")

    safe_runs = []
    for row in tables["observed_runs"]:
        status = row["result_status"]
        task_type = row["task_type"]
        if row["contract_version"] != SCHEMA_VERSION:
            raise ImproperlyConfigured("Invalid data in observability SQLite history.")
        if status is not None and status not in RESULT_STATUSES:
            raise ImproperlyConfigured("Invalid data in observability SQLite history.")
        if task_type is not None and task_type not in TASK_TYPES:
            raise ImproperlyConfigured("Invalid data in observability SQLite history.")
        safe_runs.append(
            {
                "run_id": _canonical_run_id(row["run_id"]),
                "contract_version": SCHEMA_VERSION,
                "first_seen_at": _safe_stored_timestamp(row["first_seen_at"]),
                "last_seen_at": _safe_stored_timestamp(row["last_seen_at"]),
                "completed_at": _safe_stored_timestamp(row["completed_at"], optional=True),
                "result_status": status,
                "task_type": task_type,
            }
        )

    safe_events = []
    for row in tables["trace_events"]:
        try:
            metadata = json.loads(row["metadata_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise ImproperlyConfigured(
                "Invalid data in observability SQLite history."
            ) from error
        event = parse_trace_event(
            {
                "contract_version": SCHEMA_VERSION,
                "run_id": row["run_id"],
                "sequence": row["sequence"],
                "timestamp": row["timestamp"],
                "event_type": row["event_type"],
                "component": row["component"],
                "status": row["status"],
                "duration_ms": row["duration_ms"],
                "metadata": metadata,
            }
        )
        safe_events.append(
            {
                "run_id": event["run_id"],
                "sequence": event["sequence"],
                "timestamp": event["timestamp"],
                "event_type": event["event_type"],
                "component": event["component"],
                "status": event["status"],
                "duration_ms": event["duration_ms"],
                "metadata_json": _canonical_json(event["metadata"]),
                "stored_at": _safe_stored_timestamp(row["stored_at"]),
            }
        )

    safe_models = []
    for row in tables["run_model_attribution"]:
        model = safe_model_name(row["model_name"])
        if (
            row["role"] not in MODEL_ROLES
            or row["attribution_kind"] not in {"configured", "observed"}
            or model is None
        ):
            raise ImproperlyConfigured("Invalid data in observability SQLite history.")
        safe_models.append(
            {
                "run_id": _canonical_run_id(row["run_id"]),
                "role": row["role"],
                "attribution_kind": row["attribution_kind"],
                "model_name": model,
            }
        )
    return {
        "schema_info": tables["schema_info"],
        "observed_runs": safe_runs,
        "trace_events": safe_events,
        "run_model_attribution": safe_models,
    }


def export_manifest(tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "format_version": EXPORT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "exported_at": _utc_now(),
        "tables": {
            name: {
                "row_count": len(rows),
                "sha256": hashlib.sha256(_canonical_json(rows).encode()).hexdigest(),
            }
            for name, rows in tables.items()
        },
    }


def csv_bytes(columns: tuple[str, ...], rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")
