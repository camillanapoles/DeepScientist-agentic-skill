# Event-driven orchestration playbook (Linux + OMP)

This is the Linux-target counterpart to the deterministic state machine in
`src/deepscientist/orchestration/`. It documents how a managed **agent host**
object moves through states when driven by events, and how the database is the
single source of truth.

## Core contract

- One managed object = one target environment (a Linux host, a WSL distro, etc.).
- The object's `environment` field — set via API or an `environment.detected`
  event — decides the runner and setup skill. Linux resolves to `omp` and this
  skill.
- Every transition is an **event**; the engine returns a deterministic
  **Decision** with `next_state`, `actions`, and `emitted_events`.
- The agent executes actions; it does not decide state.
- State, the event log, and the decision audit trail live in SQLite under
  `<home>/orchestration/state.db`.


## Pydantic + Instructor (typed schemas only)

The runbook is rendered by Pydantic models, not text templates:

- `src/deepscientist/orchestration/schemas.py` defines `EventDrivenPlaybook`,
  `Decision`, `Action`, `ManagedObject`, and `EnvironmentProfile` as typed
  `BaseModel`s. `EventDrivenPlaybook.render_markdown()` produces the runbook.
- `src/deepscientist/orchestration/llm.py` wraps every LLM call with
  **Instructor** (`response_model=...`), forcing strict Pydantic output.
- Every proposal is a `InstructorActionProposal`; the service validates it
  imperatively before it can be ingested as an event.

## Closed-loop gate (closed-loop, OSWorld 2.0)

`EventDrivenPlaybook.closed_loop_verdict()` is the imperative progress gate:
it returns `ready_to_advance=false` (with blockers) whenever the object is
blocked, failed, missing an action runner, or otherwise unsafe to advance.
The agent may not proceed until the verdict is clean — this prevents
hallucinated completions and infinite loops in long-horizon runs.

## Event ingress

`POST /api/orchestration/objects/<object_id>/events`

```json
{ "event_type": "preflight.result", "payload": { "ok": true } }
```

The response:

```json
{
  "ok": true,
  "object": { "object_id": "...", "state": "provisioning", "environment": "linux", "runner": "omp" },
  "decision": {
    "previous_state": "preflight",
    "next_state": "provisioning",
    "actions": [{ "kind": "install_runtime", "runner": "omp", "environment": "linux" }],
    "emitted_events": [{ "event_type": "install.requested" }],
    "rationale": "Preflight passed; requesting runtime installation."
  }
}
```

## State machine (Linux path)

```
pending
  │  environment.detected { environment: "linux" }
  ▼
preflight ── preflight.result { ok: false, requires_human } ──▶ blocked_human
  │  preflight.result { ok: true }
  ▼
provisioning ── install.completed { ok: false } ──▶ failed / blocked_human
  │  install.completed { ok: true }
  ▼
configuring ── config.completed { ok: true }
  ▼
validating ── validation.result { ok: true }
  ▼
ready  (action: launch)
```

`blocked_human` resumes via `human.action_resolved` with a `resume_state`.

## Actions mapped to Linux commands

| Action kind | What the agent does on Linux |
|---|---|
| `run_preflight` | `uname -a`, `/etc/os-release`, `id`, `free -h`, check for `curl/git/node/python3` |
| `install_runtime` | `curl -fsSL https://omp.sh/install \| sh`, `npm i -g @researai/deepscientist`, install `uv` |
| `configure_runtime` | write `~/.omp` provider config; set npm prefix/PATH; configure `runners.yaml` |
| `validate` | `omp print --json --yes "Print exactly OK and exit."`, `ds doctor` |
| `launch` | start `ds` detached; `curl -sI http://127.0.0.1:20999/api/health` |
| `block_on_human` | report the blocker and the single requested human action |

## Event-bus side effects

After a decision is persisted, the service publishes events on an in-process
`EventBus`. Subscribers may:

- trigger a background install/validation worker;
- push a UI/SSE update;
- fan out to a connector (QQ/Telegram/etc.).

Subscribers run after the DB commit and are isolated so a failing subscriber
cannot roll back orchestration state.

## Environment selection via API

The router is pure and queryable:

`GET /api/orchestration/environment/linux` →

```json
{ "profile": { "environment": "linux", "runner": "omp", "setup_skill": "deepscientist-linux-agent-setup", "package_manager": "apt" } }
```

`GET /api/orchestration/environment/wsl` →

```json
{ "profile": { "environment": "windows_wsl", "runner": "codex", "setup_skill": "deepscientist-windows-wsl-setup", "package_manager": "apt-in-wsl" } }
```

This is how context/environment selection is **API-driven, event-driven**: the
caller (UI, connector, agent) asks the backend which environment/runner to use,
and the answer is deterministic and stored on the object rather than inferred
from the CI host.

## Consistency rules

1. The DB row is authoritative; in-memory copies are projections.
2. A decision is recorded for every ingressed event (auditability).
3. Unknown `(state, event)` pairs are deterministic no-ops, never crashes.
4. Terminal states (`ready`, `failed`, `closed`) ignore further lifecycle events.
5. The engine has no network/FS side effects; the service layer owns persistence
   and bus publication.
