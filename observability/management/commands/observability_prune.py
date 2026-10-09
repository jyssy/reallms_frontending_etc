"""Preview or apply configured retention to SQLite observability history."""

import json
import sqlite3

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from ._sqlite_store import configured_sqlite_store


class Command(BaseCommand):
    help = "Preview configured SQLite observability retention, or apply it with --confirm."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Apply the configured retention policy; otherwise perform a dry run.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit a machine-readable JSON document.",
        )

    def handle(self, *args, **options):
        store = configured_sqlite_store()
        try:
            result = store.prune_retention(confirm=options["confirm"])
        except (ImproperlyConfigured, OSError, sqlite3.Error) as error:
            raise CommandError(
                "Unable to prune the configured SQLite observability database."
            ) from error

        if options["json"]:
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
            return

        mode = "applied" if result["applied"] else "dry run"
        self.stdout.write(f"SQLite observability retention: {mode}")
        self.stdout.write(f"Retention days: {result['retention_days']}")
        self.stdout.write(f"Maximum runs: {result['max_runs']}")
        self.stdout.write(f"Maximum events per run: {result['max_events_per_run']}")
        self.stdout.write(f"Runs eligible for pruning: {result['candidate_runs']}")
        self.stdout.write(f"Events eligible for pruning: {result['candidate_events']}")
        self.stdout.write(f"  Age-expired runs: {result['old_runs']}")
        self.stdout.write(f"  Runs beyond maximum: {result['overflow_runs']}")
        self.stdout.write(f"  Excess per-run events: {result['excess_events']}")
        if not result["applied"]:
            self.stdout.write("No rows changed. Re-run with --confirm to apply this plan.")
