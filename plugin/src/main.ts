// Plugin entry point: registration of view, commands, ribbon, file menu,
// and vault listeners that trigger reconciliation.

import { homedir } from "os";
import { join } from "path";
import { existsSync } from "fs";
import type { ChildProcess } from "child_process";
import {
	FileSystemAdapter,
	Menu,
	Notice,
	Plugin,
	TAbstractFile,
	TFile,
	normalizePath,
} from "obsidian";

import { VIEW_TYPE, OcrComparisonView, PdfSelectModal, PageSelectModal } from "./view.ts";
import { Inventory } from "./file-actions.ts";
import { Settings, SettingsTab, DEFAULT_SETTINGS } from "./settings.ts";
import { abortChild, convertPdf } from "./conversion.ts";

const RECONCILE_DEBOUNCE_MS = 500;
const CONVERSION_TIMEOUT_MS = 30 * 60 * 1000;
const INDEX_WAIT_STEPS = 10;
const INDEX_WAIT_MS = 200;

function resolvePdf2md(): string {
	const candidates = [join(homedir(), "bin", "pdf2md"), "/usr/local/bin/pdf2md"];
	for (const part of (process.env.PATH ?? "").split(":")) {
		if (part.length > 0) candidates.push(join(part, "pdf2md"));
	}
	for (const candidate of candidates) {
		if (existsSync(candidate)) return candidate;
	}
	return candidates[0]!;
}

export default class OcrPreviewPlugin extends Plugin {
	settings: Settings = { ...DEFAULT_SETTINGS };
	inventory!: Inventory;

	private reconcileTimer: number | null = null;
	private isConverting = false;
	private runningChild: ChildProcess | null = null;

	async onload(): Promise<void> {
		this.inventory = new Inventory(this.app, () => this.settings);
		await this.loadSettings();
		await this.inventory.load();

		this.registerView(VIEW_TYPE, (leaf) => new OcrComparisonView(leaf, this));

		this.app.workspace.onLayoutReady(async () => {
			await this.inventory.reconcile();
			const view = this.openView();
			if (view !== null) {
				view.update();
				if (view.activeName === null) {
					const first = view.firstVisible();
					if (first !== null) void view.openPreview(first);
				}
			}
		});

		const relevant = (file: TAbstractFile): boolean =>
			file instanceof TFile && file.extension === "md" && this.isPreviewFile(file);
		const trigger = () => this.triggerReconcile();

		this.registerEvent(
			this.app.vault.on("create", (file) => {
				if (relevant(file)) trigger();
			}),
		);
		this.registerEvent(
			this.app.vault.on("delete", (file) => {
				if (relevant(file)) trigger();
			}),
		);
		this.registerEvent(
			this.app.vault.on("rename", (file, oldPath) => {
				if (!(file instanceof TFile) || file.extension !== "md") return;
				if (!this.isPreviewPath(oldPath) && !this.isPreviewFile(file)) return;
				this.inventory.trackPathChange(file.path, oldPath);
				trigger();
			}),
		);
		this.registerEvent(
			this.app.vault.on("modify", (file) => {
				if (!(file instanceof TFile)) return;
				if (file.extension !== "md") return;
				if (!this.isPreviewFile(file)) return;
				trigger();
			}),
		);
		this.registerEvent(
			this.app.metadataCache.on("changed", (file) => {
				if (!(file instanceof TFile)) return;
				if (file.extension !== "md") return;
				if (!this.isPreviewFile(file)) return;
				trigger();
			}),
		);

		this.addRibbonIcon("columns-3", "Open OCR comparison", () => {
			void this.revealView();
		});

		this.addCommand({
			id: "abgleich-oeffnen",
			name: "Open OCR comparison",
			callback: () => void this.revealView(),
		});
		this.addCommand({
			id: "abgleich-weiter",
			name: "Jump to next preview entry",
			callback: () => this.openView()?.next(),
		});
		this.addCommand({
			id: "pdf-konvertieren-und-abgleich",
			name: "Convert PDF and open in OCR comparison",
			callback: () => void this.selectPdfAndConvert(),
		});

		this.registerEvent(
			this.app.workspace.on("file-menu", (menu, file) => {
				this.populateFileMenu(menu, file);
			}),
		);

		this.addSettingTab(new SettingsTab(this.app, this));
	}

	onunload(): void {
		if (this.reconcileTimer !== null) window.clearTimeout(this.reconcileTimer);
		if (this.runningChild !== null) abortChild(this.runningChild);
		this.runningChild = null;
		void this.inventory?.saveImmediately();
	}

	async loadSettings(): Promise<void> {
		const saved = (await this.loadData()) as Record<string, unknown> | null;
		if (saved) {
			const migrated: Partial<Settings> = {};
			const str = (k: string): string | undefined => {
				const val = saved[k];
				return typeof val === "string" ? val : undefined;
			};
			const previewFolder = str("previewFolder") ?? str("vorschauOrdner");
			if (previewFolder) migrated.previewFolder = previewFolder;

			const acceptedFolder = str("acceptedFolder") ?? str("akzeptiertOrdner");
			if (acceptedFolder) migrated.acceptedFolder = acceptedFolder;

			const rejectedFolder = str("rejectedFolder") ?? str("abgelehntOrdner");
			if (rejectedFolder) migrated.rejectedFolder = rejectedFolder;

			const statusFile = str("statusFile") ?? str("statusDatei");
			if (statusFile) migrated.statusFile = statusFile;

			if (saved["markdownView"]) migrated.markdownView = saved["markdownView"] as "rendered" | "source";
			else if (saved["markdownAnsicht"]) migrated.markdownView = saved["markdownAnsicht"] === "quelltext" ? "source" : "rendered";

			if (saved["columnWidths"]) migrated.columnWidths = saved["columnWidths"] as [number, number, number];
			else if (saved["spaltenbreiten"]) migrated.columnWidths = saved["spaltenbreiten"] as [number, number, number];

			if (typeof saved["pdfZoomMax"] === "number") migrated.pdfZoomMax = saved["pdfZoomMax"];
			if (typeof saved["syncActive"] === "boolean") migrated.syncActive = saved["syncActive"];
			else if (typeof saved["syncAktiv"] === "boolean") migrated.syncActive = saved["syncAktiv"];

			if (typeof saved["mdEagerLimit"] === "number") migrated.mdEagerLimit = saved["mdEagerLimit"];

			this.settings = { ...DEFAULT_SETTINGS, ...migrated };
		} else {
			this.settings = { ...DEFAULT_SETTINGS };
		}
	}

	async saveSettings(): Promise<void> {
		await this.saveData(this.settings);
	}

	triggerReconcile(): void {
		if (this.reconcileTimer !== null) window.clearTimeout(this.reconcileTimer);
		this.reconcileTimer = window.setTimeout(() => {
			this.reconcileTimer = null;
			void this.inventory.reconcile();
			this.openView()?.update();
		}, RECONCILE_DEBOUNCE_MS);
	}

	openView(): OcrComparisonView | null {
		const leaf = this.app.workspace.getLeavesOfType(VIEW_TYPE)[0];
		if (leaf === undefined) return null;
		return leaf.view instanceof OcrComparisonView ? leaf.view : null;
	}

	/** Opens the view tab, optionally with an entry. */
	async revealView(name?: string): Promise<void> {
		const { workspace } = this.app;
		let leaf = workspace.getLeavesOfType(VIEW_TYPE)[0];
		if (leaf === undefined) {
			leaf = workspace.getLeaf("tab");
			await leaf.setViewState({ type: VIEW_TYPE, active: true });
		}
		await workspace.revealLeaf(leaf);
		const view = leaf.view instanceof OcrComparisonView ? leaf.view : null;
		if (view === null) return;
		if (name !== undefined) await view.openPreview(name);
		else if (view.activeName === null) {
			const first = view.firstVisible();
			if (first !== null) await view.openPreview(first);
		}
	}

	async selectPdfAndConvert(): Promise<void> {
		if (!this.checkConversionFree()) return;
		const modal = new PdfSelectModal(
			this.app,
			this.app.vault.getFiles().filter((f) => f.extension === "pdf"),
		);
		modal.setPlaceholder("Search PDF for conversion…");
		modal.onSelection = (file) => {
			const pageModal = new PageSelectModal(this.app, file);
			pageModal.onSelection = (pages) =>
				void this.convert(file, pages);
			pageModal.open();
		};
		modal.open();
	}

	private checkConversionFree(): boolean {
		if (this.isConverting) {
			new Notice("OCR Preview: A conversion is already running.");
			return false;
		}
		return true;
	}

	private async convert(file: TFile, pages?: string): Promise<void> {
		if (!this.checkConversionFree()) return;
		this.isConverting = true;
		const name = file.basename;
		let progressNotice: Notice | null = null;
		try {
			const duplicateStems = this.app.vault
				.getFiles()
				.filter(
					(f) => f.extension === "pdf" && f.basename === name && f.path !== file.path,
				);
			if (duplicateStems.length > 0) {
				new Notice(
					`OCR Preview: "${name}" also exists as ${duplicateStems
						.map((f) => f.path)
						.join(", ")} — output would overwrite. Please rename one of the files.`,
				);
				return;
			}
			const adapter = this.app.vault.adapter;
			if (!(adapter instanceof FileSystemAdapter)) {
				new Notice("OCR Preview: Conversion requires file system access (Desktop).");
				return;
			}
			const base = adapter.getBasePath();
			const pdfRel = file.path;
			const outRel = normalizePath(this.settings.previewFolder);
			progressNotice = new Notice(`OCR Preview: Converting "${name}" …`, 0);
			let cancelled = false;
			const cancelBtn = progressNotice.containerEl.createEl("button", {
				text: "Cancel",
				cls: "ocr-notice-abbrechen",
			});
			cancelBtn.addEventListener("click", () => {
				if (cancelled) return;
				cancelled = true;
				cancelBtn.detach();
				progressNotice?.setMessage(`OCR Preview: "${name}" is being cancelled …`);
				if (this.runningChild !== null) abortChild(this.runningChild);
			});
			const result = await convertPdf(
				pdfRel,
				outRel,
				resolvePdf2md(),
				base,
				undefined,
				{
					timeoutMs: CONVERSION_TIMEOUT_MS,
					...(pages && pages.length > 0 ? { pages } : {}),
					onChild: (child) => {
						this.runningChild = child;
					},
					onProgress: (e) => {
						if (e.type !== "page" || cancelled) return;
						if (progressNotice) {
							const txt = e.derailed
								? `— page ${e.num} of ${e.total} (derailed)`
								: `— page ${e.num} of ${e.total}`;
							progressNotice.setMessage(
								`OCR Preview: Converting "${name}" ${txt} …`
							);
						}
					},
				},
			);
			this.runningChild = null;
			progressNotice.hide();
			progressNotice = null;
			if (result.code !== 0) {
				const stderrLast = result.stderrLast;
				const stdoutLast = result.stdoutLast.filter((z) => !z.startsWith("→"));
				const detail =
					(stderrLast.length > 0
						? stderrLast[stderrLast.length - 1]
						: undefined) ??
					(stdoutLast.length > 0 ? stdoutLast[stdoutLast.length - 1] : undefined) ??
					"";
				let codeText: string;
				if (result.code === 6) {
					codeText = "cancelled — partial file created (incomplete)";
				} else if (result.code === 7) {
					codeText = "cancelled — before first page (no partial file)";
				} else if (result.signal === "SIGKILL") {
					codeText = "cancelled — force terminated after grace period (SIGKILL)";
				} else if (result.timeout) {
					codeText = `cancelled after ${CONVERSION_TIMEOUT_MS / 60000} min`;
				} else if (result.code === null && result.signal !== null) {
					codeText = `cancelled (Signal ${result.signal})`;
				} else if (result.code === null) {
					codeText = "Start error";
				} else {
					codeText = `Code ${result.code}`;
				}
				let extra = detail.length > 0 ? ` — ${detail}` : "";
				if (result.code === null && /ENOENT/.test(detail)) {
					extra = " — pdf2md not found. Please run setup.sh in repo.";
				}
				new Notice(`OCR Preview: Conversion failed (${codeText})${extra}.`);
				return;
			}
			const entryName = `${name}.md`;
			const isPresent = () => this.inventory.entries.some((b) => b.name === entryName);
			await this.inventory.reconcile();
			for (let step = 0; !isPresent() && step < INDEX_WAIT_STEPS; step++) {
				await new Promise((done) => window.setTimeout(done, INDEX_WAIT_MS));
				await this.inventory.reconcile();
			}
			if (isPresent()) {
				await this.revealView(entryName);
				new Notice(`OCR Preview: "${name}" finished — comparison opened.`);
			} else {
				new Notice(
					`OCR Preview: "${name}" finished, but was not placed in preview folder (Target: ${this.settings.previewFolder}).`,
				);
			}
		} catch (err) {
			new Notice(`OCR Preview: Conversion failed — ${String(err)}.`);
		} finally {
			progressNotice?.hide();
			this.isConverting = false;
			this.runningChild = null;
		}
	}

	private populateFileMenu(menu: Menu, file: TAbstractFile): void {
		if (!(file instanceof TFile)) return;
		if (file.extension === "md" && this.isPreviewFile(file)) {
			menu.addItem((i) =>
				i
					.setTitle("Open in OCR comparison")
					.setIcon("columns-3")
					.onClick(() => void this.revealView(file.name)),
			);
		} else if (file.extension === "pdf") {
			const stem = `${file.basename}.md`;
			if (this.inventory.entries.some((b) => b.name === stem)) {
				menu.addItem((i) =>
					i
						.setTitle("Open in OCR comparison")
						.setIcon("columns-3")
						.onClick(() => void this.revealView(stem)),
				);
			}
		}
	}

	private isPreviewPath(path: string): boolean {
		const s = this.settings;
		const parent = path.slice(0, path.lastIndexOf("/"));
		return (
			parent === s.previewFolder ||
			parent === s.acceptedFolder ||
			parent === s.rejectedFolder
		);
	}

	private isPreviewFile(file: TFile): boolean {
		return this.isPreviewPath(file.path);
	}
}
