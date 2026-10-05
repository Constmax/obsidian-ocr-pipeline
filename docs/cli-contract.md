# CLI Contract — Stages 1/2 ↔ Plugin

_Issue #55. The plugin is a thin client: it spawns `pdf2md` and `reprocess-raw` and reads their output. Everything it relies on is pinned in [`contracts/cli-contract.json`](../contracts/cli-contract.json) and [`contracts/progress-v1.jsonl`](../contracts/progress-v1.jsonl). Both test suites read these files (`pdf2md/test/test_cli_contract.py`, `pdf2md/test/test_cases.py`, `bin/test/test_stage1_contract.py`, `plugin/test/cli-contract.test.ts`, `plugin/test/page-cases.test.ts`), so a change on only one side turns CI red. The Markdown preview file has its own specification in [`preview-format.md`](preview-format.md)._

To change the contract, change the JSON file first, then both sides, in one PR.

## 1. Progress Protocol (`pdf2md --progress`)

- **Channel:** stderr, one JSON object per line. Other stderr lines (warnings, tracebacks) are not JSON objects and are kept as human output.
- **Vocabulary:** German keys, exactly as listed in the contract. This is the one vocabulary; the plugin no longer accepts English aliases, which no producer ever emitted.
- **Version:** every event carries `"protokoll": 1`. The plugin reads version 1 and ignores events of any other version. An event without the field comes from a pdf2md older than the field and is read as version 1.
- **Validation:** a missing or mistyped required field rejects the event. Unknown fields are ignored, so a new optional field is compatible. It is added to the contract first, because pdf2md's tests reject undeclared fields.

| Event | Required fields | Optional | Meaning |
|---|---|---|---|
| `start` | `datei` string, `seiten` integer, `dpi` integer | — | Analysis done; `seiten` = pages this run converts |
| `seite` | `nr`, `von` integer; `sekunden` number; `herkunft` `textlayer`\|`ocr`\|`diagramm`; `entgleist` boolean | `grund` string | One page finished (and cached); `sekunden` is rounded to 0.1 s and is 0 for a page reused from the cache |
| `fertig` | `ziel` string, `sekunden` number, `entgleist` integer | — | Preview written, run complete |

**Order:** exactly one `start`, then one `seite` per converted page in page order, then `fertig`. `start` comes after the analysis and before the model load, so a long pause after it is the model loading.

**Cancellation and failure have no event.** A run that emits no `fertig` event ended through its exit code:

## 2. Exit Codes (`pdf2md`)

| Code | Contract name | Meaning |
|---|---|---|
| 0 | `success` | Preview written (`fertig` emitted); `--check`: all checks passed |
| 1 | `error` | Error with a one-line message on stderr (bad input, page range, unmergeable preview, missing venv in the wrapper) |
| 2 | `usage` | argparse usage error |
| 4 | `check-failed` | `--check`: at least one check failed |
| 6 | `cancelled-partial` | Cancelled (SIGINT/SIGTERM); a partial file was written |
| 7 | `cancelled-empty` | Cancelled before the first page; nothing was written |

## 3. Stage 1 (`reprocess-raw`, `column_tools.py`)

Stage 1 has no structured channel. The plugin reads three kinds of human lines, and the contract pins the exact lines the real scripts print:

- **B5 short pages** (stderr of `column_tools.py verify-pages`, passed through by `reprocess-raw`): `   🗑️  Page 3: only 5 characters (min: 50)`. The plugin collects the page numbers with `SHORT_PAGE_LINE` and offers them as exemptions.
- **Failure reason:** the last line starting with `❌` (stdout or stderr), e.g. `❌ File not found: missing.pdf`. The plugin shows it without the marker.
- **Run mode** (#180): the plugin runs `reprocess-raw <pdf> --in-place --keep-original <vault path>`. Every refusal, including `❌ Source changed during processing, not replacing it: …` and `❌ Every page already has a text layer, nothing to add: …`, reaches the user through the failure-reason rule above; none is pinned separately. Once the replacing rename starts, the script ignores `SIGTERM`, so a cancelled run never reports success and a successful one never reports cancellation.
- **Engine check** (#73, `stage1.checkEngine`): `reprocess-raw --check-engine --engine paddle --paddle-mode fast` exits `0` (`success`) when a run would start on that engine and `4` (`check-failed`) otherwise, with the reason on stderr. The plugin joins the last stderr lines, drops the `❌` marker, and shows the result as the reason PaddleOCR is not offered or not used. Stage-1 usage errors still exit `1`.

## 4. Page Cases (`pdf2md case`)

_Issue #140. The review view stashes a page before its first edit, marks a page as wrong, and asks which pages have a case. It knows neither the page-cache path nor the case format (`pdf2md/cases.py` owns both; [`scripts-detail.md`](scripts-detail.md#page-cases-pdf2md-case-stage-2))._

The plugin spawns `pdf2md` from the vault root with the preview's vault-relative path (`pageCases` in the contract):

| Call | When |
|---|---|
| `case stash <preview> --page N` | Before the first edit of page N is saved, once per page each time the preview is opened |
| `case add <preview> --page N [--note=TEXT]` | "Mark page as wrong"; the note is one line and is left out when empty |
| `case list <preview>` | When a preview under review is opened |

- **Exit code:** `0` (`success`) means done. Anything else is a failure, and its reason is the last stderr line (`pdf2md case: …`), shown without the prefix. A failed `stash` is logged and the edit is saved anyway; a failed `add` is shown to the user; a failed `list` shows no badges.
- **Case line** (stdout): `case <stem>/pNNN: <status>, fault stage <stage>`, with status `open` or `fixed` and fault stage `assembly` or `upstream`. `add` prints it for the case it wrote, `list` once per case of the preview. The plugin reads the page number, the status and the fault stage from it, and shows the lines `add` prints after it as they are. Every other stdout line is human text: the output of `stash`, and whatever a library prints first.
- **Previews under review only:** the page cache and `.cases/` stay in the preview folder, so the plugin makes none of these calls for a preview under `_accepted/` or `_rejected/`.

## 5. Shared Constants

- **Input formats:** `inputSuffixes` = `INPUT_SUFFIXES` in `pdf2md/conversion.py` = `CONVERTIBLE_EXTENSIONS` in `plugin/src/input-formats.ts`.
- **Preview format version:** `previewFormat` = `vorschau-format` written by `assembly.build_frontmatter` = `SUPPORTED_PREVIEW_FORMAT` in the parser. The review view shows a notice for a preview with an unknown version.

## Outside the Contract

`pdf2md --check --progress` prints one JSON document on stdout (`"typ": "check"`). The plugin does not read it; it is documented in [`scripts-detail.md`](scripts-detail.md#--check-stage-2).
