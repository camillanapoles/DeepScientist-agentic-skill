from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure src is importable for standalone test runs.
SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from deepscientist.config.models import default_runners, platform_default_runner  # noqa: E402
from deepscientist.runners import (  # noqa: E402
    OmpRunner,
    get_runner_factory,
    list_builtin_runner_names,
    register_builtin_runners,
)
from deepscientist.runners.metadata import get_runner_metadata  # noqa: E402


def test_omp_is_registered_as_builtin_runner() -> None:
    assert "omp" in list_builtin_runner_names()
    meta = get_runner_metadata("omp")
    assert meta.default_binary == "omp"
    assert meta.default_config_dir == "~/.omp"
    assert meta.quest_dotdir == ".omp"
    assert "pi-coding-agent" in meta.label.lower() or "oh my pi" in meta.label.lower()


def test_omp_runner_metadata_supports_reasoning_effort() -> None:
    meta = get_runner_metadata("omp")
    assert meta.supports_reasoning_effort is True


def test_omp_runner_builds_print_json_command() -> None:
    """The OMP runner must build a non-interactive `omp print --json` command."""
    from deepscientist.runners.base import RunRequest

    # Construct an OmpRunner with the minimum constructor surface.
    runner = OmpRunner(
        home=Path("/tmp/fake-home"),
        repo_root=Path("/tmp/fake-repo"),
        binary="omp",
        logger=None,  # type: ignore[arg-type]
        prompt_builder=None,  # type: ignore[arg-type]
        artifact_service=None,  # type: ignore[arg-type]
    )
    request = RunRequest(
        quest_id="q1",
        quest_root=Path("/tmp/q1"),
        worktree_root=None,
        run_id="run-1",
        skill_id="deepscientist-linux-agent-setup",
        message="hello",
        model="gpt-5",
        approval_policy="never",
        sandbox_mode="danger-full-access",
    )
    command = runner._build_command(request, "PROMPT BODY")
    assert command[0].endswith("omp")
    assert "print" in command
    assert "--json" in command
    assert "--yes" in command
    assert "--cwd" in command
    assert "/tmp/q1" in command
    assert "--model" in command
    assert "gpt-5" in command
    # Prompt is read from stdin (trailing "-").
    assert command[-1] == "-"
    assert runner._command_uses_stdin_prompt() is True


def test_omp_runner_uses_stdin_for_prompt() -> None:
    runner = OmpRunner(
        home=Path("/tmp/fake-home"),
        repo_root=Path("/tmp/fake-repo"),
        binary="omp",
        logger=None,  # type: ignore[arg-type]
        prompt_builder=None,  # type: ignore[arg-type]
        artifact_service=None,  # type: ignore[arg-type]
    )
    assert runner._command_uses_stdin_prompt() is True
    assert runner.runner_name == "omp"


def test_default_runners_includes_omp_enabled_by_default() -> None:
    runners = default_runners()
    assert "omp" in runners
    assert runners["omp"]["enabled"] is True
    assert runners["omp"]["binary"] == "omp"
    # codex should still be present but not forced as default.
    assert "codex" in runners


def test_platform_default_runner_is_omp_on_linux(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")
    assert platform_default_runner() == "omp"


def test_platform_default_runner_is_codex_on_windows(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Windows")
    assert platform_default_runner() == "codex"


def test_register_builtin_runners_includes_omp() -> None:
    """register_builtin_runners must accept and register an omp_runner."""
    # Build fake runners; registration only stores factories.
    class FakeRunner:
        pass

    omp = FakeRunner()
    register_builtin_runners(
        omp_runner=omp,
        codex_runner=FakeRunner(),
        claude_runner=FakeRunner(),
        kimi_runner=FakeRunner(),
        opencode_runner=FakeRunner(),
    )
    factory = get_runner_factory("omp")
    assert factory() is omp


def test_omp_translates_assistant_message_events() -> None:
    runner = OmpRunner(
        home=Path("/tmp/fake-home"),
        repo_root=Path("/tmp/fake-repo"),
        binary="omp",
        logger=None,  # type: ignore[arg-type]
        prompt_builder=None,  # type: ignore[arg-type]
        artifact_service=None,  # type: ignore[arg-type]
    )
    events, texts = runner._translate_event(
        {"type": "assistant", "content": "hello from omp"},
        raw_line='{"type":"assistant","content":"hello from omp"}',
        quest_id="q1",
        run_id="r1",
        skill_id="skill",
        created_at="2026-01-01T00:00:00Z",
        translation_state={},
    )
    assert "hello from omp" in texts
    assert events[0]["type"] == "runner.agent_message"
    assert events[0]["source"] == "omp"
