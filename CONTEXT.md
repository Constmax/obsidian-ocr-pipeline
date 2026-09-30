# OCR pipeline

Turns scanned legal study materials into searchable PDFs (Stage 1) and reviewable Markdown (Stage 2), and lets the user check both inside Obsidian (Stage 3).

## Language

### Recognition

**Recognized line**:
One line of text Stage 2 read from a source page, from the text layer or the model, with its position in page coordinates, its column where known and the table or box it belongs to.
_Avoid_: Zeile, element, span, line list

**Page coordinates**:
Positions measured on the whole source page, as opposed to positions within a tile.
_Avoid_: absolute coordinates, global coordinates

**Tile**:
A part of a page image that the model reads on its own, cut at the gutter or across a dense page.
_Avoid_: crop, band, Kachel (in English text)

### Preview and review

**Preview**:
The Markdown file Stage 2 writes for one source document, one page block per source page.
_Avoid_: output, result file, Vorschau (in English text)

**Page block**:
The part of a preview that belongs to one source page, from its page marker to the next.
_Avoid_: page section, chunk

**Review view**:
The Obsidian view that shows a preview beside its source for checking and correcting.
_Avoid_: comparison view, Abgleich-Ansicht (in English text)

### Page cases

**Page case**:
One source page the user marked as wrong, kept with the page block Stage 2 produced and the page block the user expects.
_Avoid_: test case, fixture, sample, truth page

**Stash**:
The produced page block of a page, kept silently when the user first edits it, so it survives a rerun until the page is marked or the preview is accepted or deleted.
_Avoid_: backup, snapshot

**Replay**:
Producing a page block again from a page case's recognized lines, without running the model.
_Avoid_: rerun (a rerun converts the source again), re-OCR

**Open / fixed**:
The status of a page case: open while its replay still differs from the expected page block, fixed once the user has confirmed that it matches.
_Avoid_: failing / passing, red / green (those describe one run, not the case)

**Fault stage**:
Where a page case's error arises: in assembly (the recognized lines hold everything the expected page block needs) or upstream (recognition, tiling or ordering lost or garbled it).
_Avoid_: root cause, error type
