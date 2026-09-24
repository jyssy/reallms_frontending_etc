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

Bootstrap styling comes from a pinned CDN URL with an integrity hash. The app
still renders as semantic HTML if that asset is unavailable, but full styling
requires network access until Bootstrap is vendored locally.
