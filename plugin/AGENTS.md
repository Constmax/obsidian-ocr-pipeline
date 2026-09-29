# plugin/AGENTS.md

Stage 3: the Obsidian plugin (TypeScript, esbuild, no React). A thin client:
it spawns the installed CLIs and reads what `contracts/` pins
(`docs/plugin-roadmap.md`). Views, commands and settings:
`docs/review-view.md`.

## Build and ship

- **`main.js` is committed** so a clone runs without Node. After changing
  `src/`, run `npm run build` (tsc check + esbuild) and commit `main.js`
  with it; CI fails when the committed build differs from `src/`.
- Install into a vault: `VAULT_ROOT=<path> plugin/install-plugin.sh --enable`
  (plugin id from `manifest.json`; copies by default, `--build` builds first,
  `--symlink` only outside iCloud). `npm run dev` watches and copies to
  `$OBSIDIAN_PLUGIN_DIR` when set.

## Tests

- Plain `node --test --experimental-strip-types test/*.test.ts`, Node ≥22.6.
  Tests import `src/` modules directly with `.ts` extensions
  (`allowImportingTsExtensions`).
- Keep logic in modules without Obsidian imports (like `ocr-settings.ts`,
  `preview-parser.ts`) so it runs under `node --test`. `sync.test.ts` shims
  `window` and rAF; code touching `window.pdfjsLib`, `MarkdownRenderer` or the
  real DOM is verified by a smoke test in Obsidian, and the PR says which
  steps you ran there.
- ESLint uses `eslint-plugin-obsidianmd`. The `sentence-case` rule covers
  `src/settings.ts` only (older UI strings elsewhere still violate it);
  `no-console` allows `error` and `warn`.

## Obsidian API invariants

- **View methods:** name the method that opens a preview `openPreview()`.
  A custom `open()` on an `ItemView` / `View` subclass collides with
  Obsidian's internal `View.prototype.open(containerEl)` lifecycle.
- **pdf.js:** `loadPdfJs()` may not attach to `window.pdfjsLib`. Use
  `const pdfjs = window.pdfjsLib ?? await loadPdfJs(); (window as any).pdfjsLib = pdfjs;`.
- **UI construction:** call `ensureUiBuilt()` before `openPreview()`,
  `update()`, `setState()` and `onOpen()`, so leaf restores and conversions
  always meet built subcomponents.
- **Startup:** reconcile the cache and open initial previews inside
  `app.workspace.onLayoutReady(...)`, once the vault metadata is complete.
