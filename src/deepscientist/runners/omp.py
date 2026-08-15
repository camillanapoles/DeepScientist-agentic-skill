"""Oh My Pi (``omp`` / pi-coding-agent) runner.

OMP (https://omp.sh, npm package ``@oh-my-pi/pi-coding-agent``) is the
**default** runner on Linux. It is a terminal coding agent with
hash-anchored edits, LSP/DAP tooling, subagents, multi-provider routing
and a ``print`` mode suitable for non-interactive CI/agentic use.

This runner wraps that CLI using the same :class:`SimpleCliRunner`
machinery as the other CLI runners, translating OMP's JSON output into
DeepScientist runner events.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..shared import (
    ensure_dir,
    ensure_utf8_subprocess_env,
    generate_id,
    resolve_runner_binary,
    write_json,
)
from .base import (
    RunRequest,
    builtin_mcp_server_names_for_custom_profile,
    resolve_mcp_tool_profile_for_quest,
)
from .simple_cli import SimpleCliRunner

_OMP_TERMINAL_STATUSES = frozenset(
    {"completed", "success", "done", "error", "failed", "cancelled", "interrupted"}
)


class OmpRunner(SimpleCliRunner):
    """Runner adapter for Oh My Pi (``omp``)."""

    runner_name = "omp"

    # ------------------------------------------------------------------ runtime

    def _prepare_runtime(
        self,
        *,
        workspace_root: Path,
        quest_root: Path,
        quest_id: str,
        run_id: str,
        runner_config: dict[str, Any] | None = None,
    ) -> tuple[dict[str, str], dict[str, Any]]:
        runtime_home = ensure_dir(workspace_root / ".ds" / "omp-home")
        config_dir = ensure_dir(runtime_home / ".omp")
        resolved_runner_config = (
            runner_config if isinstance(runner_config, dict) else self._load_runner_config()
        )
        source_root = Path(
            str(resolved_runner_config.get("config_dir") or Path.home() / ".omp")
        ).expanduser()

        # OMP inherits .codex/.claude skills on disk; mirror the quest skills
        # into the runtime home so the agent sees the DeepScientist skill set.
        skills_target = config_dir / "skills"
        if source_root.exists():
            _copy_tree_optional(skills_target, source_root / "skills")
        _copy_tree_optional(skills_target, quest_root / ".omp" / "skills")

        shared_env = ensure_utf8_subprocess_env(
            {
                "DEEPSCIENTIST_HOME": str(self.home),
                "DEEPSCIENTIST_REPO_ROOT": str(self.repo_root),
                "DS_HOME": str(self.home),
                "DS_QUEST_ID": quest_id,
                "DS_QUEST_ROOT": str(quest_root),
                "DS_WORKTREE_ROOT": str(workspace_root),
                "DS_RUN_ID": run_id,
                "DS_WORKER_ID": run_id,
                "DS_CONVERSATION_ID": f"quest:{quest_id}",
                "DS_AGENT_ROLE": "pi",
                "DS_TEAM_MODE": "single",
            }
        )
        custom_profile = resolve_mcp_tool_profile_for_quest(quest_root)
        if custom_profile:
            shared_env["DS_CUSTOM_PROFILE"] = custom_profile
        pythonpath = str(os.environ.get("PYTHONPATH") or "").strip()
        if pythonpath:
            shared_env["PYTHONPATH"] = pythonpath

        # Write a minimal OMP settings file so non-interactive runs are quiet
        # and use the configured model when provided.
        settings: dict[str, Any] = {
            "startup": {"quiet": True},
        }
        model = str(resolved_runner_config.get("model") or "").strip()
        if model and model.lower() not in {"", "inherit", "default"}:
            settings["model"] = model
        write_json(config_dir / "settings.json", settings)

        return (
            {
                "HOME": str(runtime_home),
                "XDG_CONFIG_HOME": str(runtime_home / ".config"),
            },
            {
                "omp_home": str(runtime_home),
                "omp_config": str(config_dir),
                "omp_mcp_servers": builtin_mcp_server_names_for_custom_profile(custom_profile),
            },
        )

    # ------------------------------------------------------------------ command

    def _build_command(
        self,
        request: RunRequest,
        prompt: str,
        *,
        runner_config: dict[str, Any] | None = None,
    ) -> list[str]:
        workspace_root = request.worktree_root or request.quest_root
        resolved_runner_config = (
            runner_config if isinstance(runner_config, dict) else self._load_runner_config()
        )
        resolved_binary = resolve_runner_binary(self.binary, runner_name=self.runner_name)

        command: list[str] = [
            resolved_binary or self.binary,
            "print",
            "--json",
            "--yes",
            "--cwd",
            str(workspace_root),
        ]
        normalized_model = str(request.model or "").strip()
        if normalized_model.lower() not in {"", "inherit", "default", "omp-default"}:
            command.extend(["--model", normalized_model])

        # Prompt is passed via stdin by the SimpleCliRunner base to avoid
        # argv length limits; append the sentinel so OMP reads it.
        command.append("-")
        return command

    def _command_uses_stdin_prompt(self) -> bool:
        return True

    # ------------------------------------------------------------------ events

    def _translate_event(
        self,
        payload: dict[str, Any],
        *,
        raw_line: str,
        quest_id: str,
        run_id: str,
        skill_id: str,
        created_at: str,
        translation_state: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        events: list[dict[str, Any]] = []
        texts: list[str] = []
        event_type = str(
            payload.get("type") or payload.get("event") or payload.get("kind") or ""
        ).strip().lower()
        known_tools = translation_state.setdefault("known_tools", {})

        def emit_message(text: str) -> None:
            normalized = str(text or "").strip()
            if not normalized:
                return
            texts.append(normalized)
            events.append(
                {
                    "event_id": generate_id("evt"),
                    "type": "runner.agent_message",
                    "quest_id": quest_id,
                    "run_id": run_id,
                    "source": self.runner_name,
                    "skill_id": skill_id,
                    "text": normalized,
                    "created_at": created_at,
                }
            )

        # Assistant text messages.
        if event_type in {"assistant", "message", "text", "assistant_message", "result"}:
            content = _extract_text(payload)
            if content:
                emit_message(content)
            return events, texts

        # Tool calls.
        if event_type in {"tool_call", "tool_use", "tool", "function_call"}:
            tool_name = str(
                payload.get("name") or payload.get("tool") or payload.get("function") or "tool"
            ).strip() or "tool"
            tool_call_id = str(
                payload.get("id") or payload.get("call_id") or payload.get("toolCallId")
                or generate_id("tool")
            )
            args = (
                payload.get("input")
                or payload.get("arguments")
                or payload.get("args")
                or {}
            )
            event = {
                "event_id": generate_id("evt"),
                "type": "runner.tool_call",
                "quest_id": quest_id,
                "run_id": run_id,
                "source": self.runner_name,
                "skill_id": skill_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "status": "calling",
                "args": json.dumps(args, ensure_ascii=False),
                "created_at": created_at,
            }
            events.append(event)
            known_tools[tool_call_id] = event

            status = str(
                payload.get("status") or payload.get("state") or ""
            ).strip().lower()
            has_error = bool(payload.get("error"))
            if status in _OMP_TERMINAL_STATUSES or has_error:
                output = payload.get("output") or payload.get("result") or ""
                rendered = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
                events.append(
                    {
                        "event_id": generate_id("evt"),
                        "type": "runner.tool_result",
                        "quest_id": quest_id,
                        "run_id": run_id,
                        "source": self.runner_name,
                        "skill_id": skill_id,
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "status": "failed" if (status.startswith("fail") or status == "error" or has_error) else "completed",
                        "args": json.dumps(args, ensure_ascii=False),
                        "output": rendered,
                        "created_at": created_at,
                    }
                )
            return events, texts

        # Fallback: surface raw text payloads as messages, otherwise ignore.
        if isinstance(payload.get("text"), str):
            emit_message(payload["text"])
        elif isinstance(payload.get("message"), str):
            emit_message(payload["message"])
        return events, texts


def _extract_text(payload: dict[str, Any]) -> str:
    content = payload.get("content") or payload.get("text") or payload.get("message")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _copy_tree_optional(target: Path, source: Path) -> None:
    """Best-effort recursive copy that never fails on a missing source."""
    import shutil

    if not source.exists() or not source.is_dir():
        return
    ensure_dir(target)
    for item in sorted(source.rglob("*")):
        rel = item.relative_to(source)
        dest = target / rel
        if item.is_dir():
            ensure_dir(dest)
        else:
            ensure_dir(dest.parent)
            try:
                shutil.copy2(item, dest)
            except OSError:
                pass
