---
name: deepscientist-linux-agent-setup
description: Install, repair, and validate DeepScientist on a native Linux user environment with OMP (oh-my-pi / pi-coding-agent) as the default runner. Use for Linux prerequisites, OMP/pi-coding setup, Codex/Kimi as alternatives, event-driven environment selection via the orchestration API plus DB, deterministic decision gates, and final ds doctor plus Web UI verification. This is the default environment skill on Linux.
---

# DeepScientist Linux Agent Setup

## Scope and default status

This is the **default setup skill on Linux**. It is the Linux-native analogue
of `deepscientist-windows-wsl-setup`: same deterministic state-machine
discipline, same DB-as-source-of-truth orchestration, but targeting a native
Linux user environment rather than Windows + WSL.

Key defaults:

- **Target environment:** native Linux (Ubuntu, Debian, Fedora, Arch, etc.).
- **Default runner:** OMP — `@oh-my-pi/pi-coding-agent`, invoked as `omp`.
  Codex, Kimi, Claude, and OpenCode remain available as alternative runners.
- **Development environment:** Linux is also the default for GitOps/CI, audits,
  and repository development — unlike the Windows/WSL skill, here the CI host
  and the target environment are the same shape.
- **Source of truth:** the orchestration database, not in-memory assumptions.

## When to use

Use this skill when:

- installing DeepScientist on a Linux machine for the first time;
- repairing a Linux install where `ds doctor` or the runner fails;
- switching the default runner to OMP/pi-coding-agent;
- an orchestration API/DB session routes a target to `environment=linux`;
- validating that a Linux agent host is ready end-to-end.

Do **not** use this skill for a Windows host or a WSL2 distro — the
orchestration router will emit `environment=windows_wsl` and point to
`deepscientist-windows-wsl-setup` instead.

## Source-of-truth refresh

Before changing anything, read:

1. `README.md`
2. `docs/en/00_QUICK_START.md`
3. `docs/en/15_CODEX_PROVIDER_SETUP.md` (for Codex provider routes)
4. `docs/en/09_DOCTOR.md`
5. this skill and its references:
   - `orchestration/event-driven-playbook.md`
   - `orchestration/event-driven-playbook.md`
   - `checklists/completion-checklist.md`
   - `references/omp-pi-coding-notes.md`

## Event-driven, deterministic orchestration

This skill is driven by the orchestration engine, not by an ad-hoc sequence:

1. A managed object representing the target host is created via the API
   (`POST /api/orchestration/objects`) with `environment=linux` (or it is
   resolved from an incoming `environment.detected` event).
2. Each phase is advanced by posting an event to
   `POST /api/orchestration/objects/<id>/events`. The engine returns the
   deterministic `Decision` — next state, actions, and emitted events.
3. The agent executes the returned actions; it does **not** improvise the
   state machine.
4. The database is the single source of truth for object state, the event
   log, and the decision audit trail.

The environment router selects the runner and setup skill from the DB object's
`environment` field. For Linux it resolves to:

```json
{
  "environment": "linux",
  "runner": "omp",
  "setup_skill": "deepscientist-linux-agent-setup",
  "package_manager": "apt"
}
```

The playbook is rendered by **Pydantic schemas** (see
`src/deepscientist/orchestration/schemas.py` — `EventDrivenPlaybook`), not by a
text templating engine. Every dynamic field is a typed attribute, and LLM-
assisted decisions are wrapped by **Instructor** so responses are strictly
validated against Pydantic models before reaching the deterministic engine.

## Closed-loop execution (SOTA / OSWorld 2.0)
<!-- closed-loop -->

The agent operates in a closed loop:

1. **Read** the current object state from the DB.
2. **Generate** one structured action — either deterministically from the state
   machine, or via Instructor with a Pydantic response model (`InstructorActionProposal`).
3. **Validate imperatively** via `EventDrivenPlaybook.closed_loop_verdict()` before
   advancing. The LLM is never trusted to self-report completion.
4. **Ingest** the result as a typed event; the engine returns the next decision.
5. Repeat until `state=ready`.

This eliminates hallucinated state transitions and infinite loops: every
transition is a pure function of `(current state, event)`, and every action is
structurally typed before execution.


Stop and surface a `BLOCKED-HUMAN` decision when:

- package installation requires `sudo` and the user is not in `sudoers`;
- no API key / provider credential is available for the chosen model provider;
- a network/proxy blocker cannot be resolved from the machine state;
- `omp` or `ds doctor` fails after the documented repair steps;
- a destructive action (removing an existing install, overwriting config) has
  not been explicitly approved.

## Pre-flight

Detect the distribution and privileges:

```bash
uname -a
cat /etc/os-release 2>/dev/null || true
command -v sudo || true
id
command -v curl git node npm python3 pip3 bun 2>/dev/null || true
free -h
df -h "$HOME"
```

Verify Linux (not WSL interop contamination):

```bash
grep -qi microsoft /proc/version 2>/dev/null && echo "WSL detected — consider the Windows/WSL skill" || echo "native Linux"
```

## Linux prerequisites

On Debian/Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg git sudo build-essential \
  python3-venv python3-pip unzip
```

On Fedora/RHEL:

```bash
sudo dnf install -y curl git sudo @development-tools python3 python3-pip unzip
```

On Arch:

```bash
sudo pacman -Syu --needed curl git sudo base-devel python python-pip unzip
```

Node.js 20 (Debian/Ubuntu shown; use `nvm` or the distro packages elsewhere):

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
  | sudo gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
printf 'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main\n' \
  | sudo tee /etc/apt/sources.list.d/nodesource.list
sudo apt-get update
sudo apt-get install -y nodejs
```

User-local npm prefix:

```bash
mkdir -p "$HOME/.npm-global" "$HOME/.local/bin"
npm config set prefix "$HOME/.npm-global"
grep -qxF 'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"' "$HOME/.profile" 2>/dev/null \
  || echo 'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"' >> "$HOME/.profile"
grep -qxF 'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"' "$HOME/.bashrc" 2>/dev/null \
  || echo 'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
. "$HOME/.profile"
```

## Installing OMP (pi-coding-agent) — the default runner

The preferred install is the official script, with Bun as an alternative:

```bash
# Official installer (macOS / Linux)
curl -fsSL https://omp.sh/install | sh

# — or — Bun global install
# bun install -g @oh-my-pi/pi-coding-agent
```

Verify:

```bash
command -v omp
omp --version 2>/dev/null || omp --help | head -5
```

OMP reads its state from `~/.omp/`. On first run it inherits skills/MCP from
`.codex`, `.claude`, etc. — no migration script is needed.

## Installing DeepScientist

```bash
npm install -g @researai/deepscientist
curl -LsSf https://github.com/astral-sh/uv/releases/latest/download/uv-installer.sh | sh
. "$HOME/.profile"
command -v ds omp uv
```

All binaries must resolve to Linux paths under `$HOME/.npm-global/bin`,
`$HOME/.local/bin`, or `/usr/local/bin` — never to Windows-mounted paths.

## Provider / model configuration for OMP

OMP supports 60+ providers via `~/.omp/agent/models.yml`. Use the provider's
documented format; a typical OpenAI-compatible entry uses an environment key.

For a relay / self-hosted provider, set the key in the environment (or via
DeepScientist `runners.yaml -> runners.omp.env`) rather than hard-coding it:

```bash
export OMP_API_KEY="<your-key>"   # name per provider docs
```

Validate directly first:

```bash
omp print --json --yes "Print exactly OK and exit."
```

If OMP is unavailable or the user prefers Codex, the engine routes to
`runner=codex` and the standard `docs/en/15_CODEX_PROVIDER_SETUP.md` flow
applies.

## Validation

Run in order, using the same environment as the chosen runner:

```bash
whoami
command -v node npm git ds omp uv
omp print --json --yes "Print exactly OK and exit."
ds doctor
```

`ds doctor` must show Codex/OMP readiness as `[ok]` or an accepted startup
probe warning. Then launch detached:

```bash
nohup ds >"$HOME/.deepscientist-daemon.log" 2>&1 &
```

Confirm the Web UI:

```bash
curl -sI http://127.0.0.1:20999/api/health | head -1
```

Then open `http://127.0.0.1:20999` in a browser.

## API-driven event flow (example)

```bash
HOST=http://127.0.0.1:20999
# 1. create a managed host object
OBJ=$(curl -s -XPOST $HOST/api/orchestration/objects \
  -H 'Content-Type: application/json' \
  -d '{"name":"my-linux-laptop","environment":"linux","kind":"agent_host"}')
ID=$(echo "$OBJ" | python3 -c 'import sys,json;print(json.load(sys.stdin)["object"]["object_id"])')

# 2. drive the deterministic state machine
curl -s -XPOST $HOST/api/orchestration/objects/$ID/events \
  -H 'Content-Type: application/json' \
  -d '{"event_type":"preflight.result","payload":{"ok":true}}'
```

Each response carries the `decision` (next state, actions) and the updated
`object`. The database (`~/DeepScientist/orchestration/state.db`) holds the
event log and decision audit trail.

## Troubleshooting

- `omp: command not found` — re-source `~/.profile`; confirm
  `$HOME/.local/bin` is on `PATH` (the official installer places `omp` there).
- `omp print` auth failure — configure the provider key in `~/.omp` or
  `runners.omp.env`; validate outside `ds` first.
- `ds doctor` slow first run — `uv` downloads the Python runtime; wait.
- Native Windows paths in `command -v` output — you are in WSL; use the
  Windows/WSL skill instead.

## References

- `orchestration/event-driven-playbook.md`
- `src/deepscientist/orchestration/schemas.py` (Pydantic playbook models)
- `src/deepscientist/orchestration/llm.py` (Instructor client)
- `checklists/completion-checklist.md`
- `references/omp-pi-coding-notes.md`
- `docs/en/00_QUICK_START.md`
- `docs/en/15_CODEX_PROVIDER_SETUP.md`
- `docs/en/09_DOCTOR.md`
