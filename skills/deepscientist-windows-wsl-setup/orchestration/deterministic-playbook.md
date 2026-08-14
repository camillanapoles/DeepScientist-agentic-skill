# Deterministic WSL setup playbook

Use this as the execution state machine. The agent may choose exact commands
adapted to the live machine, but it must not skip a gate or mark a state complete
without the corresponding validation.

## Operating boundaries

- **Current agent/CI default:** Linux. This Linux environment is used to audit
  the skill, run tests, and review changes. It is not the target Windows host.
- **Target user environment:** Windows 10/11 host plus WSL2. Most setup commands
  must be executed in Windows PowerShell or inside the selected WSL distro.
- **Evidence rule:** do not infer Windows state from this Linux sandbox. Collect
  evidence from the target host and distro before changing configuration.
- **Human gate:** stop at `BLOCKED-HUMAN` when the required action cannot be
  performed by the agent (reboot, GUI login, firewall, proxy LAN toggle, secret
  entry).

## State matrix

| State | Observation | Decision | Action | Validation | Next state |
|---|---|---|---|---|---|
| S0 source refresh | Official README, WSL guide, and this skill are read | Docs differ from memory or commands | Prefer current checked-in/official docs; record exact pins | Commands and version pins cited from docs | S1 inventory |
| S1 inventory | `wsl -l -v`, `wsl --status`, memory, Windows build, admin status collected | WSL command unavailable or Windows version unsupported | Report missing prerequisite; stop for Windows feature/admin action | Output contains WSL status and host facts | S2 preflight |
| S2 preflight | Test `wsl -d <distro> -- echo hello` or install candidate | `HCS_E_CONNECTION_TIMEOUT`, Hyper-V disabled, or pending reboot | `wsl --shutdown`; if still failing set `BLOCKED-HUMAN` for reboot/BIOS/firewall | WSL command returns `hello` | S3 distro select |
| S3 distro select | Existing distros are inspected | Existing WSL2 Ubuntu is usable and user permits reuse | Select it; otherwise create dedicated distro such as `DeepScientist`; never reuse `docker-desktop` by default | Selected distro is WSL2 and starts as non-root user | S4 interop lock |
| S4 interop lock | `/etc/wsl.conf` in selected distro | Windows PATH injection is enabled or unknown | Set `[interop] appendWindowsPath=false`; terminate distro | `command -v node npm codex ds` does not resolve under `/mnt/c/` | S5 Linux prerequisites |
| S5 Linux prerequisites | Ubuntu version, `apt`, `git`, `curl`, build tools, Python venv packages | Missing baseline packages | Install only Linux packages adapted to Ubuntu 22.04/24.04 | Required package manager and build tools exist | S6 Node/npm |
| S6 Node/npm | `node --version`, `npm --version`, npm prefix | Node missing, wrong major, or global installs require sudo | Install Node.js 20 from Linux apt source; set user-local npm prefix | `node` is v20.x and npm global prefix is under `$HOME` | S7 product install |
| S7 product install | `ds`, `codex`, `uv` resolutions | Missing binaries or Windows-mounted binaries | Install `@researai/deepscientist`, pinned Codex CLI from official guide, and `uv` in Linux user paths | All three resolve to Linux paths and versions match documented pins | S8 auth route |
| S8 auth route | Existing `~/.codex/auth.json`, ChatGPT subscription, API key, relay/Bailian profile facts | No valid auth route | Choose direct ChatGPT reuse, direct API key, or named provider profile; request secret only if missing | `codex exec` smoke works from a clean non-interactive shell | S9 network route |
| S9 network route | Direct curl to OpenAI/ChatGPT/GitHub and proxy listener facts | Direct network works | Leave proxy unset | `curl --max-time` returns expected HTTP status | S10 doctor |
| S9 network route | Direct network fails; Windows proxy exists but loopback-only | Agent cannot enable LAN access | Set `BLOCKED-HUMAN: enable Allow LAN` | Human confirms non-loopback listener | S9 network route |
| S9 network route | Non-loopback proxy candidate is known | Test candidate before persisting | Write `~/.wsl-proxy-env`; source it from `.profile` and `.bashrc` | `curl -x http://<host-ip>:<port>` succeeds | S10 doctor |
| S10 doctor | Same shell as S8/S9 has auth and proxy | `ds doctor` reports Codex failure | Diagnose binary, profile, auth, network, and provider config in that order | Output contains `[ok] Codex CLI` or accepted startup-probe warning | S11 launch |
| S11 launch | Start `ds` detached from the agent shell | Process exits when agent shell closes | Start separate WSL terminal or Desktop `.bat`; never block the agent on foreground `ds` | Linux process remains alive and port 20999 is listening | S12 browser |
| S12 browser | Windows opens `http://127.0.0.1:20999` | Browser cannot connect | Check bind address, WSL/Windows localhost forwarding, firewall, and process logs | Windows browser loads Web UI | READY |
| BLOCKED-HUMAN | Agent lacks permission or needs physical/GUI action | Human action is mandatory | Explain exact blocker, evidence, and one requested action | Human marks action complete | Resume prior state |
| READY | All gates pass | Setup complete | Produce concise handoff with distro name, launch command, auth route, and validation evidence | User can reopen UI independently | Stop |

## Command-routing rules

| Command family | Where to run | Why |
|---|---|---|
| `wsl.exe`, `Get-*`, `Set-Net*`, registry reboot checks | Windows PowerShell | WSL is managed by Windows. |
| `apt`, Linux `node`, `npm`, `uv`, `codex`, `ds` | Selected WSL2 distro | Product runtime must be Linux-native. |
| Browser check `http://127.0.0.1:20999` | Windows host browser | End-user access path is Windows -> WSL localhost forwarding. |
| Audit/tests for this repository | Linux CI or Linux dev shell | Repository automation default is Linux. |

## Stop rules

- Stop if WSL cannot start and a reboot/BIOS/firewall change is required.
- Stop if the only proxy listener is loopback-only until the human enables LAN access.
- Stop if no valid Codex auth/provider credential is available.
- Stop if `ds doctor` fails after a direct `codex exec` failure; fix Codex first.
- Stop before unregistering or overwriting an existing distro unless the user explicitly confirms destructive action.
