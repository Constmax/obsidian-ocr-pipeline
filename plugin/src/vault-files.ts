// Obsidian side of the Inventory: the vault, its metadata cache and adapter.

import { Notice, normalizePath, type App } from "obsidian";

import type { InventoryFiles } from "./file-actions.ts";

export function vaultFiles(app: App): InventoryFiles {
	const { vault } = app;
	return {
		normalize: normalizePath,
		notify(message, timeoutMs) {
			new Notice(message, timeoutMs);
		},
		markdownFiles: () => vault.getMarkdownFiles(),
		frontmatter: (file) => app.metadataCache.getFileCache(file)?.frontmatter ?? {},
		fileAt: (path) => vault.getFileByPath(path),
		folderExists: (path) => vault.getFolderByPath(path) !== null,
		async createFolder(path) {
			await vault.createFolder(path);
		},
		// `renameFile`, not `vault.rename`: it updates link paths across the
		// vault, so diagram embeds keep working.
		move: (file, newPath) => app.fileManager.renameFile(file, newPath),
		async copy(file, newPath) {
			await vault.copy(file, newPath);
		},
		adapter: vault.adapter,
	};
}
