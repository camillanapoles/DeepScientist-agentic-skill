# Linux agent setup completion checklist

Each item needs evidence from the target Linux host. The orchestration DB row
for the host object should reach `state=ready`.

## Environment and routing

- [ ] The managed object exists in the orchestration DB (`GET /api/orchestration/objects/<id>`).
- [ ] `environment` resolves to `linux` (not `windows_wsl`).
- [ ] `runner` resolves to `omp` (the Linux default), unless explicitly overridden.
- [ ] `GET /api/orchestration/environment/linux` returns the OMP/Linux profile.

## Pre-flight

- [ ] `uname -a` shows a native Linux kernel.
- [ ] `/etc/os-release` identifies the distribution.
- [ ] `id` shows a non-root user with `sudo` access (or the user accepted rootless install).
- [ ] `free -h` shows at least ~2 GB available memory.
- [ ] `df -h $HOME` shows sufficient free disk.
- [ ] `command -v curl git` succeeds.

## Prerequisites and OMP install

- [ ] Build tools and `python3-venv`/`python3-pip` are installed.
- [ ] Node.js 20 is installed (or `nvm` is configured).
- [ ] npm global prefix is user-owned (`$HOME/.npm-global`) and on `PATH`.
- [ ] `command -v omp` resolves to a Linux path.
- [ ] `omp --version` or `omp --help` succeeds.
- [ ] `~/.omp` exists and provider credentials are configured (or the user supplied a key).
- [ ] `command -v ds` resolves to a Linux path.
- [ ] `command -v uv` resolves to a Linux path.

## Validation

- [ ] `omp print --json --yes "Print exactly OK and exit."` returns OK.
- [ ] `ds doctor` reports `[ok]` for the runner (or an accepted startup-probe warning).
- [ ] Git identity is configured if `ds doctor` warned.
- [ ] `ds` starts detached and stays running.
- [ ] `curl -sI http://127.0.0.1:20999/api/health` returns HTTP 200.
- [ ] The Web UI loads in a browser.

## Event-driven state

- [ ] The host object reached `state=ready` via `validation.result { ok: true }`.
- [ ] The event log contains the full chain: `environment.detected → preflight.result → install.completed → config.completed → validation.result → state.ready`.
- [ ] A decision row exists for every ingressed event.
- [ ] No `blocked_human` or `failed` states remain unresolved.

## Handoff

- [ ] Final summary records host, runner (`omp`), provider route, and validation evidence.
- [ ] Any non-blocking warnings are recorded with an owner.
- [ ] If blocked, the report contains symptom, failed command, and the single human action required.
