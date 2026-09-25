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

## Observed MCP integration

`observability.adapters.observe_orchestrator()` calls the additive
`ask_orchestrator_observed` stdio MCP tool. Its progress callback accepts the
upstream `TraceEventV1` messages, validates them at the Django boundary, and
stores only the allowlisted projection. The structured result is reduced to
status, task type, and reviewer/judge model attribution; draft, final answer,
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

Retention is process-local and FIFO bounded to 24 runs, 128 events per run, and
512 total events. Old events/runs are evicted and counted. Restarting Django
clears all state; there is no database, export, analytics, or telemetry.

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
