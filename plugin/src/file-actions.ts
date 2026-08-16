import { App, Notice, TFile, normalizePath } from "obsidian";

import type { Settings } from "./settings.ts";
import {
	emptyManifest,
	oldDateFrom,
	readManifest,
	reconcile,
	recordDecision,
	targetFolder,
	writeManifest,
} from "./status.ts";
import type {
	FolderLocation,
	FoundFile,
	StatusEntry,
	StatusManifest,
} from "./types.ts";

export interface InventoryEntry {
	name: string;
	file: TFile;
	entry: StatusEntry;
}

interface LastAction {
	name: string;
	fromPath: string;
	toPath: string;
	previousEntry: StatusEntry;
}

/** Result of `decide`. `collision` is the only value where caller offers an action. */
export type DecisionResult =
	| "ok"
	| "unchanged"
	| "unknown"
	| "collision"
	| "error";

/**
 * Holds the manifest, reconciles with the three folders, and executes decisions.
 *
 * File moves use `fileManager.renameFile` exclusively — not `vault.rename`.
 * `renameFile` updates link paths across the vault, preserving diagram embeds.
 */
export class Inventory {
	manifest: StatusManifest = emptyManifest(new Date().toISOString());
	entries: InventoryEntry[] = [];

	private writeChain: Promise<void> = Promise.resolve();
	private writeTimer: number | null = null;
	private lastAction: LastAction | null = null;

	constructor(
		private app: App,
		private settings: () => Settings,
	) {}

	private get s(): Settings {
		return this.settings();
	}

	/** Is the path directly in one of the three folders? */
	private inThreeFolders(path: string): boolean {
		const s = this.s;
		const idx = path.lastIndexOf("/");
		const parent = idx > 0 ? path.slice(0, idx) : "";
		return (
			parent === normalizePath(s.previewFolder) ||
			parent === normalizePath(s.acceptedFolder) ||
			parent === normalizePath(s.rejectedFolder)
		);
	}

	/**
	 * Tracks path when a file is moved OUT of the three folders (adoption into wiki).
	 */
	trackPathChange(newPath: string, oldPath: string): void {
		if (this.inThreeFolders(newPath)) return;
		if (!this.inThreeFolders(oldPath)) return;
		const name = oldPath.slice(oldPath.lastIndexOf("/") + 1);
		const oldEntry = this.manifest.entries[name];
		if (oldEntry === undefined || oldEntry.path !== oldPath) return;
		this.manifest = {
			...this.manifest,
			entries: {
				...this.manifest.entries,
				[name]: { ...oldEntry, path: newPath },
			},
		};
	}

	/** Collects .md files from the three folders. */
	private collectFiles(): FoundFile[] {
		const s = this.s;
		const locations: Array<[FolderLocation, string]> = [
			["open", normalizePath(s.previewFolder)],
			["accepted", normalizePath(s.acceptedFolder)],
			["rejected", normalizePath(s.rejectedFolder)],
		];
		const found: FoundFile[] = [];
		for (const file of this.app.vault.getMarkdownFiles()) {
			const parent = file.parent?.path ?? "";
			for (const [loc, folder] of locations) {
				if (parent !== folder) continue;
				const cache = this.app.metadataCache.getFileCache(file);
				found.push({
					name: file.name,
					path: file.path,
					location: loc,
					frontmatter: cache?.frontmatter ?? {},
				});
				break;
			}
		}
		return found;
	}

	async load(): Promise<void> {
		const now = new Date().toISOString();
		const path = normalizePath(this.s.statusFile);
		let previous = emptyManifest(now);
		if (await this.app.vault.adapter.exists(path)) {
			try {
				previous = readManifest(await this.app.vault.adapter.read(path), now);
			} catch (err) {
				console.error("OCR Preview: review-status.json is unreadable", err);
				const corrupted = `${path}.corrupted`;
				try {
					if (await this.app.vault.adapter.exists(corrupted)) {
						await this.app.vault.adapter.remove(corrupted);
					}
					await this.app.vault.adapter.rename(path, corrupted);
					new Notice(
						`OCR Preview: review-status.json was unreadable and saved as ${corrupted}. Status rebuilt from folders.`,
						8000,
					);
				} catch (renameErr) {
					console.error("OCR Preview: Rename failed", renameErr);
				}
			}
		}
		await this.reconcile(previous);
	}

	async reconcile(previous: StatusManifest = this.manifest): Promise<void> {
		const now = new Date().toISOString();
		const files = this.collectFiles();
		const result = reconcile(files, previous, now, (p) =>
			this.app.vault.getFileByPath(normalizePath(p)) !== null,
		);
		this.manifest = result.manifest;

		const byName = new Map<string, TFile>();
		for (const found of files) {
			const file = this.app.vault.getFileByPath(found.path);
			if (file === null) continue;
			if (found.location === "open" || !byName.has(found.name)) {
				byName.set(found.name, file);
			}
		}
		this.entries = [];
		for (const [name, entry] of Object.entries(this.manifest.entries)) {
			const file = byName.get(name);
			if (file === undefined) continue;
			this.entries.push({ name, file, entry });
		}
		this.entries.sort((a, b) => a.name.localeCompare(b.name, "en"));

		if (result.corrected.length > 0) {
			console.warn(
				"OCR Preview: Status inferred from folder location for",
				result.corrected.join(", "),
			);
		}
		this.triggerSave();
	}

	triggerSave(): void {
		if (this.writeTimer !== null) window.clearTimeout(this.writeTimer);
		this.writeTimer = window.setTimeout(() => {
			this.writeTimer = null;
			this.writeChain = this.writeChain.then(() => this.save());
		}, 500);
	}

	async saveImmediately(): Promise<void> {
		if (this.writeTimer !== null) {
			window.clearTimeout(this.writeTimer);
			this.writeTimer = null;
		}
		this.writeChain = this.writeChain.then(() => this.save());
		await this.writeChain;
	}

	private async save(): Promise<void> {
		const path = normalizePath(this.s.statusFile);
		const idx = path.lastIndexOf("/");
		const folder = idx > 0 ? path.slice(0, idx) : "";
		try {
			if (folder.length > 0 && !(await this.app.vault.adapter.exists(folder))) {
				await this.app.vault.adapter.mkdir(folder);
			}
			await this.app.vault.adapter.write(path, writeManifest(this.manifest));
		} catch (err) {
			console.error("OCR Preview: review-status.json not writable", err);
			new Notice("OCR Preview: Status could not be saved.");
		}
	}

	private async ensureFolder(path: string): Promise<void> {
		if (this.app.vault.getFolderByPath(path) !== null) return;
		try {
			await this.app.vault.createFolder(path);
		} catch (err) {
			if (this.app.vault.getFolderByPath(path) === null) throw err;
		}
	}

	async decide(name: string, location: FolderLocation): Promise<DecisionResult> {
		const inv = this.entries.find((b) => b.name === name);
		if (inv === undefined) return "unknown";
		const target = normalizePath(targetFolder(location, this.s));
		const newPath = normalizePath(`${target}/${inv.file.name}`);
		if (newPath === inv.file.path) return "unchanged";
		if (this.app.vault.getFileByPath(newPath) !== null) return "collision";

		const previousEntry = { ...inv.entry };
		const fromPath = inv.file.path;
		try {
			await this.ensureFolder(target);
			await this.app.fileManager.renameFile(inv.file, newPath);
		} catch (err) {
			console.error("OCR Preview: Move failed", err);
			new Notice(`OCR Preview: "${name}" could not be moved.`);
			return "error";
		}

		this.manifest = recordDecision(
			this.manifest,
			name,
			location,
			newPath,
			new Date().toISOString(),
		);
		this.lastAction = { name, fromPath, toPath: newPath, previousEntry };
		await this.reconcile();
		return "ok";
	}

	async undo(): Promise<string | null> {
		const last = this.lastAction;
		if (last === null) return null;
		const file = this.app.vault.getFileByPath(last.toPath);
		if (file === null) {
			this.lastAction = null;
			return null;
		}
		try {
			const folder = last.fromPath.slice(0, last.fromPath.lastIndexOf("/"));
			await this.ensureFolder(folder);
			await this.app.fileManager.renameFile(file, last.fromPath);
		} catch (err) {
			console.error("OCR Preview: Undo failed", err);
			return null;
		}
		this.manifest = {
			...this.manifest,
			entries: {
				...this.manifest.entries,
				[last.name]: last.previousEntry,
			},
		};
		this.lastAction = null;
		await this.reconcile();
		return last.name;
	}

	async updateEntry(
		name: string,
		change: Partial<Pick<StatusEntry, "note" | "checked-until" | "manual-source-pdf">>,
	): Promise<void> {
		const oldEntry = this.manifest.entries[name];
		if (oldEntry === undefined) return;
		this.manifest = {
			...this.manifest,
			entries: { ...this.manifest.entries, [name]: { ...oldEntry, ...change } },
		};
		const inv = this.entries.find((b) => b.name === name);
		if (inv !== undefined) {
			inv.entry = this.manifest.entries[name] as StatusEntry;
		}
		this.triggerSave();
	}

	async replaceOldVersion(name: string): Promise<boolean> {
		const s = this.s;
		const accepted = normalizePath(s.acceptedFolder);
		const rejected = normalizePath(s.rejectedFolder);
		const file = this.app.vault
			.getMarkdownFiles()
			.find(
				(f) =>
					f.name === name &&
					(f.parent?.path === accepted || f.parent?.path === rejected),
			);
		if (file === undefined) return false;
		const cache = this.app.metadataCache.getFileCache(file);
		const oldDate = oldDateFrom(
			this.manifest.entries[name],
			cache?.frontmatter ?? {},
		);
		let target = normalizePath(`${rejected}/${file.basename}-${oldDate}.md`);
		let attempt = 2;
		while (this.app.vault.getFileByPath(target) !== null) {
			target = normalizePath(
				`${rejected}/${file.basename}-${oldDate}-${attempt}.md`,
			);
			attempt++;
		}
		try {
			await this.ensureFolder(rejected);
			await this.app.fileManager.renameFile(file, target);
		} catch (err) {
			console.error("OCR Preview: Old version could not be renamed", err);
			new Notice(`OCR Preview: "${name}" could not be replaced.`);
			return false;
		}
		this.manifest = {
			...this.manifest,
			entries: {
				...this.manifest.entries,
				[name]: {
					...(this.manifest.entries[name] as StatusEntry),
					status: "open",
					decided: null,
				},
			},
		};
		await this.reconcile();
		return true;
	}
}
