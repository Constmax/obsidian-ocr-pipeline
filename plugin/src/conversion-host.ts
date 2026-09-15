// Obsidian side of the ConversionController and the searchable-copy action:
// Notices, vault paths, the inventory, the comparison view, and opening files.

import { FileSystemAdapter, Notice, Platform, normalizePath, type App } from "obsidian";

import type { ConversionHost } from "./conversion-controller.ts";
import type { Inventory } from "./file-actions.ts";
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
			return app.vault
				.getFiles()
				.filter((f) => f.extension === "pdf" && f.basename === pdf.basename && f.path !== pdf.path)
				.map((f) => f.path);
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
