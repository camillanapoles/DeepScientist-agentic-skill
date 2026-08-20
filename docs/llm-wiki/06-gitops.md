# 06 — GitOps (how changes ship)

Framework changes go through PRs. No direct pushes to `main`.

```mermaid
flowchart LR
    E[edit framework] --> B["branch<br/>fix/* · feat/* · chore/*"]
    B --> C["commit (conventional,<br/>say WHY)"]
    C --> P["push + gh pr create"]
    P --> CI{"CI: orchestration suite<br/>+ skill audits"}
    CI -- fail --> F[fix locally, push again]
    CI -- pass --> M["gh pr merge --merge"]
    M --> S["local: checkout main + pull,<br/>delete branch"]
    S --> R{"release-worthy?"}
    R -- yes --> T["CHANGELOG [Unreleased]→version,<br/>bump pyproject, PR, merge,"]
    T --> G["git tag -a vX.Y.Z + push,<br/>gh release create"]
    R -- no --> D[done]
```

## Conventions in force (validated on PRs #11/#12, tag v1.7.0)

- Branch names: `fix/…`, `feat/…`, `chore/…`.
- Commit format: `type(scope): summary` + body explaining the why and the
  validation evidence.
- CI must be green on the PR before merge (`gh pr checks`).
- Releases: add/extend `CHANGELOG.md` (Keep a Changelog), bump
  `pyproject.toml`, merge via PR, then annotated tag + GitHub release.
- Local `main` is synced after every merge; feature branches are deleted.

## What is versioned

Only the **framework** (code, skills, docs, tests). Quests version
themselves in their own repos — never tag the framework for research
milestones.

Next: [07 — troubleshooting](07-troubleshooting.md).
