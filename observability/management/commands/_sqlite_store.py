"""Shared validation for observability SQLite management commands."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError

from observability.storage import SQLiteTraceStore


def configured_sqlite_store() -> SQLiteTraceStore:
    if getattr(settings, "OBSERVABILITY_STORAGE_BACKEND", "memory") != "sqlite":
        raise CommandError("SQLite observability history is not enabled.")
    raw_path = getattr(settings, "OBSERVABILITY_SQLITE_PATH", None)
    if not raw_path:
        raise CommandError("The SQLite observability path is not configured.")
    configured_path = Path(raw_path).expanduser()
    if configured_path.is_symlink():
        raise CommandError("Refusing to manage a database through a symbolic link.")
    if not configured_path.is_file():
        raise CommandError("The configured SQLite observability database does not exist.")
    return SQLiteTraceStore(
        configured_path,
        max_runs=settings.OBSERVABILITY_SQLITE_MAX_RUNS,
        retention_days=settings.OBSERVABILITY_SQLITE_RETENTION_DAYS,
        initialize=False,
    )
