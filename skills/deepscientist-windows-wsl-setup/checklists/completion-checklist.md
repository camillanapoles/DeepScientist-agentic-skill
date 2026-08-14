# Windows/WSL setup completion checklist

Use this checklist to prove completeness. Each item needs evidence from the
target Windows host or selected WSL distro, not from the Linux audit sandbox.

## 0. Scope and source of truth

- [ ] The current branch, official README, and `docs/en/32_WINDOWS_WSL2_DEPLOYMENT_GUIDE.md` were checked for version pins.
- [ ] The selected target is a Windows 10/11 host with WSL2, not a native Windows-only install.
- [ ] The agent/CI audit environment is documented as Linux and is not mistaken for the target host.
- [ ] The target WSL distro name is recorded: `____________________`
- [ ] The selected Codex auth/provider route is recorded: `____________________`

## 1. Windows and WSL2 health

- [ ] `wsl --status` succeeded.
- [ ] `wsl -l -v` shows the target distro as version `2`.
- [ ] `wsl -d <distro> -- echo hello` returned `hello`.
- [ ] `docker-desktop`, `docker-desktop-data`, or unrelated user distros were not modified without explicit permission.
- [ ] Any `HCS_E_CONNECTION_TIMEOUT`, Hyper-V, virtualization, or reboot blocker was resolved before install.

## 2. Linux interop and binaries

- [ ] `/etc/wsl.conf` contains `appendWindowsPath=false` for the target distro.
- [ ] The distro was terminated and restarted after changing WSL interop.
- [ ] `command -v node npm git ds codex uv` resolves to Linux paths.
- [ ] No required binary resolves under `/mnt/c/`.
- [ ] Node.js is the supported major version from the current docs (Node.js 20 unless docs changed it).
- [ ] Global npm packages install under a user-owned prefix such as `$HOME/.npm-global`.

## 3. Product and Codex installation

- [ ] DeepScientist was installed from the official npm package in the selected distro.
- [ ] Codex CLI version matches the current official guide and was verified with `codex --version`.
- [ ] `uv` exists under a Linux user path.
- [ ] `ds --version` or `ds doctor` invokes the intended Linux installation.
- [ ] If a named Codex profile is used (`bailian`, relay, local, etc.), it is tested before `ds doctor`.

## 4. Auth, provider, and proxy evidence

- [ ] A valid Codex route exists: ChatGPT auth reuse, direct API key, or named provider profile.
- [ ] Secrets were not echoed into logs, committed to Git, or written into this skill.
- [ ] If a relay/provider profile was used, `wire_api`, model id, base URL, env/auth shape, and reasoning effort match provider compatibility.
- [ ] Direct connectivity was tested before deciding whether proxy configuration is needed.
- [ ] Proxy configuration was persisted only after a candidate proxy address passed a WSL-side `curl` test.
- [ ] The user enabled LAN access manually when the Windows proxy listened only on `127.0.0.1`.

## 5. Validation and launch

- [ ] Direct Codex smoke test succeeded:
      `codex exec --skip-git-repo-check "Print exactly OK and exit."`
      (include `--profile <name>` when required).
- [ ] `ds doctor` shows `[ok] Codex CLI` or an accepted startup-probe warning.
- [ ] Git identity was configured if `ds doctor` warned about it.
- [ ] `ds` starts in a separate WSL terminal or Desktop shortcut and outlives the agent shell.
- [ ] The DeepScientist process remains alive.
- [ ] Windows browser opens `http://127.0.0.1:20999`.
- [ ] The Web UI loads far enough for the user to start research.

## 6. Handoff artifacts

- [ ] Final summary records distro name, launch command, Codex profile/route, proxy decision, and validation evidence.
- [ ] Remaining warnings are classified as non-blocking or assigned a follow-up owner.
- [ ] If blocked, the report contains exact symptom, failed command, exit/status, and the single human action required.
- [ ] No destructive action such as `wsl --unregister` was performed without explicit user confirmation.
