"""Regression tests for the OMP runner bootstrap probe.

Covers the bug where `ds doctor` crashed with
`KeyError: Unknown runner 'omp'` as soon as the omp binary became
available on PATH, because `ConfigManager.probe_runner_bootstrap`
had no `omp` branch.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from deepscientist.config.service import ConfigManager


def _runners_payload() -> dict:
    return {"omp": {"binary": "omp"}}


def test_probe_runner_bootstrap_knows_omp_when_binary_missing(
    temp_home: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "deepscientist.config.service.resolve_runner_binary",
        lambda binary, runner_name=None: None,
    )
    manager = ConfigManager(temp_home)
    result = manager.probe_runner_bootstrap("omp", persist=False, payload=_runners_payload())
    assert result["ok"] is False
    assert any("not available" in error for error in result["errors"])
    assert result["guidance"], "missing-binary guidance should not be empty"


def test_probe_runner_bootstrap_omp_reports_ok_on_version_success(
    temp_home: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "deepscientist.config.service.resolve_runner_binary",
        lambda binary, runner_name=None: "/usr/local/bin/omp",
    )

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(returncode=0, stdout="omp/17.3.8\n", stderr="")

    monkeypatch.setattr(
        "deepscientist.config.service.subprocess.run",
        fake_run,
    )
    # Keep the warning path deterministic regardless of the host machine.
    monkeypatch.setattr(Path, "exists", lambda self, _o=Path.exists: True)

    manager = ConfigManager(temp_home)
    result = manager.probe_runner_bootstrap("omp", persist=False, payload=_runners_payload())
    assert result["ok"] is True
    assert result["details"]["version"] == "omp/17.3.8"
    assert "omp/17.3.8" in result["summary"]


def test_probe_runner_bootstrap_omp_reports_failure_on_nonzero_exit(
    temp_home: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "deepscientist.config.service.resolve_runner_binary",
        lambda binary, runner_name=None: "/usr/local/bin/omp",
    )

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(
        "deepscientist.config.service.subprocess.run",
        fake_run,
    )

    manager = ConfigManager(temp_home)
    result = manager.probe_runner_bootstrap("omp", persist=False, payload=_runners_payload())
    assert result["ok"] is False
    assert result["details"]["exit_code"] == 1
