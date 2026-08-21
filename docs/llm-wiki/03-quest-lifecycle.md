# 03 — Quest lifecycle (what actually happens on `ds run`)

```mermaid
sequenceDiagram
    actor U as user/agent
    participant DS as ds run / daemon
    participant QS as QuestService
    participant R as OmpRunner
    participant O as omp -p --mode=json
    participant Z as z.ai API
    U->>DS: ds run decision --quest-id 001 --message "…"
    DS->>QS: resolve quest root + snapshot
    DS->>R: RunRequest(model=glm-5-turbo, cwd=worktree)
    R->>O: spawn omp (prompt on stdin)
    O->>Z: completions
    Z-->>O: assistant blocks
    O-->>R: pi-wire JSON lines
    R-->>DS: runner.agent_message (message_end text only)
    DS->>QS: append_message(role=assistant)
    DS-->>U: {ok: true, output_text}
```

## Event translation rules (pi-wire → runner events)

| pi-wire event | Runner event | Notes |
|---|---|---|
| `message_end` (role=assistant) | `runner.agent_message` | Only source of final text |
| `message_start` / `message_update` | — ignored | Partials; would duplicate |
| `message_end` (role=user) | — ignored | Echo of the prompt |
| `session` (version) | — ignored | Handshake metadata |
| `tool_call`-ish payloads | `runner.tool_call` | Legacy paths kept |

Rule of thumb: **emit once, from `message_end`**. Thinking blocks are not
forwarded as text.

## Long runs

A real research quest is not one `ds run`: the daemon keeps the loop
(baselines → experiments → analysis → writing), the quest repo accumulates
branches/worktrees, and `Findings Memory` + the Research Map carry state
across rounds. Pause/take over/resume at any time; every step is auditable
in the quest repo and the orchestration DB.

Next: [04 — layout & separation](04-layout-and-separation.md).
