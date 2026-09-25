# Local setup

This project uses `uv` to manage its own Python environment and dependencies.
No global Django installation is needed.

## Prerequisites

- Python 3.13
- [`uv`](https://docs.astral.sh/uv/)

## First-time setup

From the repository root, resolve and install the application and development
dependencies:

```sh
uv sync --group dev
```

`uv` creates or updates the repository-local `.venv` automatically. You do not
need to activate it.

## Start the Django app

```sh
uv run python manage.py runserver 127.0.0.1:8000
```

Open <http://127.0.0.1:8000/> in a browser. Stop the server with `Ctrl-C`.

The server binds only to the local machine. Keep that loopback address unless a
separate security review explicitly approves network access.

## Optional environment activation

Most commands should use `uv run`, but an interactive shell can activate the
same environment with:

```sh
source .venv/bin/activate
```

Leave it with:

```sh
deactivate
```

## Dashboard data

The dashboard does not require browser-side credentials. Django connects to the
orchestrator through a server-side MCP adapter and sends only sanitized
operational metadata to the browser.

By default, the adapter looks for the sibling repository at
`../orchestrator_code`. Override that server-side root when necessary; the app
still derives the fixed `.venv/bin/python` and `mcp_server.py` paths:

```sh
REALMS_ORCHESTRATOR_ROOT=/absolute/path/to/orchestrator_code \
uv run python manage.py runserver 127.0.0.1:8000
```

These values are read only by Django and are never returned to the browser.

The observability pages are available at:

- <http://127.0.0.1:8000/models/> for provider/model activity;
- <http://127.0.0.1:8000/runs/> for run traces; and
- <http://127.0.0.1:8000/api/v1/status/> for the local health check.

The run pages do not start model calls. They show only calls routed through the
server-side `observe_orchestrator()` integration seam in this Django process.
Calls made by unrelated MCP clients are not visible.

## Browser-safe model catalog

Configured model names are optional and independent from the orchestrator's
private configuration. Set only public model labels that are safe to display:

```sh
REALMS_OBSERVABILITY_ROUTER_MODEL=router-label \
REALMS_OBSERVABILITY_EMBEDDING_MODEL=embedding-label \
REALMS_OBSERVABILITY_RERANKER_MODEL=reranker-label \
REALMS_OBSERVABILITY_REVIEWER_MODEL=reviewer-label \
REALMS_OBSERVABILITY_JUDGE_MODEL=judge-label \
uv run python manage.py runserver 127.0.0.1:8000
```

Labels are limited to conservative model-identifier characters and 128
characters. Missing or invalid labels are omitted rather than echoed.

Observed model-backed calls use a separate 305-second timeout so the existing
15-second discovery timeout remains unchanged. Override it server-side only
when needed:

```sh
REALMS_OBSERVED_MCP_TIMEOUT_SECONDS=240 \
uv run python manage.py runserver 127.0.0.1:8000
```

## Synthetic mock mode

Develop the new pages without an orchestrator or provider credentials:

```sh
REALMS_OBSERVABILITY_MOCK=true \
uv run python manage.py runserver 127.0.0.1:8000
```

Mock APIs and pages are visibly labeled `Synthetic fixture`. The fixed fixture
contains invented metadata and does not invoke MCP or any model provider.

## Local verification

Checks must not load a repository dotenv file or make live provider calls:

```sh
PYTHON_DOTENV_DISABLED=1 uv run --group dev pytest -q
PYTHON_DOTENV_DISABLED=1 uv run --group dev ruff check .
```

Bootstrap styling comes from a pinned CDN URL with an integrity hash. The app
still renders as semantic HTML if that asset is unavailable, but full styling
requires network access until Bootstrap is vendored locally.
