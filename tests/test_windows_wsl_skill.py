from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skills" / "deepscientist-windows-wsl-setup"
AUDITOR = REPO_ROOT / "scripts" / "audits" / "audit_windows_wsl_skill.py"


def test_windows_wsl_skill_inventory_is_complete() -> None:
    expected = {
        "SKILL.md",
        "agents/openai.yaml",
        "references/deepscientist-windows-wsl-notes.md",
        "scripts/find-wsl-proxy.ps1",
        "orchestration/deterministic-playbook.md",
        "checklists/completion-checklist.md",
    }
    actual = {
        path.relative_to(SKILL_ROOT).as_posix()
        for path in SKILL_ROOT.rglob("*")
        if path.is_file()
    }
    assert expected <= actual


def test_windows_wsl_skill_auditor_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(AUDITOR)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_skill_distinguishes_linux_ci_from_windows_wsl_target() -> None:
    text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "Current repository/agent branch default: Linux." in text
    assert "Target user environment: Windows host plus WSL2 Linux distro." in text
    assert "do not treat a Linux sandbox as evidence that the target Windows machine is ready." in text


def test_deterministic_playbook_has_gates_and_ready_state() -> None:
    text = (SKILL_ROOT / "orchestration" / "deterministic-playbook.md").read_text(encoding="utf-8")
    for state in ("S0 source refresh", "S8 auth route", "S10 doctor", "S12 browser", "BLOCKED-HUMAN", "READY"):
        assert state in text
    for rule in ("Observation", "Decision", "Action", "Validation"):
        assert rule in text
