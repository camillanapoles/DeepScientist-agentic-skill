#!/usr/bin/env python3
"""Audit the Windows/WSL setup skill for deterministic, executable guidance.

The checker intentionally uses only the Python standard library so it can run in
minimal Linux CI jobs and in local sandboxes without installing project
dependencies.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = REPO_ROOT / "skills" / "deepscientist-windows-wsl-setup"
SKILL_MD = SKILL_ROOT / "SKILL.md"
NOTES_MD = SKILL_ROOT / "references" / "deepscientist-windows-wsl-notes.md"
AGENT_YAML = SKILL_ROOT / "agents" / "openai.yaml"
PROXY_PS1 = SKILL_ROOT / "scripts" / "find-wsl-proxy.ps1"

REQUIRED_FILES = (
    SKILL_MD,
    NOTES_MD,
    AGENT_YAML,
    PROXY_PS1,
    SKILL_ROOT / "orchestration" / "deterministic-playbook.md",
    SKILL_ROOT / "checklists" / "completion-checklist.md",
)

EXPECTED_DIRS = (
    SKILL_ROOT / "agents",
    SKILL_ROOT / "references",
    SKILL_ROOT / "scripts",
    SKILL_ROOT / "orchestration",
    SKILL_ROOT / "checklists",
)

REQUIRED_PHRASES = {
    SKILL_MD: (
        "name: deepscientist-windows-wsl-setup",
        "description:",
        "## Deterministic orchestration",
        "## Source-of-truth refresh",
        "## Hard stop gates",
        "## Operating-environment contract",
        "## Linux default for audit and development",
        "wsl.exe is a Windows administrative client",
        "do not treat a Linux sandbox as evidence that the target Windows machine is ready",
        "ds doctor",
        "http://127.0.0.1:20999",
        "references/deepscientist-windows-wsl-notes.md",
        "scripts/find-wsl-proxy.ps1",
        "checklists/completion-checklist.md",
        "orchestration/deterministic-playbook.md",
    ),
    NOTES_MD: (
        "# DeepScientist Windows WSL Notes",
        "## Deterministic execution matrix",
        "## Decision table",
        "HCS_E_CONNECTION_TIMEOUT",
        "appendWindowsPath=false",
        "Codex startup probe completed",
    ),
}

FORBIDDEN_PHRASES = {
    SKILL_MD: (
        "source of truth for install commands",
        "Typical pattern:\n```bash\nnpm install -g @openai/codex@0.57.0",
    ),
}

EXPECTED_LINKS = {
    "README.md": "docs/en/32_WINDOWS_WSL2_DEPLOYMENT_GUIDE.md",
    SKILL_MD.relative_to(REPO_ROOT).as_posix(): "references/deepscientist-windows-wsl-notes.md",
}

# Commands that are illustrative in the skill but must not be executed by this
# auditor. The real target is a user-controlled Windows/WSL machine.
DANGEROUS_LOCAL_MARKERS = (
    "wsl --install",
    "wsl --unregister",
    "npm install -g",
    "sudo apt-get install",
)


@dataclass
class Finding:
    severity: str
    code: str
    path: str
    message: str


class Auditor:
    def __init__(self, root: Path, strict: bool = True) -> None:
        self.root = root
        self.strict = strict
        self.findings: list[Finding] = []

    def add(self, severity: str, code: str, path: Path, message: str) -> None:
        self.findings.append(Finding(severity, code, path.relative_to(self.root).as_posix(), message))

    def text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def run(self) -> int:
        for path in REQUIRED_FILES:
            if not path.exists():
                self.add("error", "missing-file", path, "Required skill file is missing")
        for directory in EXPECTED_DIRS:
            if not directory.is_dir():
                self.add("error", "missing-directory", directory, "Required skill directory is missing")
        if self.findings:
            return self.report()

        self.audit_recursive_inventory()

        for path, phrases in REQUIRED_PHRASES.items():
            text = self.text(path)
            for phrase in phrases:
                if phrase not in text:
                    self.add("error", "missing-phrase", path, f"Missing required phrase: {phrase!r}")

        for path, phrases in FORBIDDEN_PHRASES.items():
            text = self.text(path)
            for phrase in phrases:
                if phrase in text:
                    self.add("error", "forbidden-phrase", path, f"Remove stale or unsafe phrase: {phrase!r}")

        self.audit_frontmatter(SKILL_MD)
        self.audit_agent_yaml()
        for markdown_path in SKILL_ROOT.rglob("*.md"):
            self.audit_markdown_links(markdown_path)
        self.audit_powerShell_script()
        self.audit_operating_environment_contract()
        self.audit_deterministic_playbook()
        self.audit_security()

        return self.report()

    def report(self) -> int:
        counts = {"error": 0, "warning": 0, "note": 0}
        for finding in self.findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        payload = {
            "skill": "deepscientist-windows-wsl-setup",
            "repo_root": str(self.root),
            "counts": counts,
            "findings": [asdict(item) for item in self.findings],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if counts["error"] else 0

    def audit_recursive_inventory(self) -> None:
        allowed_suffixes = {".md", ".yaml", ".ps1", ".txt", ".json", ".sh"}
        for path in sorted(SKILL_ROOT.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(SKILL_ROOT).as_posix()
            if path.suffix.lower() not in allowed_suffixes:
                self.add("warning", "unexpected-file-type", path, f"Review unexpected file type in skill inventory: {rel}")
            if path.stat().st_size == 0:
                self.add("error", "empty-file", path, "Skill file must not be empty")
            if "\ufeff" in self.text(path):
                self.add("warning", "utf8-bom", path, "Avoid UTF-8 BOM in skill files")

    def audit_frontmatter(self, path: Path) -> None:
        text = self.text(path)
        match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.S)
        if not match:
            self.add("error", "frontmatter", path, "SKILL.md must start with YAML frontmatter")
            return
        fm = match.group(1)
        for key in ("name:", "description:"):
            if key not in fm:
                self.add("error", "frontmatter-field", path, f"Frontmatter must include {key}")
        if len(re.search(r"description: (.+)", fm).group(1)) > 500:
            self.add("warning", "description-length", path, "Keep description concise and trigger-focused")

    def audit_agent_yaml(self) -> None:
        text = self.text(AGENT_YAML)
        for key in ("interface:", "display_name:", "short_description:", "default_prompt:", "policy:", "allow_implicit_invocation: true"):
            if key not in text:
                self.add("error", "agent-yaml", AGENT_YAML, f"Missing {key}")
        if "$deepscientist-windows-wsl-setup" not in text:
            self.add("error", "agent-variable", AGENT_YAML, "default_prompt must reference $deepscientist-windows-wsl-setup")

    def audit_markdown_links(self, path: Path) -> None:
        text = self.text(path)
        links = re.findall(r"\[[^\]]+\]\(([^)#]+)\)", text)
        for link in links:
            if link.startswith(("http://", "https://", "mailto:")):
                continue
            target = (path.parent / link).resolve()
            try:
                target.relative_to(self.root)
            except ValueError:
                self.add("error", "link-escape", path, f"Link escapes repository: {link}")
                continue
            if not target.exists():
                self.add("error", "broken-link", path, f"Broken relative link: {link}")

    def audit_powerShell_script(self) -> None:
        text = self.text(PROXY_PS1)
        required = (
            "param(",
            "$Ports",
            "Get-NetTCPConnection",
            "vEthernet (WSL*",
            "ConvertTo-Json",
            "loopback-only",
            "candidate-ready",
        )
        for item in required:
            if item not in text:
                self.add("error", "proxy-script", PROXY_PS1, f"PowerShell helper missing {item!r}")
        if "Invoke-WebRequest" in text or "Set-ItemProperty" in text:
            self.add("error", "proxy-script-side-effect", PROXY_PS1, "Proxy helper must be read-only")
        if "\r\n" in text:
            self.add("warning", "crlf", PROXY_PS1, "Prefer LF in Git to avoid noisy diffs")

    def audit_operating_environment_contract(self) -> None:
        text = self.text(SKILL_MD)
        if "Current repository/agent branch default: Linux." not in text:
            self.add("error", "os-contract", SKILL_MD, "Document Linux as the current audit/development default")
        if "Target user environment: Windows host plus WSL2 Linux distro." not in text:
            self.add("error", "os-contract", SKILL_MD, "Document target Windows+WSL environment")

    def audit_deterministic_playbook(self) -> None:
        path = SKILL_ROOT / "orchestration" / "deterministic-playbook.md"
        text = self.text(path)
        for phrase in (
            "State",
            "Observation",
            "Decision",
            "Action",
            "Validation",
            "BLOCKED-HUMAN",
            "READY",
        ):
            if phrase not in text:
                self.add("error", "playbook", path, f"Playbook must include {phrase!r}")
        if text.count("|") < 40:
            self.add("error", "playbook-thin", path, "Playbook needs a concrete decision/action matrix")

    def audit_security(self) -> None:
        for path in REQUIRED_FILES:
            if path.suffix not in {".md", ".yaml", ".ps1"}:
                continue
            text = self.text(path)
            if re.search(r"sk-[A-Za-z0-9]{12,}", text):
                self.add("error", "secret-like-value", path, "Document contains a secret-like API key value")
            if "sk-sp-your-real-key" in text:
                self.add("warning", "provider-specific-key", path, "Avoid provider-specific real key examples; use placeholders")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="Repository root")
    args = parser.parse_args(list(argv) if argv is not None else None)
    return Auditor(args.root.resolve()).run()


if __name__ == "__main__":
    sys.exit(main())
