---
name: deepscientist-windows-wsl-setup
description: Install, repair, and validate DeepScientist on Windows with WSL2 until the Windows browser can open the DeepScientist Web UI. Use for WSL distro setup, Linux prerequisites, Codex auth/profile and proxy repair, deterministic handoff gates, and final ds doctor plus http://127.0.0.1:20999 verification.
---

# DeepScientist Windows WSL Setup

## Operating-environment contract

Be explicit about two different environments:

- **Current repository/agent branch default: Linux.** Linux is the default environment for GitOps audits, repository tests, markdown/YAML/PowerShell syntax checks, and development of this skill.
- **Target user environment: Windows host plus WSL2 Linux distro.** Windows owns `wsl.exe`, Hyper-V, firewall, proxy listeners, browser, and reboots. The selected WSL2 distro owns the Linux-native DeepScientist runtime.

`wsl.exe is a Windows administrative client`; run it from Windows PowerShell. Run `apt`, Linux `node`, `npm`, `uv`, `codex`, and `ds` inside the selected WSL2 distro. **do not treat a Linux sandbox as evidence that the target Windows machine is ready.**

## One-liner for humans

Copy this to any AI coding agent:

> Install DeepScientist on this Windows machine using WSL2. Follow `deepscientist-windows-wsl-setup/SKILL.md`, keep the runtime inside a dedicated WSL2 distro, and keep going until `ds doctor` passes and I can open http://127.0.0.1:20999 in my Windows browser.

## Source-of-truth refresh

Before changing anything, read the current checked-in docs and compare them with this skill:

1. `README.md`
2. `docs/en/32_WINDOWS_WSL2_DEPLOYMENT_GUIDE.md`
3. `docs/en/15_CODEX_PROVIDER_SETUP.md`
4. `docs/en/09_DOCTOR.md`
5. this skill and its references:
   - `orchestration/deterministic-playbook.md`
   - `checklists/completion-checklist.md`
   - `references/deepscientist-windows-wsl-notes.md`
   - `scripts/find-wsl-proxy.ps1`

Prefer the current repository docs over memorized commands. If a version pin, CLI flag, or provider endpoint changed, update this skill in the same change. The current Windows/WSL guide pins **Codex CLI 0.57.0** for the verified WSL path; do not install a newer CLI unless the official guide says the provider route supports it.

## Deterministic orchestration

Follow the state machine in `orchestration/deterministic-playbook.md`. The short version is:

1. **Inventory** Windows/WSL state before changing it.
2. **Preflight** WSL health; do not install until WSL starts.
3. **Select or create** a dedicated WSL2 Ubuntu distro.
4. **Lock interop** so Linux does not accidentally use Windows-mounted `node`, `npm`, or `codex`.
5. **Install Linux prerequisites**, Node.js 20, npm user prefix, DeepScientist, pinned Codex CLI, and `uv`.
6. **Choose one Codex auth/provider route** and validate it directly.
7. **Test network/proxy**, then persist proxy only after a live WSL-side proxy test.
8. **Run `ds doctor`** using the same profile/environment as the direct Codex smoke test.
9. **Launch detached** so `ds` survives the agent shell.
10. **Open the Windows browser** to `http://127.0.0.1:20999`.

Do not mark work complete from memory or assumptions. Each transition requires an observation and validation command from the target machine.

## Hard stop gates

Stop and ask for a human action when:

- WSL fails with `HCS_E_CONNECTION_TIMEOUT`, Hyper-V is disabled, virtualization is off, or a reboot is pending.
- The user wants to reuse, overwrite, unregister, or migrate an existing distro and has not explicitly confirmed destructive action.
- A proxy listens only on `127.0.0.1`; the user must enable **Allow LAN** or equivalent.
- Codex has no valid ChatGPT auth, API key, relay key, or named provider credential.
- A firewall or browser/GUI action is required.
- Direct `codex exec` fails. Fix Codex/provider/network first; `ds doctor` is not the first debugger.

## Linux default for audit and development

Repository automation for this skill runs on Linux by design. It should validate files and deterministic contracts without requiring a Windows host:

```bash
python scripts/audits/audit_windows_wsl_skill.py
python -m py_compile scripts/audits/audit_windows_wsl_skill.py
pwsh -NoLogo -NoProfile -Command '$null = [scriptblock]::Create((Get-Content -Raw "skills/deepscientist-windows-wsl-setup/scripts/find-wsl-proxy.ps1")); "proxy-script-parse-ok"'
```

Linux CI can audit the skill; it cannot prove that the user's WSL VM, proxy, firewall, or browser works.

## Human actions required

| When | What the human must do |
|------|------------------------|
| WSL cannot start (`HCS_E_CONNECTION_TIMEOUT`) | Reboot the PC; resolve pending Windows updates, Hyper-V, or BIOS virtualization. |
| Proxy listens only on `127.0.0.1` | Open Clash/v2rayN/etc. and enable **Allow LAN** so it listens on a non-loopback address. |
| No valid Codex auth or provider key | Run `codex login` in a GUI terminal, or provide an API key/relay credential through an approved secret path. |
| ChatGPT subscription expired | Renew it or provide a Codex-compatible API relay/profile. |
| Firewall blocks WSL networking | Allow WSL/Hyper-V through Windows Firewall. |
| Browser setup or GUI login is required | Complete the GUI step and tell the agent when to resume. |

## Pre-flight checks (critical)

Run these from Windows PowerShell first:

```powershell
wsl --status
wsl -l -v
wsl --version
[Environment]::Is64BitOperatingSystem
(Get-CimInstance Win32_OperatingSystem).Caption
(Get-CimInstance Win32_OperatingSystem).LastBootUpTime
```

Test the selected or candidate distro:

```powershell
wsl -d <distro> -- echo hello
```

If the test fails with `HCS_E_CONNECTION_TIMEOUT`:

1. Check pending reboot data under `HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager`.
2. If uptime is long or updates are pending, ask the user to reboot.
3. Try `wsl --shutdown`, then retry.
4. Do not install until WSL starts reliably.

Check memory before creating a distro:

```powershell
$mem = Get-CimInstance Win32_OperatingSystem
[math]::Round($mem.FreePhysicalMemory / 1MB, 2)
```

Warn the user if free memory is below 2 GB.

## Distro setup

Prefer a dedicated WSL2 Ubuntu distro for DeepScientist. Do not modify `docker-desktop`, `docker-desktop-data`, or an unrelated user distro unless the user explicitly asks.

Preferred dedicated-distro creation flow with a direct Ubuntu WSL rootfs:

```powershell
New-Item -ItemType Directory -Force D:\WSL\Backups,D:\WSL\DeepScientist | Out-Null
$rootfs = 'D:\WSL\Backups\ubuntu-jammy-wsl.rootfs.tar.gz'
if (-not (Test-Path $rootfs)) {
  curl.exe -L 'https://cloud-images.ubuntu.com/wsl/jammy/current/ubuntu-jammy-wsl-amd64-wsl.rootfs.tar.gz' -o $rootfs
}
wsl --import DeepScientist D:\WSL\DeepScientist $rootfs --version 2
wsl -d DeepScientist --user root -- /bin/true
```

Using a direct rootfs avoids the Store Ubuntu first-user OOBE and makes the setup deterministic. If the user cannot download the rootfs, fall back to the official `wsl --install -d Ubuntu-22.04` flow, complete the normal interactive user creation, then export/import it as a dedicated distro only if the user explicitly approves.

Then create a normal non-root user inside the imported distro and make it the default user with `/etc/wsl.conf`:

```bash
adduser ds
usermod -aG sudo ds
printf '[user]\ndefault=ds\n' | sudo tee -a /etc/wsl.conf
```

From Windows PowerShell:

```powershell
wsl --terminate DeepScientist
wsl -d DeepScientist -- whoami
```

Do not unregister the base Ubuntu distro unless the user explicitly asks for cleanup. If the host already has a clean WSL2 Ubuntu 22.04 image that the user wants imported directly, `wsl --import` may use that tarball instead.

Prefer Ubuntu 22.04 for the current verified Codex 0.57.0 path. Ubuntu 24.04 is acceptable only after checking that Python venv package names and provider behavior still work.

Disable Windows PATH injection in the target distro:

```bash
printf '[interop]\nappendWindowsPath=false\n' | sudo tee /etc/wsl.conf
```

Then from Windows PowerShell:

```powershell
wsl --terminate <distro>
wsl -d <distro> -- bash -lc 'command -v node || true; command -v npm || true; command -v codex || true'
```

After restart, no required binary may resolve to `/mnt/c/...`.

## Linux prerequisites

Inside the selected distro, identify Ubuntu version first:

```bash
lsb_release -r
```

Ubuntu 24.04:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg git sudo build-essential python3-venv python3-pip
```

Ubuntu 22.04:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg git sudo build-essential python3.10-venv python3-pip
```

Install Node.js 20 unless official docs changed it:

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
printf 'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main\n' | sudo tee /etc/apt/sources.list.d/nodesource.list
sudo apt-get update
sudo apt-get install -y nodejs
```

Configure a user-local npm prefix:

```bash
mkdir -p "$HOME/.npm-global" "$HOME/.local/bin"
npm config set prefix "$HOME/.npm-global"
grep -qxF 'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"' "$HOME/.profile" 2>/dev/null || echo 'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"' >> "$HOME/.profile"
grep -qxF 'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"' "$HOME/.bashrc" 2>/dev/null || echo 'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
. "$HOME/.profile"
```

## DeepScientist installation

Install the current npm package, the Codex CLI version required by the current WSL guide, and `uv`:

```bash
npm install -g @researai/deepscientist
npm install -g @openai/codex@0.57.0
curl -LsSf https://github.com/astral-sh/uv/releases/latest/download/uv-installer.sh | sh
. "$HOME/.profile"
```

If GitHub/Astral downloads fail, fix proxy/network first or use an approved mirror. Do not switch Codex versions to bypass a provider problem without documenting the compatibility tradeoff.

Verify resolution:

```bash
command -v node npm git ds codex uv
node --version
npm --version
codex --version
uv --version
```

All required product binaries must resolve to Linux paths under `$HOME/.npm-global/bin`, `$HOME/.local/bin`, or another Linux path—not under `/mnt/c/`.

## Codex auth and API profiles

Choose one route, then validate that route directly before DeepScientist.

### Common validation order

```bash
codex --version
command -v codex
# default route:
codex exec --skip-git-repo-check "Print exactly OK and exit."
# profile route:
codex exec --profile <profile> --skip-git-repo-check "Print exactly OK and exit."
```

For provider profiles, also follow `docs/en/15_CODEX_PROVIDER_SETUP.md`. If `codex exec --profile <profile>` fails, stop and fix Codex before running `ds doctor`.

### Option A: reuse Windows-side ChatGPT auth

Use this only when the Windows ChatGPT subscription is active.

1. Inspect whether Windows has `C:\Users\<user>\.codex\auth.json`.
2. Back up any existing Linux auth file.
3. Copy it into `~/.codex/auth.json`.
4. Run `chmod 600 ~/.codex/auth.json`.
5. Validate with a direct non-interactive Codex request.

Subscription expiry can make copied auth look present but fail at model access time.

### Option B: third-party OpenAI-compatible relay

Use this when the relay documents a Codex-compatible endpoint. Ask which model and base URL to use; do not guess.

For a Responses-compatible relay that expects an OpenAI-style key, a typical shape is:

```toml
model_provider = "OpenAI"
model = "<model-from-relay>"
review_model = "<model-from-relay>"
model_reasoning_effort = "high"
disable_response_storage = true
network_access = "enabled"

[model_providers.OpenAI]
name = "OpenAI"
base_url = "<relay-base-url>"
wire_api = "responses"
supports_websockets = true
requires_openai_auth = true
```

`~/.codex/auth.json` must contain only the secret material, never a real key in docs or logs:

```json
{
  "OPENAI_API_KEY": "<relay-api-key>"
}
```

If the relay is chat-only, do not force `wire_api = "responses"`. Use Codex 0.57.0 with a named custom provider, `wire_api = "chat"`, the documented model id, `env_key` or bearer token shape supported by that relay, and `requires_openai_auth = false` when it is not using OpenAI account auth. Validate the exact provider shape with `codex exec --profile <profile>` first.

Codex 0.57.0 accepts `model_reasoning_effort` values `minimal`, `low`, `medium`, and `high`. Change `xhigh` to `high`.

### Option C: direct OpenAI API key

If the user has a platform.openai.com API key, place it in `~/.codex/auth.json` or the runner environment according to the official provider guide:

```json
{
  "OPENAI_API_KEY": "<api-key>"
}
```

### Option D: documented provider profile (Bailian, MiniMax, GLM, local, etc.)

For provider-specific profiles such as Bailian, MiniMax, GLM, Ark, Ollama, or another gateway, use the provider-specific block and profile from `docs/en/15_CODEX_PROVIDER_SETUP.md`. Record the selected profile and use it consistently:

```bash
ds doctor --codex-profile <profile>
ds --codex-profile <profile>
```

## Proxy and NAT repair

Do not assume every machine needs a proxy. Test direct connectivity from inside WSL first:

```bash
curl -I --max-time 10 https://github.com/ || true
curl -I --max-time 10 https://chatgpt.com/ || true
```

If direct access works, do not persist proxy variables. If direct access fails and the user says a Windows proxy is in use, inspect it with the bundled read-only helper:

```powershell
pwsh -File scripts/find-wsl-proxy.ps1
pwsh -File scripts/find-wsl-proxy.ps1 -Ports 7890
```

The helper only inspects listeners and prints JSON. It never changes system proxy settings.

Rules:

- Confirm whether proxy is enabled, which app is used, and which port is intended before writing config.
- Treat common ports (`7890`, `1080`, `10808`, `10809`, `20170`) only as candidates.
- If a listener is loopback-only, stop and ask the human to enable LAN access.
- Prefer the WSL default gateway from `ip route`; do not rely solely on `/etc/resolv.conf`.
- Test the candidate before persisting.

```bash
host_ip="$(ip route 2>/dev/null | awk '/^default/ { print $3; exit }')"
curl -I --max-time 8 -x "http://$host_ip:<confirmed-port>" https://github.com/
```

Persist only after that test passes:

```bash
cat > "$HOME/.wsl-proxy-env" <<'EOF'
host_ip="$(ip route 2>/dev/null | awk '/^default/ { print $3; exit }')"
proxy_port="<confirmed-port>"
if [ -n "$host_ip" ] && [ -n "$proxy_port" ]; then
    export http_proxy="http://$host_ip:$proxy_port"
    export https_proxy="$http_proxy"
    export HTTP_PROXY="$http_proxy"
    export HTTPS_PROXY="$http_proxy"
    export ALL_PROXY="$http_proxy"
fi
EOF
grep -qxF '[ -f "$HOME/.wsl-proxy-env" ] && . "$HOME/.wsl-proxy-env"' "$HOME/.bashrc" 2>/dev/null || echo '[ -f "$HOME/.wsl-proxy-env" ] && . "$HOME/.wsl-proxy-env"' >> "$HOME/.bashrc"
grep -qxF '[ -f "$HOME/.wsl-proxy-env" ] && . "$HOME/.wsl-proxy-env"' "$HOME/.profile" 2>/dev/null || echo '[ -f "$HOME/.wsl-proxy-env" ] && . "$HOME/.wsl-proxy-env"' >> "$HOME/.profile"
```

### Bash variable escaping from Windows agents

When issuing WSL commands from Windows via `wsl.exe ... bash -c "..."`, `$` variables can be consumed by the calling shell. Reliable patterns are:

1. Write a `.sh` script to a Windows temp path and run `wsl.exe -d <distro> -- bash /mnt/c/.../script.sh`.
2. Or write a PowerShell wrapper that invokes `wsl.exe -d <distro> -- bash /path/to/script.sh`, then run it with `powershell.exe -ExecutionPolicy Bypass -File wrapper.ps1`.

## Validation

Run validation in this order, inside the target distro and with the same profile/environment used by Codex:

1. Shell and binary paths:

   ```bash
   whoami
   command -v node npm git ds codex uv
   ```

2. Direct Codex execution:

   ```bash
   cd /tmp
   codex exec --skip-git-repo-check "Print exactly OK and exit."
   # or, when using a profile:
   codex exec --profile <profile> --skip-git-repo-check "Print exactly OK and exit."
   ```

3. DeepScientist diagnostics:

   ```bash
   ds doctor
   # or:
   ds doctor --codex-profile <profile>
   ```

   Accept `[ok] Codex CLI` or `[warn] Codex CLI: Codex startup probe completed.` only when the direct Codex smoke test also passed.

4. Configure Git identity if needed:

   ```bash
   git config --global user.name "Your Name"
   git config --global user.email "you@example.com"
   ```

5. Start `ds` so it outlives the agent shell. From Windows PowerShell:

   ```powershell
   Start-Process wsl.exe -ArgumentList '-d','<distro>','--','bash','-lc','cd $HOME; ds --codex-profile <profile>; exec bash'
   ```

   Omit `--codex-profile` only when using the default Codex route.

6. Confirm Windows can reach:

   ```text
   http://127.0.0.1:20999
   ```

Use `checklists/completion-checklist.md` for final evidence.

## Desktop shortcut

Create a `.bat` file on the Desktop with the selected distro and profile:

```bat
@echo off
start wsl.exe -d <distro> -- bash -lc "cd $HOME; ds --codex-profile <profile>; exec bash"
timeout /t 5 /nobreak >nul
start "" "http://127.0.0.1:20999"
```

Use the actual distro name; do not hard-code `Ubuntu` when the dedicated distro is `DeepScientist`.

## Troubleshooting

### WSL won't start

- `HCS_E_CONNECTION_TIMEOUT`: reboot required after checking pending updates and uptime.
- `HCS_E_HYPERV_NOT_INSTALLED`: enable virtualization in BIOS, run `bcdedit /set hypervisorlaunchtype auto`, then reboot.
- If a distro is WSL version `1`, do not install into it until WSL2 is available or a new WSL2 distro is created.

### Binaries resolve to Windows paths

- Fix `/etc/wsl.conf`, terminate the distro, re-enter, and recheck `command -v`.
- Make sure `$HOME/.npm-global/bin` and `$HOME/.local/bin` come before inherited or injected paths.

### Codex and provider errors

- Login success does not prove model access; always run the non-interactive `codex exec` smoke test.
- `unknown variant xhigh`: use `high` on Codex 0.57.0.
- `wire_api` mismatch: use the API format documented by the provider; do not force Responses on a chat-only gateway.
- If direct Codex fails but `ds doctor` is being attempted, stop and debug Codex/provider/network first.

### Proxy issues

- If the candidate listener is loopback-only, the human must enable LAN access.
- If multiple candidate ports exist, ask which app/port is intended.
- TLS resets, websocket resets, or long reconnects usually indicate proxy/network problems before auth problems.

### Python and first-run issues

- On Ubuntu 24.04 use `python3-venv`, not `python3.10-venv`.
- The first `ds doctor` can take several minutes while `uv` downloads the Python runtime.
- LF line endings matter when shell startup files are edited from Windows.

## References

- `orchestration/deterministic-playbook.md`
- `checklists/completion-checklist.md`
- `references/deepscientist-windows-wsl-notes.md`
- `scripts/find-wsl-proxy.ps1`
- `docs/en/32_WINDOWS_WSL2_DEPLOYMENT_GUIDE.md`
- `docs/en/15_CODEX_PROVIDER_SETUP.md`
- `docs/en/09_DOCTOR.md`
