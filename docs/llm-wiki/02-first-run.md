# 02 — First run (verified path)

The exact sequence validated on Ubuntu 24.04 aarch64 / omp 17.3.8. Run from
the framework checkout with `UV_LINK_MODE=copy` and the toolchain on PATH.

## 0. Environment prerequisites

```bash
export UV_LINK_MODE=copy                      # proot/mounts without hardlink support
export PATH="/opt/node/bin:/root/.bun/bin:$PATH"
# z.ai credentials (also settable in runners.yaml env block):
export ZAI_API_KEY="..." ZAI_BASE_URL="https://api.z.ai/api/coding/paas/v4"
```

`omp` must answer `omp --version` (bun must be on PATH — omp's shebang is
`#!/usr/bin/env bun`).

## 1. Health gate

```bash
uv run ds doctor
```

Expect **12/12 `[ok]`**. Any `[fail]` blocks; `[warn]` on omp means
first-run auth/config missing. Never proceed past a fail.

## 2. Daemon + Web UI

```bash
uv run ds daemon          # serves http://127.0.0.1:20999
curl -s http://127.0.0.1:20999/api/health   # {"status":"ok",...}
```

Logs go under the DS home (`<home>/logs/`), not `/tmp`.

## 3. First quest

```bash
uv run ds new "my-research-topic"           # creates quests/00N (a git repo)
uv run ds run decision --quest-id 001 \
    --message "Smoke test. Reply with exactly: QUEST OK"
```

Success signature: JSON result with `"ok": true` and non-empty
`output_text`. That proves the full chain: quest → OmpRunner → omp →
glm-5-turbo → pi-wire `message_end` → message recorded.

## 4. Where the quest landed

```bash
ls /root/DeepScientist/quests/001
# SUMMARY.md artifacts baselines brief.md experiments handoffs literature
# memory paper plan.md quest.yaml release status.md userfiles ...
```

**Not** inside the framework repo. That's the contract —
see [04 — layout & separation](04-layout-and-separation.md).

Next: [03 — quest lifecycle](03-quest-lifecycle.md).
