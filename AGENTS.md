## Imported Claude Cowork project instructions

## Shared Memory Rules

- This repository's stable `project_id` is `agentnexus`.
- Before substantial work, call the `agent-memory` MCP `bootstrap` tool with
  `project_id="agentnexus"`. Treat Project State as authoritative for current facts.
- At the start of a substantive user task, call `start_turn` so the raw request is
  recorded before memory extraction. Do not store secrets or unsupported guesses.
- Use `search` only when historical context is needed; old semantic memories do not
  override current Project State or the user's latest explicit instruction.
- Pass the latest `state_version` as `expected_version` for state changes. On
  `STATE_CONFLICT`, re-run `bootstrap` or `get_state` and never silently overwrite.
- After substantive changes, call `finish_turn` with the summary, changed files,
  decisions/TODO-related state updates, and the expected state version.
- If Mem0 is degraded, continue using Project State. If the MCP server itself is
  unavailable, continue the task and clearly report that shared-memory sync was skipped.
