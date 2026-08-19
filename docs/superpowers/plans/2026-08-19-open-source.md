# Agent Bridge Open Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish this tree to the existing public repo https://github.com/FeiZhuLulu/Agent-Bridge as a standard MIT project with bilingual READMEs, CI, and no Cursor-as-contributor / no Cursor-as-supported-worker on the public front.

**Architecture:** Docs and metadata only, plus one test-path anonymization. Git author stays `FeiZhuLulu`. Remote already exists; do not create a second repo. Do not commit `docs/plan.md`, `.venv`, `.cursor`, or `灵感`.

**Tech Stack:** Git, GitHub Actions, uv, pytest, MIT.

---

### Task 1: Public docs and legal files

**Files:**
- Create: `LICENSE`, `README.md`, `README.zh-CN.md`, `CONTRIBUTING.md`, `CONTRIBUTING.zh-CN.md`, `SECURITY.md`, `.github/workflows/ci.yml`, `.github/ISSUE_TEMPLATE/bug.yml`, `.github/ISSUE_TEMPLATE/feature.yml`
- Modify: `.gitignore`, `pyproject.toml`, `AGENTS.md`, `docs/codex-setup.md`, `tests/test_grok_observe.py`
- Do not add: `docs/plan.md`

- [ ] Write LICENSE as MIT, `Copyright (c) 2026 FeiZhuLulu` (replace the existing GitHub LICENSE copyright line when histories merge).
- [ ] Rewrite READMEs to the agreed one-paragraph pitch. No Cursor in the diagram or tool table.
- [ ] Short bilingual CONTRIBUTING + SECURITY + CI + issue templates.
- [ ] Anonymize Windows user paths in `tests/test_grok_observe.py`.
- [ ] Strip Cursor from AGENTS.md / README public "supported" lists. Keep the adapter in code.

### Task 2: Verify and commit as FeiZhuLulu

- [ ] `uv run pytest` — expect pass.
- [ ] `git add` only public files. `git commit` with local `user.name=FeiZhuLulu`. Author must not be Cursor.
- [ ] `git log -1 --format=full` shows `Author: FeiZhuLulu` and `Commit: FeiZhuLulu`.

### Task 3: Connect the existing GitHub repo and push

- [ ] `git remote add origin https://github.com/FeiZhuLulu/Agent-Bridge.git` (or set-url if present).
- [ ] Fetch `origin/main`. Merge unrelated histories. Keep our LICENSE copyright as FeiZhuLulu.
- [ ] `git push -u origin main`.
- [ ] Confirm https://github.com/FeiZhuLulu/Agent-Bridge shows the bilingual README and Contributors is only FeiZhuLulu.
