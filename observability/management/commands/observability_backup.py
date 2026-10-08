"""Create a consistent owner-only backup of the configured SQLite history."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from observability.storage import SQLiteTraceStore, get_trace_store


class Command(BaseCommand):
    help = "Back up sanitized observability SQLite history with SQLite's backup API."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True)
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        store = get_trace_store()
        if not isinstance(store, SQLiteTraceStore):
            raise CommandError("SQLite observability history is not enabled.")
        requested_output = Path(options["output"]).expanduser()
        if requested_output.is_symlink():
            raise CommandError("Refusing to write through a symbolic link.")
        output = requested_output.resolve()
        if output == store.path:
            raise CommandError("Backup destination must differ from the active database.")
        if output.is_symlink():
            raise CommandError("Refusing to write through a symbolic link.")
        if output.exists() and not options["force"]:
            raise CommandError("Output already exists; pass --force to replace it.")
        if output.exists() and not output.is_file():
            raise CommandError("Backup destination must be a file path.")
        if output.exists():
            output.unlink()
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with sqlite3.connect(store.path) as source, sqlite3.connect(output) as destination:
            source.backup(destination)
        os.chmod(output, 0o600)
        self.stdout.write(self.style.SUCCESS("Sanitized observability backup completed."))
