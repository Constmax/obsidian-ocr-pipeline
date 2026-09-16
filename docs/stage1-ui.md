# Safe Stage-1 OCR from the Obsidian review view

**Status:** Plan; independently shippable with the existing Apple and Tesseract
engines.

## Goal

Let a user create a searchable PDF from Obsidian without risking the source
file. The normal result is a new sibling PDF such as `casebook-ocr.pdf`.

PaddleOCR is optional and specified separately in
[`paddle-textlayer.md`](paddle-textlayer.md). The UI must not wait for that
engine.

## Safety invariants

- The plugin remains a thin desktop client around installed Stage-1 CLIs.
- The source PDF is untouched by default.
- A destination appears in the vault only after processing and all quality
  gates succeed.
- Failure or cancellation leaves no partial PDF or `_FAILED_` artifact in the
  vault.
- Existing text is preserved by default. Destructive force-OCR behavior is a
  separate, exceptional workflow.
- Cancellation terminates the whole process group, including OCRmyPDF and its
  child processes.
- The plugin runs at most one Stage-1 or Stage-2 conversion at a time.

These are release gates, not later polish.

## 1. Add a source-preserving CLI interface

Extend `bin/reprocess-raw.sh` with an explicit output mode:

```bash
reprocess-raw source.pdf --output destination.pdf [pdf-combine options]
```

Keep the current no-`--output` behavior backward-compatible for existing shell
users. The new mode has these semantics:

1. Canonicalize source and destination before starting. Reject the same file,
   an existing destination, or a destination that resolves to the source.
2. Process in a private temporary directory outside the vault.
3. Preserve existing PDF text by default; do not pass `--force-ocr`.
4. Run the existing page-count, file-size, and B5 text-coverage checks. B5
   means at least 50 extracted characters per non-exempt page, not merely a
   non-empty text layer.
5. On success, copy to a hidden temporary file with a non-PDF suffix in the
   destination directory, then publish it with an atomic no-clobber operation.
   A plain `cp` or `mv` is insufficient because a destination created during
   OCR must not be overwritten. Use a same-filesystem exclusive primitive and
   fail cleanly if the final name appeared after the initial check.
   In Bash that primitive is effectively `ln` (fails if the target exists)
   followed by removing the temporary name.
   *Resolved in #63:* `ln` works and refuses existing targets inside both
   iCloud Drive vault locations (`~/Documents` and the Obsidian iCloud
   container), so there is no `mv` fallback. BSD `ln` links *into* a folder
   that appears under the destination name, so the script confirms the new
   link is the destination itself. Details in
   [`scripts-detail.md`](scripts-detail.md#source-preserving-mode---output-file).
6. On failure or cancellation, clean up all temporary output. `_FAILED_`
   artifacts remain available only to the legacy in-place shell workflow.

`--allow-pages` continues to exempt exact pages from B5 without weakening the
gate for other pages. It must not change page numbering or output order.

An in-place Obsidian action is outside the first release. If added later, it
must still build and validate a complete temporary result before an atomic
final replacement.

**Complete when:** shell tests in `bin/test/` (stubbing OCR like the existing
tests) prove byte-for-byte source immutability,
same-file and collision rejection (including a destination-created-during-run
race), atomic publication, cleanup on failure and signal, correct 50-character
B5 behavior, and stable `--allow-pages` semantics.

## 2. Deepen the plugin process module

Prerequisite: #53 extracts the conversion lifecycle (running state, child
ownership, cancellation, timeout, result mapping) from `main.ts` into a
`ConversionController`. Add the Stage-1 call there; without #53 this step
would duplicate that lifecycle inside `main.ts`.

Keep process management in `plugin/src/conversion.ts` and extend its interface
instead of adding a second spawn path. The Stage-1 call accepts:

- source and destination paths;
- CLI path and working directory;
- engine, split-column, and allowed-page options;
- injected spawn and termination functions for tests; and
- the existing progress, completion, and error callbacks.

Spawn the CLI in a new process group on macOS. Cancellation first sends
`SIGTERM` to the group, waits a bounded interval, then sends `SIGKILL` if any
member remains. Treat an already-exited group as successful cancellation
cleanup, not a secondary error. Plugin unload uses the same group termination;
a detached group otherwise keeps running after Obsidian closes.

Use indeterminate progress for the first release. A future CLI can emit JSON
progress without changing the UI-facing conversion interface.

*Implemented in #64* as `createSearchableCopy` and `terminateProcessGroup` in
`conversion.ts` and `ConversionController.runOcr`. Two additions: the spawn
prepends `~/bin`, `/opt/homebrew/bin` and `/usr/local/bin` to `PATH`, because
Obsidian started from the Dock lacks them and `reprocess-raw` needs OCRmyPDF,
qpdf, Ghostscript and Poppler; and Stage 1 has no timeout, since OCR time grows
with page count and the user can cancel. On quit, the `SIGKILL` escalation
only happens if Obsidian is still running when the grace period ends.

**Complete when:** unit tests cover exact arguments, spawn errors, ordinary
failure, cancellation escalation, and callback behavior; a local process-tree
test proves that no descendant remains after cancellation; and all existing
Stage-2 conversion tests stay green.

## 3. Implement the default user workflow

The primary action is **Create searchable copy (OCR)**. It:

- uses saved engine and column settings;
- omits `--force-ocr`;
- proposes `<stem>-ocr.pdf` beside the source;
- stops before spawning when that destination exists; and
- opens the new PDF after success without replacing the source.

Expose the action from:

- the PDF file menu;
- a command that asks the user to select a PDF; and
- the comparison view for its current PDF.

The comparison view should expose a small `currentPdf()` interface rather than
letting commands reach into private view state.

On success, show a notice with an action to open the sibling PDF. Do not run the
Markdown cache inventory or Stage-2 reconciliation for a Stage-1-only result.

If B5 fails, show the exact short pages. Offer **Run with page exemptions…**,
which opens a modal prefilled with those page numbers and reruns only after the
user confirms the explicit list. Do not add a global “ignore B5” setting.

An exceptional **Rebuild OCR text layer…** action may be designed after the
safe workflow ships. It must warn that force OCR rasterizes born-digital text
and must still produce a sibling file by default.

**Complete when:** a normal scan, a mixed searchable/scanned PDF, a destination
collision, and an exempt cover page each have one clear path with no source
mutation.

## 4. Add minimal persisted settings

Persist:

```typescript
interface OcrSettings {
  ocrEngine: "auto" | "apple" | "tesseract";
  splitColumns: boolean;
}
```

Defaults are `auto` and `false`. Validate loaded values and explicitly migrate
missing or invalid historical data. Add `paddle` only after the engine plan's
retention and installation gates pass.

The action is desktop-only because it needs filesystem paths and child
processes. Resolve paths only through Obsidian's filesystem adapter and show a
precise unsupported-platform message elsewhere.

**Complete when:** settings survive reload, missing and invalid values fall back
predictably, the settings UI passes the sentence-case lint rule, and Paddle is
not offered when it is unavailable.

## Verification matrix

Headless plugin tests:

- exact CLI arguments and absence of `--force-ocr`;
- collision detection before spawn;
- spawn and non-zero-exit errors;
- process-group cancellation and escalation;
- settings defaults, validation, and migration;
- B5 exemption rerun arguments; and
- no regression in the existing Stage-2 workflow.

Shell integration tests:

- source hash is unchanged after success, failure, and cancellation;
- destination appears only after success;
- a pre-existing destination is unchanged;
- a destination created while OCR is running is not overwritten;
- short pages fail at 50 characters unless explicitly exempted;
- page exemptions do not hide failures on other pages; and
- no partial or `_FAILED_` vault artifact remains.

Obsidian smoke tests:

- all three entry points target the correct PDF;
- cancellation leaves no OCRmyPDF descendants;
- success opens the sibling and keeps the source available;
- duplicate basenames in different folders resolve correctly;
- settings persist across restart; and
- no partial file appears in the vault during processing.

Before delivery, run from `plugin/`:

```bash
npm run lint
npm run check
npm test
npm run build
```

Commit the regenerated `plugin/main.js` with the source changes.

## Execution order and dependencies

1. Specify `reprocess-raw --output` behavior and shell tests.
2. Implement source-preserving temporary processing and atomic publication.
3. Extend and test the shared plugin process module.
4. Add settings and migration.
5. Add the command, file-menu, and comparison-view entry points.
6. Add the B5 exemption modal and exact-page retry.
7. Run local process-tree and Obsidian smoke tests.
8. Run the full plugin, shell, and Python verification suites.

Steps 3–6 may start only after the CLI interface is stable; steps 3, 5, and 6
also require #53. PaddleOCR has no
place in this dependency chain.

## Estimated effort

| Work | Estimate |
| --- | ---: |
| Source-preserving CLI mode and tests | 0.5–1 day |
| Plugin process management and cancellation | 0.5 day |
| Settings and three UI entry points | 0.5 day |
| B5 exemption flow and smoke tests | 0.5–1 day |
| **Total** | **2–3 days** |
