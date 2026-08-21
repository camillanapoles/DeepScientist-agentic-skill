# 07 — Troubleshooting (real incidents, real fixes)

Every entry below happened during the validated install on aarch64/proot.

## `KeyError: Unknown runner 'omp'` in `ds doctor`

**Cause:** omp binary on PATH advanced the doctor into
`probe_runner_bootstrap`, which had no `omp` branch (pre-v1.7.0).
**Fix:** v1.7.0 adds the probe. `git log --oneline` → `de6ddb9`.

## `Error: unknown flags: --json, --yes` from omp

**Cause:** omp ≥ 17 removed the `print` subcommand and those flags; the
runner still emitted the old command line.
**Fix:** v1.7.0 ports to `-p --mode=json --auto-approve --cwd=<dir>`
(pi-wire events). Flag surfaces **drift**; the versioned JSON protocol is
the stable contract. If omp updates again, re-check `omp print --help`
(that's `omp --help` now) and adjust `_build_command` only.

## `omp` installed but `omp: command not found` / env bun errors

**Cause:** omp's shebang is `#!/usr/bin/env bun`; bun missing from PATH.
**Fix:** install bun (`curl -fsSL https://bun.sh/install | bash`) and
symlink: `ln -sf /root/.bun/bin/bun /usr/local/bin/bun`.

## `Failed to load pi_natives native addon for linux-arm64`

**Cause:** `bun install -g` blocks postinstalls → native addon never lands.
**Fix:** `npm install -g @oh-my-pi/pi-coding-agent` (npm runs
optionalDependencies properly), keep bun only as runtime.

## `ENOENT: could not open the "node_modules" directory` (bun)

**Cause:** bun's default install uses hardlinks from cache; the `/workspace`
mount doesn't support them (same class of problem as `UV_LINK_MODE=copy`).
**Fix:** use npm for installs under `/workspace`. If the cache got raced:
clear and reinstall.

## omp hangs at `Still starting … phase: readPipedInput`

**Cause:** `omp print`-mode waits on stdin.
**Fix:** pipe the prompt: `printf '…' | omp -p …` (the runner does this).

## Files vanished from `/tmp` (daemon logs, dumps)

**Cause:** `/tmp` is ephemeral — rootfs recycles wipe it (user mandate:
verified incident).
**Fix:** continuous artifacts → DS home (`<home>/logs/daemon.log`),
`/workspace`, or `/root`. `/tmp` only for throwaway captures.

## `register_builtin_runners() missing 'omp_runner'` (ds run)

**Cause:** daemon was updated for OMP but the CLI caller wasn't.
**Fix:** v1.7.0 (`f03816e`) constructs + registers OmpRunner in `cli.py`.

## Doctor `[warn]` on omp with binary present

`~/.omp` doesn't exist yet / first-run auth not done — or provider env
missing. Check `ZAI_API_KEY`/`ZAI_BASE_URL`, then `omp models`.

Back to [index](README.md).
