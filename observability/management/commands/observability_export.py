"""Export the fixed sanitized observability schema in a portable form."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from observability.storage import (
    TABLE_SPECS,
    SQLiteTraceStore,
    csv_bytes,
    export_manifest,
    get_trace_store,
    table_rows,
)


def _write_private(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise CommandError("Refusing to write through a symbolic link.")
    path.write_bytes(content)
    os.chmod(path, 0o600)


class Command(BaseCommand):
    help = "Export all sanitized observability tables as versioned JSON or CSV."

    def add_arguments(self, parser):
        parser.add_argument("--format", choices=("json", "csv"), required=True)
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
        tables = table_rows(store.path)
        if options["format"] == "json":
            self._export_json(output, tables, options["force"])
        else:
            self._export_csv(output, tables, options["force"])
        self.stdout.write(self.style.SUCCESS("Sanitized observability export completed."))

    def _export_json(self, output: Path, tables: dict, force: bool) -> None:
        if output.is_symlink():
            raise CommandError("Refusing to write through a symbolic link.")
        if output.exists() and not force:
            raise CommandError("Output already exists; pass --force to replace it.")
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {"manifest": export_manifest(tables), "tables": tables}
        content = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
        _write_private(output, content)

    def _export_csv(self, output: Path, tables: dict, force: bool) -> None:
        known_paths = [output / f"{name}.csv" for name in TABLE_SPECS]
        known_paths.append(output / "manifest.json")
        if output.is_symlink() or any(path.is_symlink() for path in known_paths):
            raise CommandError("Refusing to write through a symbolic link.")
        if any(path.exists() for path in known_paths) and not force:
            raise CommandError("Export files already exist; pass --force to replace them.")
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(output, 0o700)
        manifest = export_manifest(tables)
        for name, (columns, _) in TABLE_SPECS.items():
            content = csv_bytes(columns, tables[name])
            _write_private(output / f"{name}.csv", content)
            manifest["tables"][name]["sha256"] = hashlib.sha256(content).hexdigest()
        _write_private(
            output / "manifest.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        )
