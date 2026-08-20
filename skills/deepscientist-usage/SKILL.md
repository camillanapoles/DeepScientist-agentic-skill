---
name: deepscientist-usage
description: Operate DeepScientist day-to-day on a Linux box with OMP as the runner — run quests, choose models (glm-5-turbo default, glm-5.3 for hard/strategic/planning), keep the framework repo clean, and create research projects that never live inside the framework checkout. Covers doctor/daemon/new/run flows, the framework-vs-quests separation contract, model policy via z.ai, and pointers to the LLM wiki and GitOps flow. Use for any "run/use/operate DeepScientist" task; use deepscientist-linux-agent-setup for install/repair.
---

# DeepScientist Usage

Operational skill for **using** an installed DeepScientist. Canonical deep
documentation: [`docs/llm-wiki/`](../../docs/llm-wiki/README.md) — read pages
04 (separation) and 05 (model policy) before touching anything.

## The one rule that matters most

**The framework repo and research projects never mix.**

- Framework checkout (`…/DeepScientist-agentic-skill`): code, skills, docs,
  tests only. Never create experiment outputs, datasets, papers, or scratch
  repos here.
- Research projects are **quests**: real git repos created by the framework
  under the DS home (`ds new "topic"` → `<home>/quests/00N`).
- Need an isolated project? Its own DS home: `ds --home /root/DeepScientist-X new "X"`.
- Runner cwd is always the quest worktree, never the framework checkout.
- Persistent state/logs go under the DS home — never `/tmp` (ephemeral).

## Daily loop

```bash
export UV_LINK_MODE=copy PATH="/opt/node/bin:/root/.bun/bin:$PATH"
export ZAI_API_KEY=… ZAI_BASE_URL="https://api.z.ai/api/coding/paas/v4"

uv run ds doctor                      # must be 12/12 [ok] before anything
uv run ds daemon                      # Web UI http://127.0.0.1:20999
uv run ds new "research topic"        # quest repo under <home>/quests/
uv run ds run decision --quest-id 001 --message "…"
```

Success signature: `"ok": true` + non-empty `output_text`.

## Model policy (user mandate)

| Task shape | Model |
|---|---|
| Everyday turns | `glm-5-turbo` (default in runners.yaml) |
| **Hard / strategic / planning** | `glm-5.3` via `--model glm-5.3` |

When in doubt ask: is this architecture/planning/deep-analysis? → 5.3.

## Environments & failure classes

- omp shebang needs `bun` on PATH (runtime bun via /usr/local/bin symlink;
  installs via npm — bun -g blocks native postinstalls).
- `/workspace` mounts don't support hardlinks → npm for installs,
  `UV_LINK_MODE=copy` for uv.
- omp CLI flags drift; pi-wire JSON (`--mode=json`, versioned events) is the
  stable contract. Full incident table: wiki 07.

## Changing the framework

GitOps only: branch (`fix/ feat/ chore/`) → PR → CI green → merge → sync.
Release: CHANGELOG + pyproject bump + annotated tag + GitHub release
(pattern validated on PRs #11/#12, v1.7.0). Details: wiki 06.

## Do / Don't

- Do run `ds doctor` first on any new session or after env changes.
- Do keep the z.ai key in `runners.yaml` (`omp.env`) or env — not in the repo.
- Don't `cd` a quest into the framework tree or vice-versa.
- Don't store anything continuous in `/tmp`.
- Don't tag framework releases for research milestones — quests version
  themselves in their own repos.
