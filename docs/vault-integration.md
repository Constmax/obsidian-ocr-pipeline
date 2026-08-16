# Vault Integration — Co-existence with CLAUDE.md Workflows

This skill serves as the pre-processing stage for vault workflows. Following PDF processing, the vault-specific ingest workflow (defined in `<vault>/CLAUDE.md`) takes over.

## Division of Responsibilities

| Component | Responsible For |
|---|---|
| **This Skill** | Images/PDFs → searchable, compressed PDF in `raw/` |
| **Vault CLAUDE.md** | `raw/` → `wiki/` (Summaries, legal issues, structures, query linking) |

This skill **completes** its execution once the final PDF resides in `raw/`. It writes **no wiki pages** and updates **no index**.

## Typical Ingest Sequence

### Scenario A: Single Source (Court Decision, Short Script, Exam Case)

```
1. User: "I scanned a new BGH court decision, please ingest"
2. Skill active
   ├─ Check raw/assets/ → images or existing PDF?
   ├─ Execute pdf-workflow or pdf-combine accordingly
   └─ Move output to raw/
3. Skill completes → CLAUDE.md "Ingest" workflow starts
   ├─ Read source file
   ├─ Create summary page in wiki/
   ├─ Extract legal issues
   └─ Update index + log
```

### Scenario B: Batch Import (Entire Course Material Set)

```
1. User: "Here are 40 course scripts from this semester, run batch-ingest"
2. Skill active
   ├─ pdf-auto raw/assets --cleanup --fast --engine tesseract
   │    (tesseract because course scripts = two-column layout)
   ├─ Move _processed/*.pdf to raw/
   └─ Verify _archive/ containing original scans
3. Skill completes → CLAUDE.md "Batch-Ingest" workflow takes over
```

### Scenario C: Exam Case Ingest (Special Case!)

CLAUDE.md maintains a strict distinction between standard ingest and exam case ingest. This skill operates identically in both scenarios — only subsequent wiki processing differs.

**Important**: This skill does not tag content as an "exam". Classification occurs exclusively during the wiki workflow based on user instruction.

## Engine Recommendations by Source Type

Aligned with CLAUDE.md taxonomy:

| Source Type (CLAUDE.md) | Layout | Engine | Rationale |
|---|---|---|---|
| Course Scripts (Hemmer/Kaiser) | 2-column, fine print | `tesseract` | Column detection critical |
| Court Decisions (BGH/BVerwG) | 1-column, body text | `apple` | Faster, cleaner |
| Textbook Chapters (Scans) | 1-column mostly | `apple` | Handles phone photos better |
| Law Journal Articles (JuS/NJW) | 2-column often | `tesseract` | Column handling |
| Lecture Notes | Variable | `apple` | Handles handwriting |
| Exam Cases / Solution Sketches | 1-column | `apple` | Standard |

## Adhering to Naming Conventions

From CLAUDE.md:

> Filenames: lowercase, hyphens instead of spaces
> No umlauts in filenames (ue, ae, oe, ss instead of ü, ä, ö, ß)

**Relevance to this skill**: Apply these naming conventions when constructing output file names:

```bash
# ✅ Compliant with conventions
pdf-combine raw/assets/urteile urteil-bgh-ix-zr-42-24

# ❌ Violates conventions (uppercase, spaces, umlauts)
pdf-combine raw/assets/urteile "Urteil BGH IX ZR 42 über Bürgschaft"
```

If the user supplies a name containing uppercase letters or umlauts, suggest or silently format to compliant style according to user preference.

## pdf-auto Part Detection and CLAUDE.md

The "Teil N" (Part N) pattern in `pdf-auto` differs from the vault filename convention:
- Part pattern requires spaces: `Verwaltungsrecht AT Teil 1.pdf`
- Vault convention requires hyphens: `verwaltungsrecht-at-teil-1.pdf`

**Practical Recommendation**:
- **In raw/assets/** (Input): arbitrary filenames, often with spaces + "Teil N"
- **In raw/** (Output): formatted to lowercase hyphenated convention

`pdf-auto` generates outputs such as `Verwaltungsrecht AT.pdf` (part suffix stripped, remaining string preserved). The downstream vault workflow can rename as needed:

```bash
mv "raw/assets/_processed/Verwaltungsrecht AT.pdf" "raw/verwaltungsrecht-at.pdf"
```

## Special Case: Obsidian iCloud Sync

When the vault resides in iCloud:
1. Scripts write `raw/foo.pdf` locally
2. iCloud Sync runs in background automatically
3. Obsidian on secondary devices receives the PDF after 1-5 minutes

When this skill generates multiple PDFs in rapid succession, iCloud upload may lag slightly behind local writing. This poses no issue for the skill, but serves as informational context for users working across iPad/iPhone devices.

## Interaction with Batch Ingest

CLAUDE.md Batch Ingest Step 2 states:
> Execute `pdf-auto raw/assets --cleanup --fast`. This generates a finalized PDF in `raw/assets/_processed/` for each subdirectory or group.

This skill implements that exact step. The additional intelligence provided by this skill: **Engine selection based on source type** (e.g., automatically applying `--engine tesseract` when course scripts are mentioned).

