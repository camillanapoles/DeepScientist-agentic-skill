#!/usr/bin/env python3
"""Audit the Linux agent setup skill.

Standard-library only. Verifies inventory, frontmatter, required phrases,
relative links, absence of text-template engines, and that the Pydantic
playbook renders correctly via the orchestration service.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SKILL_ROOT = REPO_ROOT / "skills" / "deepscientist-linux-agent-setup"
SKILL_MD = SKILL_ROOT / "SKILL.md"
AGENT_YAML = SKILL_ROOT / "agents" / "openai.yaml"
PLAYBOOK = SKILL_ROOT / "orchestration" / "event-driven-playbook.md"
CHECKLIST = SKILL_ROOT / "checklists" / "completion-checklist.md"
NOTES = SKILL_ROOT / "references" / "omp-pi-coding-notes.md"

REQUIRED_FILES = (SKILL_MD, AGENT_YAML, PLAYBOOK, CHECKLIST, NOTES)

REQUIRED_PHRASES = {
    SKILL_MD: (
        "name: deepscientist-linux-agent-setup",
        "OMP",
        "omp",
        "Event-driven, deterministic orchestration",
        "single source of truth",
        "POST /api/orchestration/objects",
        "POST /api/orchestration/objects/<id>/events",
        "ds doctor",
        "curl -fsSL https://omp.sh/install | sh",
        "Pydantic",
        "Instructor",
        "closed-loop",
    ),
    PLAYBOOK: (
        "single source of truth",
        "Decision",
        "state machine",
        "Pydantic",
        "closed-loop",
    ),
    CHECKLIST: ("environment", "omp", "ds doctor", "state=ready"),
    NOTES: ("omp print", "~/.omp", "@oh-my-pi/pi-coding-agent"),
}


@dataclass
class Finding:
    severity: str
    code: str
    path: str
    message: str


class Auditor:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.findings: list[Finding] = []

    def add(self, severity, code, path, message):
        self.findings.append(Finding(severity, code, path.relative_to(self.root).as_posix(), message))

    def text(self, path):
        return path.read_text(encoding="utf-8")

    def run(self) -> int:
        for path in REQUIRED_FILES:
            if not path.exists():
                self.add("error", "missing-file", path, "Required skill file is missing")
        if self.findings:
            return self.report()

        for path, phrases in REQUIRED_PHRASES.items():
            text = self.text(path)
            for phrase in phrases:
                if phrase not in text:
                    self.add("error", "missing-phrase", path, f"Missing phrase: {phrase!r}")

        self.audit_no_text_templates(SKILL_ROOT)
        self.audit_frontmatter(SKILL_MD)
        for md in SKILL_ROOT.rglob("*.md"):
            self.audit_links(md)
        self.audit_pydantic_playbook()
        self.audit_security(SKILL_ROOT)
        return self.report()

    def audit_no_text_templates(self, root: Path) -> None:
        """Text templating engines must not appear in the skill or its templates."""
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix in {".j2", ".jinja", ".jinja2", ".tmpl"}:
                self.add("error", "text-template", path, "Text template files are forbidden; use Pydantic schemas.")
                continue
            text = self.text(path)
            if re.search(r"\{%.*?%\}|\{\{.*?\}\}", text):
                self.add("error", "text-template-syntax", path, "Text-template interpolation syntax is forbidden; use Pydantic.")
            if any(ref in text.lower() for ref in ("jinja", "jinja2", "mako", "cheetah")):
                self.add("error", "text-template-reference", path, "References to text templating engines are forbidden.")

    def audit_frontmatter(self, path):
        text = self.text(path)
        if not text.startswith("---\n"):
            self.add("error", "frontmatter", path, "Must start with YAML frontmatter")
            return
        for key in ("name:", "description:"):
            if key not in text.split("---", 2)[1]:
                self.add("error", "frontmatter-field", path, f"Missing {key}")

    def audit_links(self, path):
        for link in re.findall(r"\[[^\]]+\]\(([^)#]+)\)", self.text(path)):
            if link.startswith(("http://", "https://", "mailto:")):
                continue
            target = (path.parent / link).resolve()
            if not target.exists():
                self.add("error", "broken-link", path, f"Broken link: {link}")

    def audit_pydantic_playbook(self):
        """Drive the real service through a full flow and render the playbook."""
        try:
            from deepscientist.orchestration import OrchestrationService, create_store
            from deepscientist.orchestration.models import (
                EVT_CONFIG_COMPLETED,
                EVT_ENVIRONMENT_DETECTED,
                EVT_INSTALL_COMPLETED,
                EVT_PREFLIGHT_RESULT,
                EVT_VALIDATION_RESULT,
            )
        except Exception as exc:  # noqa: BLE001
            self.add("error", "import", Path(__file__), f"Could not import orchestration: {exc}")
            return

        with tempfile.TemporaryDirectory() as td:
            service = OrchestrationService(create_store(Path(td) / "state.db"))
            obj = service.create_object(name="audit-host", environment="linux")
            for evt, payload in [
                (EVT_ENVIRONMENT_DETECTED, {"environment": "linux"}),
                (EVT_PREFLIGHT_RESULT, {"ok": True}),
                (EVT_INSTALL_COMPLETED, {"ok": True}),
                (EVT_CONFIG_COMPLETED, {"ok": True}),
                (EVT_VALIDATION_RESULT, {"ok": True}),
            ]:
                result = service.ingest_event(obj.object_id, evt, payload)
                if not result.get("decision"):
                    self.add("error", "playbook-event", SKILL_MD, f"No decision for {evt}")
            rendered = service.render_playbook(obj.object_id)
            if not rendered.get("ok"):
                self.add("error", "playbook-render", SKILL_MD, "Playbook did not render")
                return
            md = rendered["markdown"]
            for required in ("audit-host", "omp", "linux", "ready", "Actions to execute now"):
                if required not in md:
                    self.add("error", "playbook-content", SKILL_MD, f"Rendered playbook missing {required!r}")
            if not rendered["verdict"]["ready_to_advance"]:
                self.add("error", "playbook-verdict", SKILL_MD, "Closed-loop verdict not ready after full flow")

    def audit_security(self, root):
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".md", ".yaml", ".sh"}:
                continue
            if re.search(r"sk-[A-Za-z0-9]{16,}", self.text(path)):
                self.add("error", "secret-like-value", path, "Contains a secret-like key")

    def report(self):
        counts = {"error": 0, "warning": 0, "note": 0}
        for f in self.findings:
            counts[f.severity] += 1
        print(json.dumps({
            "skill": "deepscientist-linux-agent-setup",
            "counts": counts,
            "findings": [asdict(f) for f in self.findings],
        }, indent=2))
        return 1 if counts["error"] else 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    return Auditor(args.root.resolve()).run()


if __name__ == "__main__":
    sys.exit(main())
