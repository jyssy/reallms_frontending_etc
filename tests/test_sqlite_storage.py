import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from observability.contract import parse_trace_event
from observability.storage import SCHEMA_VERSION, TABLE_SPECS, SQLiteTraceStore


def event(sequence, *, run_id="00000000-0000-4000-8000-000000000001", **overrides):
    value = {
        "contract_version": 1,
        "run_id": run_id,
        "sequence": sequence,
        "timestamp": f"2026-09-24T12:00:{sequence:02d}+00:00",
        "event_type": "provider.attempt",
        "component": "provider",
        "status": "success",
        "duration_ms": sequence,
        "metadata": {"provider": "remote", "attempt": sequence},
    }
    value.update(overrides)
    return parse_trace_event(value)


def make_store(path: Path, *, max_runs=5, max_events=5):
    return SQLiteTraceStore(
        path,
        max_runs=max_runs,
        retention_days=7,
        max_events_per_run=max_events,
    )


def test_sqlite_store_persists_ordered_sanitized_history(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = make_store(path)
    unsafe = event(2)
    unsafe["metadata"]["prompt"] = "forbidden-marker"

    assert store.add_event(unsafe) is True
    assert store.add_event(event(1)) is True
    assert store.add_event(event(2)) is False
    store.finish_run(
        unsafe["run_id"],
        {
            "status": "success",
            "task_type": "coding",
            "model_roles": {"reviewer": "review-model", "judge": "judge-model"},
            "final": "forbidden-marker",
        },
        configured={"router": "router-model"},
    )

    reopened = make_store(path)
    snapshot = reopened.snapshot()
    run = snapshot["runs"][0]

    assert snapshot["source"] == "sqlite_history"
    assert [item["sequence"] for item in run["events"]] == [1, 2]
    assert run["workflow"][0]["models"][0] == {
        "role": "router",
        "name": "router-model",
        "state": "configured",
    }
    assert run["result"]["model_roles"]["reviewer"] == "review-model"
    assert "forbidden-marker" not in path.read_bytes().decode("utf-8", errors="ignore")
    assert path.stat().st_mode & 0o777 == 0o600


def test_sqlite_store_enforces_run_event_and_age_retention(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = make_store(path, max_runs=2, max_events=2)
    run_ids = [f"00000000-0000-4000-8000-{index:012d}" for index in range(1, 4)]
    for run_id in run_ids:
        for sequence in (1, 2, 3):
            store.add_event(event(sequence, run_id=run_id))

    snapshot = store.snapshot()
    assert len(snapshot["runs"]) == 2
    assert all([item["sequence"] for item in run["events"]] == [2, 3] for run in snapshot["runs"])

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE observed_runs SET last_seen_at = '2000-01-01T00:00:00+00:00'"
        )
    store.add_event(event(1, run_id="00000000-0000-4000-8000-000000000009"))
    assert [run["run_id"] for run in store.snapshot()["runs"]] == [
        "00000000-0000-4000-8000-000000000009"
    ]


def test_sqlite_store_keeps_browser_snapshot_bounded_to_24_runs(tmp_path):
    store = make_store(tmp_path / "history.sqlite3", max_runs=30, max_events=1)
    for index in range(1, 31):
        run_id = f"00000000-0000-4000-8000-{index:012d}"
        store.add_event(event(1, run_id=run_id))

    snapshot = store.snapshot()

    assert len(snapshot["runs"]) == 24
    assert snapshot["retention"]["stored_events"] == 30
    assert snapshot["retention"]["dropped_events_observed"] is False


@pytest.fixture
def sqlite_settings(tmp_path):
    database = tmp_path / "history.sqlite3"
    with override_settings(
        OBSERVABILITY_STORAGE_BACKEND="sqlite",
        OBSERVABILITY_SQLITE_PATH=str(database),
        OBSERVABILITY_SQLITE_MAX_RUNS=10,
        OBSERVABILITY_SQLITE_RETENTION_DAYS=7,
    ):
        store = make_store(database, max_runs=10)
        store.add_event(event(1))
        store.finish_run(
            "00000000-0000-4000-8000-000000000001",
            {
                "status": "success",
                "task_type": "coding",
                "model_roles": {"reviewer": "safe-model"},
                "completion": "forbidden-marker",
            },
        )
        yield database


def test_json_export_contains_every_table_manifest_and_checksums(sqlite_settings, tmp_path):
    output = tmp_path / "portable.json"
    with sqlite3.connect(sqlite_settings) as connection:
        connection.execute(
            "UPDATE trace_events SET metadata_json = ?",
            ('{"prompt":"forbidden-marker","provider":"remote"}',),
        )
    call_command("observability_export", format="json", output=str(output))
    payload = json.loads(output.read_text())

    assert payload["manifest"]["schema_version"] == SCHEMA_VERSION
    assert set(payload["tables"]) == set(TABLE_SPECS)
    for name, rows in payload["tables"].items():
        canonical = json.dumps(
            rows, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode()
        assert payload["manifest"]["tables"][name] == {
            "row_count": len(rows),
            "sha256": hashlib.sha256(canonical).hexdigest(),
        }
    assert "forbidden-marker" not in output.read_text()
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(CommandError):
        call_command("observability_export", format="json", output=str(output))
    call_command("observability_export", format="json", output=str(output), force=True)


def test_csv_export_and_sqlite_backup_are_complete_and_private(sqlite_settings, tmp_path):
    output = tmp_path / "csv-export"
    call_command("observability_export", format="csv", output=str(output))
    manifest = json.loads((output / "manifest.json").read_text())

    assert set(manifest["tables"]) == set(TABLE_SPECS)
    for name in TABLE_SPECS:
        csv_path = output / f"{name}.csv"
        assert csv_path.exists()
        assert csv_path.stat().st_mode & 0o777 == 0o600
        assert manifest["tables"][name]["sha256"] == hashlib.sha256(
            csv_path.read_bytes()
        ).hexdigest()

    backup = tmp_path / "backup.sqlite3"
    call_command("observability_backup", output=str(backup))
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT COUNT(*) FROM trace_events").fetchone()[0] == 1
    with pytest.raises(CommandError):
        call_command("observability_backup", output=str(backup))
    call_command("observability_backup", output=str(backup), force=True)


def test_export_and_backup_refuse_symbolic_link_destinations(sqlite_settings, tmp_path):
    target = tmp_path / "target"
    target.write_text("do-not-replace")
    link = tmp_path / "linked-output"
    link.symlink_to(target)

    with pytest.raises(CommandError):
        call_command(
            "observability_export", format="json", output=str(link), force=True
        )
    with pytest.raises(CommandError):
        call_command("observability_backup", output=str(link), force=True)
    assert target.read_text() == "do-not-replace"


def test_commands_require_explicit_sqlite_mode(tmp_path):
    with override_settings(OBSERVABILITY_STORAGE_BACKEND="memory"):
        with pytest.raises(CommandError):
            call_command(
                "observability_export",
                format="json",
                output=str(tmp_path / "unused.json"),
            )
