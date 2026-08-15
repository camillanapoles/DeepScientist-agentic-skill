from __future__ import annotations

from .claude import ClaudeRunner
from .codex import CodexRunner
from .kimi import KimiRunner
from .omp import OmpRunner
from .opencode import OpenCodeRunner
from .registry import register_runner


def register_builtin_runners(
    *,
    omp_runner: OmpRunner,
    codex_runner: CodexRunner,
    claude_runner: ClaudeRunner,
    kimi_runner: KimiRunner,
    opencode_runner: OpenCodeRunner,
) -> None:
    register_runner("omp", lambda **_: omp_runner)
    register_runner("codex", lambda **_: codex_runner)
    register_runner("claude", lambda **_: claude_runner)
    register_runner("kimi", lambda **_: kimi_runner)
    register_runner("opencode", lambda **_: opencode_runner)
