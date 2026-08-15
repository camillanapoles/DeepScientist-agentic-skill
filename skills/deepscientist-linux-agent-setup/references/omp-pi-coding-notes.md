# OMP / pi-coding-agent notes for DeepScientist

OMP (Oh My Pi) is the default runner on Linux.

- Website: https://omp.sh
- GitHub: https://github.com/can1357/oh-my-pi
- npm package: `@oh-my-pi/pi-coding-agent`
- Binary: `omp`
- Config/state: `~/.omp/`
- Requires Bun >= 1.3.14 when installed via Bun.

## Install

```bash
# macOS / Linux
curl -fsSL https://omp.sh/install | sh

# Bun (reproducible)
bun install -g @oh-my-pi/pi-coding-agent
```

The official installer places `omp` under `~/.local/bin`; ensure that is on
`PATH` (the Linux skill adds `$HOME/.local/bin` to both `.profile` and
`.bashrc`).

## Non-interactive / CI mode

OMP's `print` subcommand is the non-interactive form used by the runner and by
direct smoke tests:

```bash
omp print --json --yes --cwd "$PWD" "Print exactly OK and exit."
```

- `--json` emits machine-readable events on stdout.
- `--yes` auto-approves tool calls (mirrors Codex `--dangerously-bypass-approvals`
  in the DeepScientist sandbox context).
- `--cwd` sets the working directory.
- Prompt is passed on stdin by the DeepScientist runner to avoid argv limits.

## Why OMP is the default on Linux

OMP ships hash-anchored edits (reducing stale patches), first-class LSP/DAP
tooling, subagents, multi-provider routing, and a broad tool surface. It is
MIT-licensed, cross-platform, and actively maintained. Codex remains available
and remains the default on native Windows / the WSL path.

## Provider configuration

OMP supports 60+ providers declared in `~/.omp/agent/models.yml`. Each provider
speaks one of several wire APIs. Set the API key via the environment variable
the provider documents (e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`), or place it
in `~/DeepScientist/config/runners.yaml` under `runners.omp.env` so the daemon
inherits it reliably.

## MCP / skills inheritance

On first run OMP inherits rules, skills, and MCP servers from `.claude`,
`.codex`, `.cursor`, `.windsurf`, `.gemini`, `.cline`, `.github/copilot`, and
`.vscode`. The DeepScientist OMP runner mirrors quest skills into
`.ds/omp-home/.omp/skills` so the agent sees the DeepScientist skill set.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `omp: command not found` after install | `source ~/.profile`; confirm `~/.local/bin` is on `PATH`. |
| Provider auth failure | Set the documented env key; test with `omp print` outside `ds`. |
| `omp print` hangs waiting on approval | Use `--yes` (the runner does). |
| Bun missing for npm/bun install | Install Bun: `curl -fsSL https://bun.sh/install \| bash`. |
| Model/wire API mismatch | Use the provider's documented `models.yml` shape; OMP supports responses, chat, anthropic-messages, etc. |
