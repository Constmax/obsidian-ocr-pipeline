# AGENTS.md

OCR pipeline for scanned legal study materials, used inside the user's
Obsidian vault. Two use cases, one plugin:

- **Searchable copy** (Stage 1, `bin/`): scanned PDF → the same PDF with an
  invisible text layer.
- **PDF → Markdown** (Stage 2, `pdf2md/`): scanned PDF or page image → a
  preview with one page block per source page.
- **Review** (Stage 3, `plugin/`): the Obsidian plugin runs both and shows a
  preview beside its source for checking and correcting.

The plugin is where the user meets this code. Domain terms: `CONTEXT.md`.

## When work is done

- **Plugin path.** Every PR names the plugin action that runs the changed
  code (for example "OCR → Markdown on a two-column scan"). Code that no
  plugin action reaches (bench tooling, a flag the plugin never passes, an
  engine the settings do not offer) is preparation: the PR says so and names
  the issue that connects it to the plugin.
- **Real pages.** A quality fix is done when real pages from the user's vault
  change, measured the way their stage measures: Stage-2 assembly by page
  cases (`pdf2md/AGENTS.md`), reading order by the truth set
  (`bench/AGENTS.md`). Synthetic fixtures pin the behaviour; the real pages
  show that the fix is done.
- **Unseen pages.** Before changing a heuristic, name the target pages and
  the measurement that says done. Pages you tuned on stop counting as
  validation.
- **Green.** `make check` passes, the reference docs below are updated in the
  same PR, and an independent review ran.

## Working rules

- **Copyright.** Page text and page images of the scans stay in the vault.
  Repo, issues, PRs and commit messages carry ids (`<stem>/pNNN`), geometry
  and fingerprints.
- **Worktree.** Several agent sessions (Claude, Codex, bench runs) share this
  checkout and switch branches in it. Work in your own worktree, one branch
  per issue, and check `git branch --show-current` before every commit and
  push.
- **Queue.** The work order lives in GitHub issues: a tracking issue lists
  its items as sub-issues, in order. Record findings outside the current
  issue on the tracking issue or as a new issue, and keep them out of the PR.
- **Diagnose first.** For a bug: reproduce it, find the cause, then fix,
  with `mattpocock-skills:diagnosing-bugs`. The PR states the cause.
- **Language.** Code, comments, commit messages, `docs/` and the AGENTS
  files are English. `README.md` and `bench/ERGEBNIS.md` are German.
  Frontmatter keys and progress-event keys stay German: they are contract
  (`docs/preview-format.md`). Docs use the English option names; the German
  aliases still work.

## Before you change

| Area | Read first |
|---|---|
| `plugin/` | `plugin/AGENTS.md` |
| `bin/`, the Stage-1 engines | `bin/AGENTS.md` |
| `pdf2md/` | `pdf2md/AGENTS.md` |
| `bench/`, `ocrmypdf_paddle/`, reading order | `bench/AGENTS.md` |
| anything the plugin reads from a CLI: progress events, exit codes, message lines, input formats, preview format version | `docs/cli-contract.md` and `contracts/`; Python and TypeScript tests both read them, so change both sides in one PR |
| setup, venvs, `setup.sh`, `install.sh` | `docs/installation.md` |
| a design decision | the design records below, and `docs/adr/` once it exists (created lazily) |

## Commands

- `make test-fast`: unit tests, a few seconds. `make check`: everything CI
  runs, one target per CI job. Mark a test `@pytest.mark.slow` when it runs a
  CLI or pipeline end to end.
- `make check-cases [ISSUE=n]`: replays the page cases of the vault
  (`VAULT_ROOT`). Cases hold page text, so it is never part of `make check`
  or CI; see `pdf2md/AGENTS.md`.
- `./setup.sh` is the one installation path (idempotent); `install.sh` and
  `plugin/install-plugin.sh` are building blocks it calls. venvs live under
  `VENV_ROOT` (default `~/.venvs`): `ocrmypdf` and `mlxocr`.
- Claude Code on the web: the SessionStart hook installs the CI toolchain in
  the background. `make check` is ready once `$VENV_ROOT/.session-start.done`
  exists.

## Docs

Reference docs describe what the code does now. Update them in the same PR as
the code:

- `docs/scripts-detail.md`: flags of Stages 1 and 2
- `docs/preview-format.md`: normative preview format
- `docs/review-view.md`: the plugin's views, commands, settings, folder model
- `docs/installation.md`: setup troubleshooting, which venv holds what
- `docs/ocr-preview.md`, `docs/vault-integration.md`, `docs/log-und-git.md`,
  `skill/SKILL.md`: vault-side conventions for the Claude skill (the vault
  keeps its own, diverged copy under `.claude/skills/pdf-jura-workflow/`)

Design records hold the reasoning behind decisions; the status header at the
top says what is done: `docs/plugin-roadmap.md` (thin client),
`docs/stage1-ui.md` (searchable-copy action), `docs/paddle-textlayer.md`
(PaddleOCR engine), `docs/BUGREPORT-2026-07-06-split-merge.md` (why the B5
gate exists).

## Agent skills

- **Issue tracker:** GitHub Issues on `Constmax/obsidian-ocr-pipeline` via
  `gh`; see `docs/agents/issue-tracker.md`.
- **Triage labels:** `needs-triage`, `needs-info`, `ready-for-agent`,
  `ready-for-human`, `wontfix`; see `docs/agents/triage-labels.md`.
- **Domain docs:** single context, root `CONTEXT.md` + `docs/adr/` (created
  lazily); see `docs/agents/domain.md`. Sharpen a term or record a decision
  with `mattpocock-skills:domain-modeling`.
- **Code review:** when the user asks for a code review, always use the
  `mattpocock-skills:code-review` skill, not the built-in `/code-review`.
- **Heuristic changes:** `mattpocock-skills:tdd` for the synthetic fixture;
  done is still measured on real pages (see "When work is done").
- **New queue:** `mattpocock-skills:grilling` with the user on the plan
  before the sub-issues exist.
- **Agent docs:** `mattpocock-skills:writing-for-agents` when editing an
  AGENTS file, `CLAUDE.md` or `skill/SKILL.md`.
