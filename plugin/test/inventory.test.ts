import { test } from "node:test";
import assert from "node:assert/strict";

import type { TFile } from "obsidian";

// Inventory debounces its manifest writes with window.setTimeout.
Object.defineProperty(globalThis, "window", {
	configurable: true,
	value: {
		setTimeout: (fn: () => void, ms?: number) => setTimeout(fn, ms),
		clearTimeout: (id?: NodeJS.Timeout) => clearTimeout(id),
	},
});

const { Inventory } = await import("../src/file-actions.ts");
type InventoryFiles = import("../src/file-actions.ts").InventoryFiles;
type Decision = import("../src/file-actions.ts").Decision;
type Settings = import("../src/settings.ts").Settings;
const { writeManifest, emptyManifest } = await import("../src/status.ts");

const SETTINGS = {
	previewFolder: "_ocr-preview",
	acceptedFolder: "_ocr-preview/_accepted",
	rejectedFolder: "_ocr-preview/_rejected",
	statusFile: "_ocr-preview/review-status.json",
} as Settings;

function fakeFile(path: string): TFile {
	const slash = path.lastIndexOf("/");
	const name = path.slice(slash + 1);
	return standIn<TFile>({
		path,
		name,
		basename: name.replace(/\.md$/, ""),
		extension: "md",
		parent: { path: path.slice(0, slash) },
	});
}

/** A plain object in place of an Obsidian class that tests cannot construct. */
function standIn<T>(value: unknown): T {
	return value as T;
}

/** The vault in memory: indexed markdown files, folders, and the adapter's raw files. */
class FakeVault implements InventoryFiles {
	files = new Map<string, { frontmatter: Record<string, unknown> }>();
	folders = new Set<string>(["_ocr-preview"]);
	raw = new Map<string, string>();
	notices: string[] = [];

	add(path: string, frontmatter: Record<string, unknown> = {}): void {
		this.files.set(path, { frontmatter });
	}
	normalize(path: string): string {
		return path.replace(/\/+$/, "");
	}
	notify(message: string): void {
		this.notices.push(message);
	}
	markdownFiles(): TFile[] {
		return [...this.files.keys()].map(fakeFile);
	}
	frontmatter(file: TFile): Record<string, unknown> {
		return this.files.get(file.path)?.frontmatter ?? {};
	}
	fileAt(path: string): TFile | null {
		return this.files.has(path) ? fakeFile(path) : null;
	}
	folderExists(path: string): boolean {
		return this.folders.has(path);
	}
	async createFolder(path: string): Promise<void> {
		this.folders.add(path);
	}
	async move(file: TFile, newPath: string): Promise<void> {
		const entry = this.files.get(file.path)!;
		this.files.delete(file.path);
		this.files.set(newPath, entry);
	}
	async copy(file: TFile, newPath: string): Promise<void> {
		if (this.files.has(newPath)) throw new Error("exists");
		this.files.set(newPath, { ...this.files.get(file.path)! });
	}
	adapter = {
		exists: async (path: string) => this.raw.has(path),
		read: async (path: string) => this.raw.get(path)!,
		write: async (path: string, text: string) => {
			this.raw.set(path, text);
		},
		remove: async (path: string) => {
			this.raw.delete(path);
		},
		rename: async (from: string, to: string) => {
			this.raw.set(to, this.raw.get(from)!);
			this.raw.delete(from);
		},
		mkdir: async () => undefined,
	};
}

function setup(vault = new FakeVault()) {
	return { vault, inventory: new Inventory(vault, () => SETTINGS) };
}

test("load only reads the manifest: a row whose file is not indexed yet keeps its note", async () => {
	const vault = new FakeVault();
	vault.add("_ocr-preview/a.md");
	const { inventory } = setup(vault);
	await inventory.load();
	await inventory.start();
	await inventory.updateEntry("a.md", { note: "check p. 3", "manually-edited": true });
	vault.raw.set(SETTINGS.statusFile, writeManifest(inventory.manifest));

	// Obsidian start: the vault index does not show the file yet.
	const early = new FakeVault();
	early.raw = vault.raw;
	const restarted = new Inventory(early, () => SETTINGS);
	await restarted.load();
	assert.equal(restarted.manifest.entries["a.md"]?.note, "check p. 3");

	// A reconcile before layout-ready (a vault event, a view) changes nothing.
	await restarted.reconcile();
	assert.equal(restarted.manifest.entries["a.md"]?.note, "check p. 3");
	assert.deepEqual(restarted.entries, []);

	// Layout-ready: the file is indexed, the first reconcile keeps the row.
	early.add("_ocr-preview/a.md");
	await restarted.start();
	assert.equal(restarted.manifest.entries["a.md"]?.note, "check p. 3");
	assert.equal(restarted.manifest.entries["a.md"]?.["manually-edited"], true);
});

test("load of a missing manifest starts empty", async () => {
	const { inventory } = setup();
	await inventory.load();
	assert.deepEqual(inventory.manifest.entries, emptyManifest("").entries);
});

async function decided(inventory: InstanceType<typeof Inventory>, name: string): Promise<Decision> {
	const result = await inventory.decide(name, "accepted");
	assert.equal(typeof result, "object");
	return result as Decision;
}

test("undo on an older notice reverts that decision, not the latest one", async () => {
	const vault = new FakeVault();
	vault.add("_ocr-preview/x.md");
	vault.add("_ocr-preview/y.md");
	const { inventory } = setup(vault);
	await inventory.start();

	const x = await decided(inventory, "x.md");
	const y = await decided(inventory, "y.md");
	assert.equal(await inventory.undo(x), "undone");

	assert.ok(vault.files.has("_ocr-preview/x.md"));
	assert.ok(vault.files.has("_ocr-preview/_accepted/y.md"));
	assert.equal(inventory.manifest.entries["x.md"]?.status, "open");
	assert.equal(inventory.manifest.entries["y.md"]?.status, "accepted");

	assert.equal(await inventory.undo(y), "undone");
	assert.equal(inventory.manifest.entries["y.md"]?.status, "open");
});

test("undo of a decision that is no longer current changes nothing", async () => {
	const vault = new FakeVault();
	vault.add("_ocr-preview/x.md");
	const { inventory } = setup(vault);
	await inventory.start();

	const accept = await decided(inventory, "x.md");
	const reject = await inventory.decide("x.md", "rejected");
	assert.equal(typeof reject, "object");

	assert.equal(await inventory.undo(accept), "not-current");
	assert.ok(vault.files.has("_ocr-preview/_rejected/x.md"));
	assert.equal(inventory.manifest.entries["x.md"]?.status, "rejected");

	// Undone once, the same notice cannot undo again.
	assert.equal(await inventory.undo(reject as Decision), "undone");
	assert.equal(await inventory.undo(reject as Decision), "not-current");
});

test("keepEditedCopy copies the preview into the rejected folder under a free name", async () => {
	const vault = new FakeVault();
	vault.add("_ocr-preview/a.md", { "ocr-date": "2026-10-01" });
	vault.add("_ocr-preview/_rejected/a-edited-2026-10-01.md");
	const { inventory } = setup(vault);
	await inventory.start();

	assert.equal(await inventory.keepEditedCopy("a.md"), true);
	assert.ok(vault.files.has("_ocr-preview/a.md"));
	assert.ok(vault.files.has("_ocr-preview/_rejected/a-edited-2026-10-01-2.md"));
});

test("keepEditedCopy without a file in the preview folder reports failure", async () => {
	const { inventory } = setup();
	assert.equal(await inventory.keepEditedCopy("missing.md"), false);
});

test("undo keeps a note added after the decision", async () => {
	const vault = new FakeVault();
	vault.add("_ocr-preview/x.md");
	const { inventory } = setup(vault);
	await inventory.start();

	const accept = await decided(inventory, "x.md");
	await inventory.updateEntry("x.md", { note: "added while the notice showed" });
	assert.equal(await inventory.undo(accept), "undone");
	assert.equal(inventory.manifest.entries["x.md"]?.status, "open");
	assert.equal(inventory.manifest.entries["x.md"]?.note, "added while the notice showed");
});

test("previewAtRisk: edits in the preview folder, a decision, or nothing", async () => {
	const vault = new FakeVault();
	vault.add("_ocr-preview/edited.md");
	vault.add("_ocr-preview/plain.md");
	vault.add("_ocr-preview/done.md");
	const { inventory } = setup(vault);
	await inventory.start();
	await inventory.updateEntry("edited.md", { "manually-edited": true });
	await decided(inventory, "done.md");
	// Edits to a decided file are not what pdf2md overwrites.
	await inventory.updateEntry("done.md", { "manually-edited": true });

	assert.equal(inventory.previewAtRisk("edited.md"), "edited");
	assert.equal(inventory.previewAtRisk("plain.md"), null);
	assert.equal(inventory.previewAtRisk("done.md"), "accepted");
	assert.equal(inventory.previewAtRisk("missing.md"), null);
});
