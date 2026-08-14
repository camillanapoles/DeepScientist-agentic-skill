# DeepScientist Windows WSL Notes

Use this note only after the main skill triggers.

## Environment split

- Repository GitOps and tests run on **Linux** by default.
- The target deployment is a **Windows host plus WSL2 Ubuntu distro**.
- Linux CI proves the skill files are complete, parseable, and internally consistent. It does not prove the user's WSL VM, proxy, firewall, or browser works.

## Source-of-truth docs

Check these before changing commands or version pins:

- `README.md`
- `docs/en/32_WINDOWS_WSL2_DEPLOYMENT_GUIDE.md`
- `docs/en/15_CODEX_PROVIDER_SETUP.md`
- `docs/en/09_DOCTOR.md`
- `orchestration/deterministic-playbook.md`
- `checklists/completion-checklist.md`

Current verified Windows/WSL path:

- WSL2 Ubuntu 22.04
- Node.js 20
- npm with user-local prefix
- `@researai/deepscientist`
- `@openai/codex@0.57.0`
- `uv`

## Known-good end state

- Dedicated WSL2 Ubuntu 22.04 distro exists and starts reliably.
- The default user is non-root (for example `ds`).
- `/etc/wsl.conf` contains `appendWindowsPath=false`.
- Linux-side `node`, `npm`, `git`, `uv`, `codex`, and `ds` exist and resolve to Linux paths.
- `codex --version` reports the version required by the docs (currently `0.57.0`).
- A direct non-interactive `codex exec ... "Print exactly OK and exit."` succeeds.
- If a provider profile is used, `codex exec --profile <profile> ...` succeeds.
- Proxy is either unnecessary or set to a reachable Windows host address from WSL.
- `ds doctor` shows `[ok] Codex CLI` or an accepted startup-probe warning.
- `ds` remains running outside the agent shell.
- Windows browser opens `http://127.0.0.1:20999`.

## Deterministic execution matrix

| Phase | Command/observation | Must validate | Stop condition |
|---|---|---|---|
| Inventory | `wsl --status`, `wsl -l -v`, OS/memory facts | WSL exists and target distro candidates are known | Unsupported Windows, missing admin feature, or no WSL command |
| Preflight | `wsl -d <distro> -- echo hello` | Target starts | `HCS_E_CONNECTION_TIMEOUT`, Hyper-V/virtualization error, pending reboot |
| Distro creation | `wsl --import DeepScientist ... --version 2` | `wsl -d DeepScientist --user root -- /bin/true` works | User has not approved a custom path or destructive import/unregister |
| User setup | `adduser ds`, sudo group, `/etc/wsl.conf [user] default=ds` | `wsl -d DeepScientist -- whoami` prints `ds` | Interactive password prompt cannot be completed |
| Interop | `/etc/wsl.conf [interop] appendWindowsPath=false`; terminate distro | Required tools do not resolve under `/mnt/c/` | Windows path contamination remains |
| Linux prereqs | `lsb_release -r`, `apt-get install ...` | `git`, `curl`, build tools, Python venv package exist | apt/network failure |
| Node/npm | Node.js 20 install and `npm config set prefix "$HOME/.npm-global"` | `node --version`, `npm --version`, Linux prefix | npm global install still needs sudo or resolves to Windows npm |
| Product install | `npm install -g @researai/deepscientist`, Codex pin, `uv` installer | `command -v ds codex uv` and versions | GitHub/npm/uv download failure; do not continue on missing binary |
| Auth/profile | ChatGPT auth copy, API key, or provider profile | Direct `codex exec` smoke passes | Missing credential or invalid provider model/base URL/wire API |
| Proxy | Direct curl; PowerShell listener helper; WSL `curl -x` test | Persisted proxy is reachable | Proxy is loopback-only, port unknown, or proxy test fails |
| Doctor | `ds doctor` or `ds doctor --codex-profile <profile>` | Codex startup probe passes | Direct Codex failed first; do not debug DS before Codex works |
| Launch | Separate WSL terminal or `.bat`; process stays alive | Port 20999 is reachable from Windows | Foreground process exits with the agent shell |
| Browser | Open `http://127.0.0.1:20999` | Web UI loads | Firewall/bind/process issue |

## Decision table

| Input | Route |
|---|---|
| Active ChatGPT subscription and Windows auth works | Copy Windows `auth.json` into Linux `~/.codex/auth.json`, chmod `600`, smoke test |
| Direct OpenAI platform key | Write key to auth/runner env according to official docs, smoke test |
| Relay with Responses support | Use OpenAI-compatible provider shape with `wire_api = "responses"` only if relay documents it |
| Chat-only relay or provider (for example some China coding-plan gateways) | Use Codex 0.57.0, custom provider, named profile, `wire_api = "chat"`, documented model, and `requires_openai_auth = false` when appropriate |
| Bailian/MiniMax/GLM/Ark/local provider | Follow the provider-specific section in `docs/en/15_CODEX_PROVIDER_SETUP.md`; record profile and pass `--codex-profile` consistently |
| Direct network succeeds | Do not configure proxy |
| Direct network fails and proxy has a non-loopback listener | Test WSL gateway IP and port, then persist `~/.wsl-proxy-env` |
| Proxy is loopback-only | Human enables LAN access first |

## Concrete PowerShell preflight

```powershell
wsl --status
wsl -l -v
wsl --version
(Get-CimInstance Win32_OperatingSystem).Caption
(Get-CimInstance Win32_OperatingSystem).LastBootUpTime
[math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 2)
```

HCS timeout:

```powershell
wsl --shutdown
wsl -d <distro> -- echo hello
```

If the retry still fails, the human must reboot or fix Hyper-V/virtualization.

## Create the dedicated distro from rootfs

```powershell
New-Item -ItemType Directory -Force D:\WSL\Backups,D:\WSL\DeepScientist | Out-Null
$rootfs = 'D:\WSL\Backups\ubuntu-jammy-wsl.rootfs.tar.gz'
if (-not (Test-Path $rootfs)) {
  curl.exe -L 'https://cloud-images.ubuntu.com/wsl/jammy/current/ubuntu-jammy-wsl-amd64-wsl.rootfs.tar.gz' -o $rootfs
}
wsl --import DeepScientist D:\WSL\DeepScientist $rootfs --version 2
wsl -d DeepScientist --user root -- /bin/true
```

Create the default Linux user:

```bash
adduser ds
usermod -aG sudo ds
printf '[interop]\nappendWindowsPath=false\n[user]\ndefault=ds\n' | sudo tee /etc/wsl.conf
```

From PowerShell:

```powershell
wsl --terminate DeepScientist
wsl -d DeepScientist -- whoami
wsl -d DeepScientist -- bash -lc 'command -v node || true; command -v npm || true; command -v codex || true'
```

## Install commands inside WSL

```bash
lsb_release -r
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg git sudo build-essential python3.10-venv python3-pip
```

Ubuntu 24.04 would use `python3-venv`; current verified path is 22.04.

Node.js 20:

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
printf 'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main\n' | sudo tee /etc/apt/sources.list.d/nodesource.list
sudo apt-get update
sudo apt-get install -y nodejs
```

npm prefix and product:

```bash
mkdir -p "$HOME/.npm-global" "$HOME/.local/bin"
npm config set prefix "$HOME/.npm-global"
grep -qxF 'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"' "$HOME/.profile" 2>/dev/null || echo 'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"' >> "$HOME/.profile"
grep -qxF 'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"' "$HOME/.bashrc" 2>/dev/null || echo 'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
. "$HOME/.profile"
npm install -g @researai/deepscientist
npm install -g @openai/codex@0.57.0
curl -LsSf https://github.com/astral-sh/uv/releases/latest/download/uv-installer.sh | sh
```

## Auth/provider examples

Never commit or print real secrets.

Direct ChatGPT auth reuse:

```bash
mkdir -p ~/.codex
[ -f ~/.codex/auth.json ] && cp ~/.codex/auth.json ~/.codex/auth.json.bak.$(date +%Y%m%d-%H%M%S)
cp /mnt/c/Users/<user>/.codex/auth.json ~/.codex/auth.json
chmod 600 ~/.codex/auth.json
codex exec --skip-git-repo-check "Print exactly OK and exit."
```

Named chat provider skeleton for Codex 0.57.0:

```toml
[model_providers.example_chat]
name = "Example chat-compatible coding gateway"
base_url = "https://example.com/v1"
env_key = "EXAMPLE_API_KEY"
wire_api = "chat"
requires_openai_auth = false

[profiles.example]
model = "<provider-model-id>"
model_provider = "example_chat"
```

Use the exact base URL, model id, and wire API from the provider's current Codex documentation.

## Proxy workflow

Test direct connectivity first:

```bash
curl -I --max-time 10 https://github.com/ || true
curl -I --max-time 10 https://chatgpt.com/ || true
```

Windows-side read-only inspection:

```powershell
pwsh -File scripts/find-wsl-proxy.ps1
```

WSL-side test:

```bash
host_ip="$(ip route 2>/dev/null | awk '/^default/ { print $3; exit }')"
curl -I --max-time 8 -x "http://$host_ip:<port>" https://github.com/
```

Persist only after a successful test.

## Validation commands

```bash
command -v node npm git ds codex uv
node --version
npm --version
codex --version
uv --version
cd /tmp
codex exec --skip-git-repo-check "Print exactly OK and exit."
# for a profile:
# codex exec --profile <profile> --skip-git-repo-check "Print exactly OK and exit."
ds doctor
# or: ds doctor --codex-profile <profile>
```

Accepted Codex line:

```text
[ok] Codex CLI: Codex startup probe completed.
```

or an accepted warning such as the Codex 0.57.0 `xhigh` downgrade when direct Codex also works.

Launch from Windows so the process survives:

```powershell
Start-Process wsl.exe -ArgumentList '-d','DeepScientist','--','bash','-lc','cd $HOME; ds; exec bash'
Start-Process 'http://127.0.0.1:20999'
```

If a profile is required, use `ds --codex-profile <profile>`.

## Common failure patterns

| Symptom | Cause | Fix |
|---|---|---|
| `HCS_E_CONNECTION_TIMEOUT` | Pending reboot/Hyper-V/WSL service issue | Reboot PC, enable virtualization/Hyper-V, retry |
| `command -v ds` returns `/mnt/c/...` | Windows PATH injection | Set `appendWindowsPath=false`, terminate, re-enter, fix PATH |
| `python3.10-venv not found` | Wrong Ubuntu package set | Use `python3-venv` on 24.04 or stay on 22.04 for the verified path |
| `unknown variant xhigh` | Codex 0.57.0 limitation | Set reasoning effort to `high` |
| `wire_api`/model error | Provider and Codex API mismatch | Use provider-documented `wire_api`, model, and profile |
| `codex login` works but `ds doctor` fails | Login is not a non-interactive model probe | Run direct `codex exec`; fix auth/model/network/profile |
| Proxy test returns 000 | Proxy unreachable or loopback-only | Human enables LAN; use WSL gateway IP |
| `ds` exits when the agent finishes | Foreground process tied to shell | Use `Start-Process`, separate terminal, or Desktop shortcut |
| First `ds doctor` is slow | `uv` setting up Python runtime | Wait; do not repeatedly kill it without evidence |
| Shell startup files have syntax errors | Edited from Windows with CRLF or bad quoting | Normalize to LF and inspect `.bashrc`/`.profile` |

## Bash escaping workaround

When running WSL commands from a Windows-side agent, `$` variables in `bash -c "..."` can be consumed by the outer shell. Prefer writing a temporary `.sh` script and a PowerShell wrapper:

1. Write `.sh` under a Windows temp path.
2. Write wrapper: `wsl.exe -d DeepScientist -- bash /mnt/c/.../script.sh`.
3. Run: `powershell.exe -ExecutionPolicy Bypass -File wrapper.ps1`.
