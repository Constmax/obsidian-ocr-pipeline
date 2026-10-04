import type { TFile } from "obsidian";

import type { PreviewAtRisk } from "./conversion-controller.ts";
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

/** A decision `decide` executed; its notice hands it back to `undo`. */
export interface Decision {
	name: string;
	location: FolderLocation;
	fromPath: string;
	toPath: string;
	/** `decided` the manifest recorded, to tell whether the decision is still current. */
	decided: string | null;
	previousEntry: StatusEntry;
}

/** Failure results of `decide`. `collision` is the only one where the caller offers an action. */
export type DecisionResult = "unchanged" | "unknown" | "collision" | "error";

/**
 * What `Inventory` needs from the vault. The Obsidian implementation is
 * `vaultFiles()` (`vault-files.ts`); tests pass an in-memory fake.
 */
export interface InventoryFiles {
	normalize(path: string): string;
	notify(message: string, timeoutMs?: number): void;
	markdownFiles(): TFile[];
	frontmatter(file: TFile): Record<string, unknown>;
	fileAt(path: string): TFile | null;
	folderExists(path: string): boolean;
	createFolder(path: string): Promise<void>;
	/** Moves a file and updates the links to it across the vault. */
	move(file: TFile, newPath: string): Promise<void>;
	copy(file: TFile, newPath: string): Promise<void>;
	/** The status file goes through the adapter: it may sit in a hidden folder. */
	adapter: {
		exists(path: string): Promise<boolean>;
		read(path: string): Promise<string>;
		write(path: string, text: string): Promise<void>;
		remove(path: string): Promise<void>;
		rename(from: string, to: string): Promise<void>;
		mkdir(path: string): Promise<void>;
	};
}

/** Holds the manifest, reconciles with the three folders, and executes decisions. */
export class Inventory {
	manifest: StatusManifest = emptyManifest(new Date().toISOString());
	entries: InventoryEntry[] = [];

	private writeChain: Promise<void> = Promise.resolve();
	private writeTimer: number | null = null;
	/**
	 * False until the vault index is complete (layout-ready). Before that,
	 * reconcile would drop the rows of files not indexed yet, with their notes
	 * and review state, so it does nothing.
	 */
	private indexed = false;

	private readonly files: InventoryFiles;
	private readonly settings: () => Settings;

	constructor(files: InventoryFiles, settings: () => Settings) {
		this.files = files;
		this.settings = settings;
	}

	private get s(): Settings {
		return this.settings();
	}

	/** Is the path directly in one of the three folders? */
	private inThreeFolders(path: string): boolean {
		const s = this.s;
		const idx = path.lastIndexOf("/");
		const parent = idx > 0 ? path.slice(0, idx) : "";
		return (
			parent === this.files.normalize(s.previewFolder) ||
			parent === this.files.normalize(s.acceptedFolder) ||
			parent === this.files.normalize(s.rejectedFolder)
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
			["open", this.files.normalize(s.previewFolder)],
			["accepted", this.files.normalize(s.acceptedFolder)],
			["rejected", this.files.normalize(s.rejectedFolder)],
		];
		const found: FoundFile[] = [];
		for (const file of this.files.markdownFiles()) {
			const parent = file.parent?.path ?? "";
			for (const [loc, folder] of locations) {
				if (parent !== folder) continue;
				found.push({
					name: file.name,
					path: file.path,
					location: loc,
					frontmatter: this.files.frontmatter(file),
				});
				break;
			}
		}
		return found;
	}

	/** Reads the manifest only; the first reconcile is `start()`. */
	async load(): Promise<void> {
		const now = new Date().toISOString();
		const path = this.files.normalize(this.s.statusFile);
		let previous = emptyManifest(now);
		if (await this.files.adapter.exists(path)) {
			try {
				previous = readManifest(await this.files.adapter.read(path), now);
			} catch (err) {
				console.error("OCR Preview: review-status.json is unreadable", err);
				const corrupted = `${path}.corrupted`;
				try {
					if (await this.files.adapter.exists(corrupted)) {
						await this.files.adapter.remove(corrupted);
					}
					await this.files.adapter.rename(path, corrupted);
					this.files.notify(
						`OCR Preview: review-status.json was unreadable and saved as ${corrupted}. Status rebuilt from folders.`,
						8000,
					);
				} catch (renameErr) {
					console.error("OCR Preview: Rename failed", renameErr);
				}
			}
		}
		this.manifest = previous;
	}

	/** Layout-ready: the vault index is complete; runs the first reconcile. */
	async start(): Promise<void> {
		this.indexed = true;
		await this.reconcile();
	}

	async reconcile(previous: StatusManifest = this.manifest): Promise<void> {
		if (!this.indexed) return;
		const now = new Date().toISOString();
		const files = this.collectFiles();
		const result = reconcile(files, previous, now, (p) =>
			this.files.fileAt(this.files.normalize(p)) !== null,
		);
		this.manifest = result.manifest;

		const byName = new Map<string, TFile>();
		for (const found of files) {
			const file = this.files.fileAt(found.path);
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
		const path = this.files.normalize(this.s.statusFile);
		const idx = path.lastIndexOf("/");
		const folder = idx > 0 ? path.slice(0, idx) : "";
		try {
			if (folder.length > 0 && !(await this.files.adapter.exists(folder))) {
				await this.files.adapter.mkdir(folder);
			}
			await this.files.adapter.write(path, writeManifest(this.manifest));
		} catch (err) {
			console.error("OCR Preview: review-status.json not writable", err);
			this.files.notify("OCR Preview: Status could not be saved.");
		}
	}

	private async ensureFolder(path: string): Promise<void> {
		if (this.files.folderExists(path)) return;
		try {
			await this.files.createFolder(path);
		} catch (err) {
			if (!this.files.folderExists(path)) throw err;
		}
	}

	async decide(name: string, location: FolderLocation): Promise<Decision | DecisionResult> {
		const inv = this.entries.find((b) => b.name === name);
		if (inv === undefined) return "unknown";
		const target = this.files.normalize(targetFolder(location, this.s));
		const newPath = this.files.normalize(`${target}/${inv.file.name}`);
		if (newPath === inv.file.path) return "unchanged";
		if (this.files.fileAt(newPath) !== null) return "collision";

		const previousEntry = { ...inv.entry };
		const fromPath = inv.file.path;
		try {
			await this.ensureFolder(target);
			await this.files.move(inv.file, newPath);
		} catch (err) {
			console.error("OCR Preview: Move failed", err);
			this.files.notify(`OCR Preview: "${name}" could not be moved.`);
			return "error";
		}

		this.manifest = recordDecision(
			this.manifest,
			name,
			location,
			newPath,
			new Date().toISOString(),
		);
		const decided = this.manifest.entries[name]?.decided ?? null;
		await this.reconcile();
		return { name, location, fromPath, toPath: newPath, decided, previousEntry };
	}

	/**
	 * Reverts `decision` while it is still current: the file is where the
	 * decision put it and the entry records that decision. So the Undo of an
	 * older notice reverts its own decision, never the latest one.
	 */
	async undo(last: Decision): Promise<"undone" | "not-current" | "failed"> {
		const entry = this.manifest.entries[last.name];
		const file = this.files.fileAt(last.toPath);
		if (
			file === null ||
			entry === undefined ||
			entry.path !== last.toPath ||
			entry.status !== last.location ||
			entry.decided !== last.decided
		) {
			return "not-current";
		}
		try {
			const folder = last.fromPath.slice(0, last.fromPath.lastIndexOf("/"));
			await this.ensureFolder(folder);
			await this.files.move(file, last.fromPath);
		} catch (err) {
			console.error("OCR Preview: Undo failed", err);
			return "failed";
		}
		this.manifest = {
			...this.manifest,
			entries: {
				...this.manifest.entries,
				// Only what the decision changed: a note added meanwhile stays.
				[last.name]: {
					...entry,
					status: last.previousEntry.status,
					path: last.previousEntry.path,
					decided: last.previousEntry.decided,
					previous: last.previousEntry.previous,
				},
			},
		};
		await this.reconcile();
		return "undone";
	}

	async updateEntry(
		name: string,
		change: Partial<
			Pick<
				StatusEntry,
				"note" | "checked-until" | "manual-source-pdf" | "manually-edited"
			>
		>,
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
		const accepted = this.files.normalize(s.acceptedFolder);
		const rejected = this.files.normalize(s.rejectedFolder);
		const file = this.files
			.markdownFiles()
			.find(
				(f) =>
					f.name === name &&
					(f.parent?.path === accepted || f.parent?.path === rejected),
			);
		if (file === undefined) return false;
		const oldDate = oldDateFrom(this.manifest.entries[name], this.files.frontmatter(file));
		const target = this.freeRejectedPath(`${file.basename}-${oldDate}`);
		try {
			await this.ensureFolder(rejected);
			await this.files.move(file, target);
		} catch (err) {
			console.error("OCR Preview: Old version could not be renamed", err);
			this.files.notify(`OCR Preview: "${name}" could not be replaced.`);
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

	/**
	 * Before a re-conversion overwrites an edited preview: copies the preview
	 * folder's file into the rejected folder, where it shows as a rejected
	 * entry of its own. False when no such file exists or the copy failed.
	 */
	/**
	 * What converting `name` again would destroy: manual edits in the preview
	 * folder's file (what pdf2md overwrites), or a decision. Edits to a decided
	 * file elsewhere stay where they are.
	 */
	previewAtRisk(name: string): PreviewAtRisk {
		const item = this.entries.find((entry) => entry.name === name);
		if (item === undefined) return null;
		const folder = this.files.normalize(this.s.previewFolder);
		if (item.file.parent?.path === folder && item.entry["manually-edited"]) return "edited";
		const status = item.entry.status;
		return status === "accepted" || status === "rejected" ? status : null;
	}

	async keepEditedCopy(name: string): Promise<boolean> {
		const folder = this.files.normalize(this.s.previewFolder);
		const file = this.files.fileAt(this.files.normalize(`${folder}/${name}`));
		if (file === null) return false;
		const oldDate = oldDateFrom(this.manifest.entries[name], this.files.frontmatter(file));
		const target = this.freeRejectedPath(`${file.basename}-edited-${oldDate}`);
		try {
			await this.ensureFolder(this.files.normalize(this.s.rejectedFolder));
			await this.files.copy(file, target);
		} catch (err) {
			console.error("OCR Preview: Edited preview could not be copied", err);
			return false;
		}
		return true;
	}

	/** `<rejected>/<stem>.md`, or with `-2`, `-3`, … appended until no file has the path. */
	private freeRejectedPath(stem: string): string {
		const rejected = this.files.normalize(this.s.rejectedFolder);
		let target = this.files.normalize(`${rejected}/${stem}.md`);
		for (let attempt = 2; this.files.fileAt(target) !== null; attempt++) {
			target = this.files.normalize(`${rejected}/${stem}-${attempt}.md`);
		}
		return target;
	}
}
