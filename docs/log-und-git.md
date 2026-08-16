# Log & GitHub Sync — Common Template

## Unified Log Format (wiki/log.md)

Every entry follows exactly this schema:

```markdown
## [YYYY-MM-DD] action | Title
Brief description of what happened.
```

### Action Types and Required Fields

| Action | Title Format | Required Content |
|---|---|---|
| `ingest` | Name of source | Created/updated pages (list) |
| `batch-ingest` | Topic / folder | Number of sources, created/updated pages |
| `klausur-ingest` | Exam identifier | Exam page, extracted problems, schema changes |
| `lint` | Scope (e.g. "Civil Law" or "Full Wiki") | Issues found / fixed, raw checks, gaps |
| `pdf-workflow` | Processed files | Input files, output to raw/ |
| `update` | Affected page(s) | What was changed and why |

### Examples

```markdown
## [2026-04-25] ingest | BGB AT Part 1 — Script WS 2026
Summary created: [[bgb-at-teil-1-klausurtechnik]]. Problem updated: [[erklaerungsbewusstsein]]. Schema added: [[pruefungsaufbau-primaeranspruch]].

## [2026-04-25] batch-ingest | Criminal Law AT Scripts (3 sources)
3 sources processed. New: [[strafrecht-at-teil-1-grundlagen]], [[notwehr]], [[notwehrexzess]]. Updated: [[schuldfaehigkeit]].

## [2026-04-25] klausur-ingest | BGB-AT Case 12 — Minor
Exam page: [[bgb-at-fall-12-minderjaehriger-suesswaren]]. Problems extracted: [[geschaeftsfaehigkeit]] (exam reference added), [[anfechtung]] (exam reference added).

## [2026-04-25] lint | Civil Law
Structure: 4 broken links fixed, 2 orphaned pages found. Raw check on 3 pages. Gaps: Enrichment law overview missing.

## [2026-04-25] pdf-workflow | Hemmer BGB AT Part 4+5 (Scans)
Input: raw/assets/hemmer-bgb-at/ (23 images). Output: raw/4_BGB_AT_Teil_4.pdf, raw/5_BGB_AT_Teil_5.pdf (tesseract, 200 DPI).
```

---

## Git Commit

**No auto-push.** Commits occur **only upon explicit user request** or during the **daily batch commit** — not after every single workflow (which would flood the git history and conflicts with actual practice).

When a commit is due, execute from **vault root** (vault is located at `~/JuraExamenVault`, **not** under the old dead iCloud path `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/PW`):

```bash
# from vault root (CWD is already vault root during skill invocation)
git add -A
git commit -m "YYYY-MM-DD action: Title"
```

Commit message corresponds to the log entry title, e.g.:
- `2026-04-25 ingest: BGB AT Part 1 — Script WS 2026`
- `2026-04-25 lint: Civil Law`
- `2026-04-25 pdf-workflow: Hemmer BGB AT Part 4+5`

**Push** only if requested by the user. If no remote is configured, skip `git push` and inform the user. No `--force`, no auto-push to `main`/`master`.

