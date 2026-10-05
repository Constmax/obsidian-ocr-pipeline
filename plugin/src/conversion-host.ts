// Obsidian side of the ConversionController and the "Add OCR text layer" action:
// Notices, vault paths, the inventory, the comparison view, and opening files.

import { FileSystemAdapter, Notice, Platform, TFile, normalizePath, type App } from "obsidian";

import { homedir } from "os";

import { resolveCli, resolvePdf2md, type ConversionHost } from "./conversion-controller.ts";
import { checkEngine, runPageCase } from "./conversion.ts";
import { PageCases } from "./page-cases.ts";
import { isConvertible } from "./input-formats.ts";
import type { Inventory } from "./file-actions.ts";
import { ExemptionModal } from "./exemption-modal.ts";
import type { TextLayerHost } from "./text-layer.ts";
import { confirmInModal } from "./confirm-modal.ts";
import { ProgressPresenter, type ProgressNotice, type ProgressSurfaces } from "./progress-display.ts";
import type { OcrEngine } from "./ocr-settings.ts";
import type { Settings } from "./settings.ts";

/**
 * `reprocess-raw --check-engine` for this machine, run from the vault folder
 * like the OCR text layer: null when `engine` is usable, otherwise the reason.
 */
export function checkEngineHere(app: App, engine: OcrEngine): Promise<string | null> {
	const adapter = app.vault.adapter;
	const cwd = adapter instanceof FileSystemAdapter ? adapter.getBasePath() : homedir();
	return checkEngine(engine, resolveCli("reprocess-raw"), cwd);
}

/**
 * Page cases for the review view: `pdf2md case …` run from the vault folder,
 * so the preview's vault-relative path names it. Null without file-system
 * access.
 */
export function createPageCases(app: App): PageCases | null {
	const adapter = app.vault.adapter;
	if (!Platform.isDesktopApp || !(adapter instanceof FileSystemAdapter)) return null;
	const cwd = adapter.getBasePath();
	return new PageCases((args) => runPageCase(args, resolvePdf2md(), cwd));
}

/** Hidden vault folder where `--keep-original` leaves a replaced PDF until it is trashed. */
const ORIGINALS_FOLDER = ".ocr-originals";

export function createTextLayerHost(app: App, settings: () => Settings): TextLayerHost {
	return {
		isDesktop: Platform.isDesktopApp,
		notify(message) {
			new Notice(message);
		},
		settings: () => settings(),
		checkEngine: (engine) => checkEngineHere(app, engine),
		async originalSlot(fileName) {
			// One folder per run keeps the file name, which the trash shows.
			const folder = normalizePath(`${ORIGINALS_FOLDER}/${Date.now()}`);
			try {
				if (await app.vault.adapter.exists(folder)) return null;
				await app.vault.adapter.mkdir(folder);
				return `${folder}/${fileName}`;
			} catch {
				return null;
			}
		},
		async trashOriginal(path) {
			try {
				if (await app.vault.adapter.trashSystem(path)) return true;
				await app.vault.adapter.trashLocal(path);
				return true;
			} catch {
				return false;
			}
		},
		async releaseOriginalSlot(path) {
			const folder = path.slice(0, path.lastIndexOf("/"));
			try {
				const { files, folders } = await app.vault.adapter.list(folder);
				if (files.length === 0 && folders.length === 0) await app.vault.adapter.rmdir(folder, false);
			} catch {
				// An empty hidden folder left behind is harmless.
			}
		},
		offerExemptions(offer) {
			// Stays until the user acts on it or clicks it away.
			const notice = new Notice(offer.message, 0);
			const button = notice.containerEl.createEl("button", {
				text: "Run with page exemptions…",
				cls: "ocr-notice-button",
			});
			button.addEventListener("click", () => {
				notice.hide();
				new ExemptionModal(app, offer).open();
			});
		},
	};
}

/** A persistent progress notice with Hide and, unless null, Cancel. */
function openProgressNotice(message: string, onCancel: (() => void) | null): ProgressNotice {
	const notice = new Notice(message, 0);
	let hidden = false;
	const hide = () => {
		hidden = true;
		notice.hide();
	};
	// Obsidian hides a notice the user clicks. Its fade-out keeps it in the
	// DOM for a moment, so the click itself marks it hidden.
	notice.containerEl.addEventListener("click", () => {
		hidden = true;
	});
	const hideBtn = notice.containerEl.createEl("button", {
		text: "Hide",
		cls: "ocr-notice-button",
		attr: { "aria-label": "The run continues; the status bar shows its progress" },
	});
	hideBtn.addEventListener("click", (event) => {
		event.stopPropagation();
		hide();
	});
	if (onCancel !== null) {
		const cancelBtn = notice.containerEl.createEl("button", {
			text: "Cancel",
			cls: "ocr-notice-button",
		});
		cancelBtn.addEventListener("click", (event) => {
			// Keeps the notice open, so it shows the cancelling state.
			event.stopPropagation();
			cancelBtn.detach();
			onCancel();
		});
	}
	return {
		setMessage: (text) => {
			notice.setMessage(text);
		},
		hide,
		isShown: () => !hidden && notice.containerEl.isConnected,
	};
}

/**
 * `progressStatus` is the plugin's status-bar item: it shows the running
 * conversion and brings its notice back on click.
 */
export function createConversionHost(
	app: App,
	inventory: () => Inventory,
	settings: () => Settings,
	revealEntry: (entryName: string) => Promise<boolean>,
	progressStatus: HTMLElement,
): ConversionHost {
	let current: ProgressPresenter | null = null;
	progressStatus.addEventListener("click", () => current?.reopen());
	const surfaces: ProgressSurfaces = {
		openNotice: openProgressNotice,
		statusBar: {
			show(text) {
				// The inner span clips with an ellipsis; the flex item itself cannot.
				progressStatus.empty();
				progressStatus.createSpan({ text, cls: "ocr-progress-status-text" });
				progressStatus.show();
			},
			hide() {
				progressStatus.empty();
				progressStatus.hide();
				// The run is over; a click on the hidden item has nothing to reopen.
				current = null;
			},
		},
	};
	return {
		notify(message) {
			new Notice(message);
		},
		showProgress(message, onCancel) {
			current = new ProgressPresenter(surfaces, message, onCancel);
			return current;
		},
		vaultBasePath() {
			const adapter = app.vault.adapter;
			return adapter instanceof FileSystemAdapter ? adapter.getBasePath() : null;
		},
		pdfsWithSameBasename(pdf) {
			// Any convertible source, not just PDFs: `scan.png` and `scan.pdf`
			// both write `scan.md` and would overwrite each other (Issue #100).
			return app.vault
				.getFiles()
				.filter((f) => isConvertible(f) && f.basename === pdf.basename && f.path !== pdf.path)
				.map((f) => f.path);
		},
		previewSource(entryName, folder) {
			const item = inventory().entries.find(
				(entry) => entry.name === entryName && entry.file.parent?.path === folder,
			);
			if (item === undefined) return null;
			const recorded = item.entry["source-pdf"] ?? item.entry["manual-source-pdf"];
			if (recorded === null || recorded.length === 0) return item.file.path;
			// `quelle-pdf` holds the path pdf2md was called with, but a
			// hand-written `Source: [[…]]` link resolves the same way the
			// comparison view resolves it.
			const byPath = app.vault.getFileByPath(normalizePath(recorded));
			if (byPath instanceof TFile) return byPath.path;
			const dest = app.metadataCache.getFirstLinkpathDest(recorded, item.file.path);
			return dest?.path ?? item.file.path;
		},
		previewAtRisk: (entryName) => inventory().previewAtRisk(entryName),
		confirmReconvert: (message) =>
			confirmInModal(app, "Convert again?", message, "Convert again"),
		keepEditedCopy: (entryName) => inventory().keepEditedCopy(entryName),
		previewFolder() {
			const configured = settings().previewFolder;
			return { configured, normalized: normalizePath(configured) };
		},
		reconcile: () => inventory().reconcile(),
		hasPreviewEntry(entryName, folder) {
			return inventory().entries.some(
				(entry) => entry.name === entryName && entry.file.parent?.path === folder,
			);
		},
		openPreviewEntry: (entryName) => revealEntry(entryName),
		wait: (ms) => new Promise((done) => window.setTimeout(done, ms)),
	};
}
