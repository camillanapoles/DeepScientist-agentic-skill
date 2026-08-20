# Changelog

Notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `docs/llm-wiki/` — agent-facing operational wiki (8 pages, mermaid
  diagrams): mental model, first run, quest lifecycle, **layout & separation
  (framework vs DS home vs quests)**, model policy (glm-5-turbo /
  glm-5.3-for-strategic), GitOps flow, troubleshooting from real incidents.
- `skills/deepscientist-usage/` — day-to-day operation skill: daily loop,
  model policy, framework/project non-interference contract, GitOps
  pointers. Complements `deepscientist-linux-agent-setup` (install/repair).

## [1.7.0] — 2026-08-20

First release of the fork's operational hardening line: OMP becomes the
default runner everywhere except native Windows, the omp CLI integration is
ported to the current (>= 17) pi-wire interface, and two runtime crashes
found by end-to-end validation on real hardware are fixed.

### Added
- **OMP bootstrap probe** (`ConfigManager.probe_runner_bootstrap`): new `omp`
  branch probing `omp --version` with structured result (version, config-dir
  warning, guidance). OMP-specific install/auth guidance in
  `_runner_missing_binary_guidance`.
- **pi-wire event translation** in `OmpRunner._translate_event`: handles
  `message_end` assistant text blocks; ignores `message_start` /
  `message_update` partials and user echoes to avoid duplicate messages.
- Regression tests: 3 for the config probe, 2 for the pi-wire translator,
  plus the updated command-contract test.

### Changed
- **Default runner is now `omp`** (`default_config`) — matching
  `platform_default_runner()` (codex remains default on native Windows and
  stays available as an alternative runner everywhere).
- **OMP runner command for omp >= 17**: `omp print --json --yes` (removed
  upstream) replaced by `-p --mode=json --auto-approve --cwd=<dir>`; the
  prompt continues to be fed via stdin by `SimpleCliRunner`.

### Fixed
- `ds doctor` crash `KeyError: Unknown runner 'omp'` that fired as soon as
  the omp binary was available on PATH (the doctor advanced past the
  binary-missing check into a probe that had no omp branch).
- `ds run` crash `TypeError: register_builtin_runners() missing 1 required
  keyword-only argument: 'omp_runner'` — `cli.py` now constructs and
  registers the `OmpRunner` like the daemon already did.
- Pre-existing broken `get_runner` stub in a daemon fallback test (returned
  a `str` instead of a runner instance).

### Validation
- `ds doctor`: 12/12 checks ok on Ubuntu 24.04 aarch64 (omp 17.3.8 via npm).
- End-to-end: `ds run decision` → omp → glm-5-turbo (z.ai coding plan) →
  assistant output captured (`ok: true`).
- Test suites: orchestration (incl. OMP integration), config probe,
  runner-runtime overrides, config testing, doctor — all green locally and
  on CI (PR #11).

### Notes
- pi-wire **RPC** runner (`--mode=rpc`) filed as follow-up to eliminate
  CLI-flag drift permanently.
- 45 pre-existing/untriaged failures remain in `tests/test_daemon_api.py`
  (long-running suite); at least one is a pre-existing test bug.

## [1.6.0] — 2026-05-12

Upstream release (ResearAI/DeepScientist v1.6.0): Claude Code, OpenCode,
Kimi Code runners, BenchStore, science evidence workflows.

## [1.5.x] — Upstream

See upstream history (git log v1.5.14…v1.5.17).
