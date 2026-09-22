// Obsidian side of the ConversionController and the searchable-copy action:
// Notices, vault paths, the inventory, the comparison view, and opening files.

import { FileSystemAdapter, Notice, Platform, TFile, normalizePath, type App } from "obsidian";

import type { ConversionHost } from "./conversion-controller.ts";
import { isConvertible } from "./input-formats.ts";
import type { Inventory } from "./file-actions.ts";
import { ExemptionModal } from "./exemption-modal.ts";
import type { SearchableCopyHost } from "./searchable-copy.ts";
import type { Settings } from "./settings.ts";

export function createSearchableCopyHost(app: App, settings: () => Settings): SearchableCopyHost {
	return {
		isDesktop: Platform.isDesktopApp,
		notify(message) {
			new Notice(message);
		},
		// The adapter checks the disk, so an unindexed or hidden file counts too.
		exists: (path) => app.vault.adapter.exists(normalizePath(path)),
		settings: () => settings(),
		async openPdf(path) {
			const file = app.vault.getFileByPath(normalizePath(path));
			if (file === null) return false;
			await app.workspace.getLeaf("tab").openFile(file);
			return true;
		},
		wait: (ms) => new Promise((done) => window.setTimeout(done, ms)),
		offerExemptions(offer) {
			// Stays until the user acts on it or clicks it away.
			const notice = new Notice(offer.message, 0);
			const button = notice.containerEl.createEl("button", {
				text: "Run with page exemptions…",
				cls: "ocr-notice-abbrechen",
			});
			button.addEventListener("click", () => {
				notice.hide();
				new ExemptionModal(app, offer).open();
			});
		},
	};
}

export function createConversionHost(
	app: App,
	inventory: () => Inventory,
	settings: () => Settings,
	revealEntry: (entryName: string) => Promise<boolean>,
): ConversionHost {
	return {
		notify(message) {
			new Notice(message);
		},
		showProgress(message, onCancel) {
			const notice = new Notice(message, 0);
			const cancelBtn = notice.containerEl.createEl("button", {
				text: "Cancel",
				cls: "ocr-notice-abbrechen",
			});
			cancelBtn.addEventListener("click", () => {
				cancelBtn.detach();
				onCancel();
			});
			return {
				setMessage: (text) => {
					notice.setMessage(text);
				},
				hide: () => notice.hide(),
			};
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
