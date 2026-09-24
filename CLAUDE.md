@AGENTS.md

# Claude Code-specific guidance

- Use plan mode for read-only design work. Enter an edit-capable session only
  after the user has approved the exact task, repository, allowed paths, denied
  paths, and constraints required by the guarded orchestration workflow.
- When that workflow requires architectural advice, use the registered
  orchestrator MCP tools with this repository as `repo_root`; evaluate their
  output against the live repository rather than applying it blindly.
- Do not call `index_codebase` or run an orchestrator index command unless the
  human explicitly requests an index change.
- Additional directories supplied to Claude are context, not write authority.
  In particular, treat the sibling `orchestrator_code` repository as read-only
  unless a separate approval explicitly targets it.
- Keep permission requests narrow. Do not add broad persistent filesystem access,
  bypass permission checks, or approve commands on the user's behalf.
- Prefer repository scripts and the selected package manager once they exist.
  Do not start persistent dev servers, open browsers, install dependencies, or
  make live provider calls unless the user has authorized those external actions.
