"""Report read-only status for the configured observability SQLite database."""

import json
import sqlite3

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from ._sqlite_store import configured_sqlite_store


class Command(BaseCommand):
    help = "Report read-only health and retention status for SQLite observability history."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit a machine-readable JSON document.",
        )

    def handle(self, *args, **options):
        store = configured_sqlite_store()
        try:
            status = store.maintenance_status()
        except (ImproperlyConfigured, OSError, sqlite3.Error) as error:
            raise CommandError(
                "Unable to inspect the configured SQLite observability database."
            ) from error

        if options["json"]:
            self.stdout.write(json.dumps(status, indent=2, sort_keys=True))
            return

        health = "healthy" if status["healthy"] else "attention required"
        self.stdout.write(f"SQLite observability status: {health}")
        self.stdout.write(
            f"Schema: {status['schema_version']} (expected {status['expected_schema_version']})"
        )
        self.stdout.write(f"Database bytes: {status['database_bytes']}")
        self.stdout.write("Table rows:")
        for table, count in status["table_counts"].items():
            self.stdout.write(f"  {table}: {count}")
        self.stdout.write(f"Active runs: {status['active_runs']}")
        self.stdout.write(f"Quick check: {', '.join(status['quick_check'])}")
        self.stdout.write(f"Foreign-key violations: {status['foreign_key_violations']}")
        retention = status["retention"]
        self.stdout.write("Configured-retention preview:")
        self.stdout.write(f"  Retention days: {retention['retention_days']}")
        self.stdout.write(f"  Maximum runs: {retention['max_runs']}")
        self.stdout.write(f"  Maximum events per run: {retention['max_events_per_run']}")
        self.stdout.write(f"  Runs eligible for pruning: {retention['candidate_runs']}")
        self.stdout.write(f"  Events eligible for pruning: {retention['candidate_events']}")
