# REALMS Frontend Local

A local Django dashboard for the REALMS orchestrator's MCP catalog and
metadata-only run observability. Django is a narrow backend-for-frontend: the
browser never connects to stdio MCP or provider APIs.

See [SETUP.md](SETUP.md) for installation, live startup, and synthetic mock mode.

## Views and endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/` | Existing MCP discovery dashboard |
| GET | `/models/` | REALMS provider and model activity |
| GET | `/runs/` | Ordered Orchestrator run traces |
| GET | `/api/v1/status/` | Frontend, MCP configuration, and contract status |
| GET | `/api/v1/tools/` | Actual tool catalog from `tools/list` |
| GET | `/api/v1/activity/` | Sanitized activity from this frontend process |
| GET | `/api/v1/observability/models/` | Model/provider activity projection |
| GET | `/api/v1/observability/runs/` | Bounded run and event snapshots |

All observability responses use `Cache-Control: no-store`. Browser pages poll
and replace their current snapshot, so reconnecting naturally resumes from the
latest retained state without building an unbounded client-side history.

## Ask Orchestrator workflow trail

The run page makes the most recent retained run the primary visualization. Its
six stages are derived deterministically from contract events:

1. Route (`router`)
2. Retrieve (`retrieval`, `embedding`, and `reranker`)
3. Specialist (`specialist` / reviewer role)
4. Judge (`judge`)
5. Revision (`revision`)
6. Complete (`run.completed`)

The selected run opens with end-to-end status and elapsed time, observed model
count, provider attempts/retries, and work classification. Its workflow shows
relative duration marks alongside status, event count, purpose, and model
attribution. Retained runs are presented as `Run N of M`; the shortened UUID is
kept as secondary trace evidence rather than used as the run number. A per-run
model roster separates observed identities from
configured expectations. An operational-evidence panel reports retrieval
candidate/selection counts, context use, embedding batch size, reranking counts,
provider activity, and reported phase time when those measurements exist.

Actor cards can also show a bounded retained throughput range, median generation
time, median time to first token, and quantization when those values are
explicitly reported by provider-attempt events. Throughput is calculated
deterministically from output tokens and generation duration; it is never
accepted as an opaque upstream claim. Cost, CPU, and memory remain `not
reported`. The UI never estimates missing measurements.
All rendered timestamps show America/New_York Eastern time (automatically EST
or EDT as appropriate) alongside UTC.
Reviewer and judge names are read from each sanitized structured orchestration
result and marked `observed`. Model upgrades therefore appear automatically on
the next retained call without a frontend code or configuration change. The
frontend also accepts a safe `model_roles.router` value when the upstream result
provides one; until then, the router can only be shown as a configured
expectation. The model APIs and visible model listings expose only the
orchestration actors: router, reviewer, and judge.
Embedding and reranking remain retrieval measurements when the trace reports
them, but are not presented as model actors. Missing names remain `not
reported`; the UI does not infer identity from provider type.

Optional configured expectations are supplied entirely through server-side
environment variables; no model identifiers are hardcoded in application
settings. The router is expected to run through the local Ollama service, but
the browser does not connect to Ollama directly. Observed identities take
precedence over configured expectations, and both are shown when they differ.
Synthetic fixtures contain no observed model identities. Their actor slots are
displayed as demo placeholders rather than being relabeled as live observations.

An actors-and-handoff map separates the observed Ask Orchestrator boundary from
the external execution client. Set `REALMS_OBSERVABILITY_EXECUTOR=Codex` or use
another safe label such as `Claude`. It is always displayed as
`configured · external`: contract v1 does not observe repository edits or other
executor activity after the advisory MCP result is returned.

Structured results may report router, reviewer, and judge identities. The run
page also aggregates bounded retained paths as task class → observed reviewer
model → result. Rows are included only when all three values were reported.

Provider-level performance is an optional additive integration. The frontend
accepts the following allowlisted `provider.attempt` metadata when the upstream
orchestrator supplies it:

```json
{
  "provider": "local",
  "operation": "routing",
  "role": "router",
  "model": "qwen2.5:1.5b",
  "quantization": "Q4_K_M",
  "input_tokens": 320,
  "output_tokens": 24,
  "generation_duration_ms": 600,
  "time_to_first_token_ms": 120,
  "load_duration_ms": 30,
  "context_window_tokens": 32768
}
```

`role` must be `router`, `reviewer`, or `judge`; `model` and `quantization`
must pass conservative label validation. Token counts are bounded at two
million and duration measurements at 24 hours. Performance samples require an
explicit role, model, and successful provider attempt. The frontend does not
infer role from provider or operation, does not accept `tokens_per_second`, and
does not mix measurements from different model or quantization identities.
Until upstream emits these optional fields, actor performance remains visibly
`not reported`.

## Observed MCP integration

`observability.adapters.observe_orchestrator()` calls the additive
`ask_orchestrator_observed` stdio MCP tool. Its progress callback accepts the
upstream `TraceEventV1` messages, validates them at the Django boundary, and
stores only the allowlisted projection. The structured result is reduced to
status, task type, and router/reviewer/judge model attribution; draft, final answer,
paths, policy identifiers, warnings, component messages, and errors are
discarded.

The adapter is intentionally a server-side integration seam, not a browser
execution endpoint. Existing tool discovery still performs only `tools/list`
and does not make model calls. A separately approved server-side workflow can
call the adapter when it needs observation. Because the upstream transport is
stdio and observations are scoped to a single call, this process cannot see
calls made by other MCP clients.

## Contract v1

The canonical browser-facing contract is in `observability/contract.py`.
Accepted events have:

- `contract_version`: exactly `1`;
- a canonical UUID `run_id`, positive `sequence`, and timezone-aware
  `timestamp`;
- an allowlisted `event_type`, `component`, and lifecycle `status`;
- an optional bounded non-negative `duration_ms`; and
- allowlisted primitive `metadata` only.

Unknown top-level and metadata fields are never copied into public state.
Progress messages over 16 KiB and malformed events are ignored. The public
contract never includes credentials, prompts, completions, source chunks,
provider bodies, scanner output, local paths, policy content, environment
values, or raw exception text.

Events are deduplicated by `(run_id, sequence)` and presented in sequence order,
including when progress arrives out of order. A `run.completed` event makes a
run terminal. Otherwise it remains `in_progress`, which truthfully covers an
active call, disconnect, timeout, cancellation, or partial/degraded stream.

By default retention is process-local and FIFO bounded to 24 runs, 128 events
per run, and 512 total events. Old events/runs are evicted and counted, and a
Django restart clears that default store.

## Optional SQLite history and portable exports

SQLite history is an explicit opt-in for a single local frontend instance. It
uses Python's standard `sqlite3` module and does not enable Django ORM storage
or require migrations. The database contains only the same allowlisted event
fields and sanitized result/model attribution already permitted in browser
responses. It never stores prompts, completions, source content, provider
bodies, paths, policy text, environment values, or raw errors.

Enable it with a deliberately chosen server-side path:

```sh
REALMS_OBSERVABILITY_STORAGE=sqlite \
REALMS_OBSERVABILITY_SQLITE_PATH=/private/local/path/realms-observability.sqlite3 \
uv run python manage.py runserver 127.0.0.1:8000
# to kill port 8000
lsof -ti :8000 | xargs kill
# or...
Ctrl+C
```

The defaults retain at most 500 runs for seven days, with at most 128 events
per run. `REALMS_OBSERVABILITY_SQLITE_MAX_RUNS` and
`REALMS_OBSERVABILITY_SQLITE_RETENTION_DAYS` can narrow or extend those bounds.
The run API still returns no more than the newest 24 runs per response. SQLite
files are created owner-readable/writable (`0600`), and the configured local
path is never returned to the browser.

With SQLite mode and its path configured, all four versioned observability
tables can be exported in stable order:

```sh
# One portable JSON document with a manifest, row counts, and table checksums.
uv run python manage.py observability_export \
  --format json --output ./observability-export.json

# A private directory of CSV tables plus manifest.json.
uv run python manage.py observability_export \
  --format csv --output ./observability-export

# A consistent SQLite copy made through the SQLite backup API.
uv run python manage.py observability_backup \
  --output ./observability-backup.sqlite3
```

Each command refuses to replace its known outputs unless `--force` is passed.
Exported files and backups are `0600`; CSV export directories are `0700`.
Exports remain sensitive operational metadata because they include run timing
and safe model labels, so store and share them accordingly. No telemetry or
automatic export is enabled.

The configured database can also be inspected and its existing retention
policy applied from the environment. These commands use only the server-side
path from `REALMS_OBSERVABILITY_SQLITE_PATH`; they do not accept SQL, table
names, or an alternate input database:

```sh
# Read-only schema, integrity, row-count, and retention status.
uv run python manage.py observability_status

# The same status as machine-readable JSON.
uv run python manage.py observability_status --json

# Preview rows eligible under the configured age, run, and per-run event limits.
uv run python manage.py observability_prune

# Apply exactly that configured retention policy.
uv run python manage.py observability_prune --confirm
```

`observability_prune` is always a dry run unless `--confirm` is present. It does
not accept an ad hoc age or limit override, and it does not expose run IDs or
stored metadata. Create an `observability_backup` first if history eligible for
pruning may need to be recovered. Both commands refuse non-SQLite mode, a
missing database, or a configured symbolic-link path. Database compaction
(`VACUUM`), arbitrary SQL, reset/clear operations, and migrations are
intentionally not provided because they require a separate maintenance window
and authorization.

## Model activity states

Model identity and activity are deliberately separate:

- `configured`: a validated label in the frontend-owned safe catalog;
- `observed`: named by the structured result of an observed call;
- `skipped`: an upstream trace explicitly marked the role skipped;
- `fallback`: allowlisted upstream trace metadata explicitly reported fallback;
  and
- `not-reported`: component activity was observed but the upstream interface
  did not report a model name.

States can coexist. For example, a configured reviewer can also be observed and
marked fallback. Provider cards report only local/remote attempts, retries, and
degraded status—not provider response content.

## Trust boundary

The frontend never reads the orchestrator's dotenv file and never returns its
configured filesystem path. Model labels are supplied independently through
validated frontend settings. The browser APIs are observation-only GET routes;
they cannot submit prompts, execute approved plans, rebuild an index, browse
files, or invoke arbitrary tools.
