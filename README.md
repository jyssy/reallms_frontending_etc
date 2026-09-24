# REALMS Frontend Local

A local Django dashboard for seeing the REALMS orchestrator's actual MCP tool
surface and sanitized activity observed through this frontend.

The browser never receives model-provider credentials or direct access to the
orchestrator process. Django acts as a narrow backend-for-frontend and returns
only operational metadata.

See [SETUP.md](SETUP.md) for the two commands needed to install and run the app.

## Current endpoints

| Endpoint | Purpose |
| --- | --- |
| `/` | Bootstrap 5 observability dashboard |
| `/api/v1/status/` | Local frontend and MCP configuration status |
| `/api/v1/tools/` | Tool catalog discovered from the real MCP server |
| `/api/v1/activity/` | Sanitized activity observed by this frontend process |

Activity is intentionally process-local and bounded. The dashboard does not
claim to observe MCP calls made by other clients.
