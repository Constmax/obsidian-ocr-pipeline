// Plugin entry point: registration of view, commands, ribbon, file menu,
// and vault listeners that trigger reconciliation. PDF conversion is
// delegated to the ConversionController.

import { Menu, Notice, Plugin, TAbstractFile, TFile, normalizePath } from "obsidian";

import { VIEW_TYPE, OcrComparisonView, PdfSelectModal, PageSelectModal } from "./view.ts";
import { Inventory } from "./file-actions.ts";
import { Settings, SettingsTab, DEFAULT_SETTINGS } from "./settings.ts";
import { parseOcrSettings } from "./ocr-settings.ts";
import { LEGACY_FOLDERS, LEGACY_PLUGIN_ID, legacyStart } from "./legacy-install.ts";
import { ConversionController } from "./conversion-controller.ts";
import { isConvertible } from "./input-formats.ts";
import { createConversionHost, createSearchableCopyHost } from "./conversion-host.ts";
import { runSearchableCopy, type SearchableCopyHost } from "./searchable-copy.ts";

const RECONCILE_DEBOUNCE_MS = 500;

export default class OcrPreviewPlugin extends Plugin {
	settings: Settings = { ...DEFAULT_SETTINGS };
	inventory!: Inventory;
	conversion!: ConversionController;
	private searchableCopyHost!: SearchableCopyHost;

	private reconcileTimer: number | null = null;

	async onload(): Promise<void> {
		this.inventory = new Inventory(this.app, () => this.settings);
		this.conversion = new ConversionController(
			createConversionHost(
				this.app,
				() => this.inventory,
				() => this.settings,
				(entryName) => this.revealView(entryName),
			),
		);
		this.searchableCopyHost = createSearchableCopyHost(this.app, () => this.settings);
		await this.loadSettings();
		await this.inventory.load();

		this.registerView(VIEW_TYPE, (leaf) => new OcrComparisonView(leaf, this));

		this.app.workspace.onLayoutReady(async () => {
			await this.inventory.reconcile();
			for (const view of this.openViews()) view.update();
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
			callback: () => void this.jumpToNextPreview(),
		});
		this.addCommand({
			id: "pdf-konvertieren-und-abgleich",
			name: "Convert PDF and open in OCR comparison",
			callback: () => void this.selectPdfAndConvert(),
		});
		this.addCommand({
			id: "create-searchable-copy",
			name: "Create searchable copy (OCR)",
			callback: () => this.selectPdfForSearchableCopy(),
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
		this.conversion?.dispose();
		void this.inventory?.saveImmediately();
	}

	async loadSettings(): Promise<void> {
		let saved = (await this.loadData()) as Record<string, unknown> | null;
		// A fresh install may follow the pre-rename plugin (Issue #104): start
		// from its data or its folders instead of hiding existing previews.
		// The adapter checks the disk directly — the vault index is not
		// complete this early in onload.
		let carriedOver = false;
		if (!saved) {
			const adapter = this.app.vault.adapter;
			saved = legacyStart(await this.readLegacyData(), {
				legacyFolder: await adapter.exists(LEGACY_FOLDERS.previewFolder),
				currentFolder: await adapter.exists(DEFAULT_SETTINGS.previewFolder),
			});
			carriedOver = saved !== null;
		}
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

			const operationMode = str("operationMode") ?? str("bedienmodus");
			if (operationMode) {
				migrated.operationMode =
					operationMode === "workbench" || operationMode === "werkbank"
						? "workbench"
						: "review-flow";
			}

			if (saved["columnWidths"]) migrated.columnWidths = saved["columnWidths"] as [number, number, number];
			else if (saved["spaltenbreiten"]) migrated.columnWidths = saved["spaltenbreiten"] as [number, number, number];

			if (typeof saved["pdfZoomMax"] === "number") migrated.pdfZoomMax = saved["pdfZoomMax"];
			if (typeof saved["syncActive"] === "boolean") migrated.syncActive = saved["syncActive"];
			else if (typeof saved["syncAktiv"] === "boolean") migrated.syncActive = saved["syncAktiv"];

			if (typeof saved["mdEagerLimit"] === "number") migrated.mdEagerLimit = saved["mdEagerLimit"];

			// OCR engine and column split: data from before these settings and
			// invalid values (e.g. an engine this version does not offer) fall
			// back to the defaults field by field.
			Object.assign(migrated, parseOcrSettings(saved));

			this.settings = { ...DEFAULT_SETTINGS, ...migrated };
		} else {
			this.settings = { ...DEFAULT_SETTINGS };
		}
		if (carriedOver) {
			await this.saveSettings();
			new Notice(
				`OCR Preview: Settings taken over from the former install (${LEGACY_PLUGIN_ID}) — previews stay in ${this.settings.previewFolder}.`,
			);
		}
	}

	/** data.json of the pre-rename plugin id, or null if absent or unreadable. */
	private async readLegacyData(): Promise<unknown> {
		const path = normalizePath(
			`${this.app.vault.configDir}/plugins/${LEGACY_PLUGIN_ID}/data.json`,
		);
		try {
			return JSON.parse(await this.app.vault.adapter.read(path)) as unknown;
		} catch {
			return null;
		}
	}

	async saveSettings(): Promise<void> {
		await this.saveData(this.settings);
	}

	triggerReconcile(): void {
		if (this.reconcileTimer !== null) window.clearTimeout(this.reconcileTimer);
		this.reconcileTimer = window.setTimeout(() => {
			this.reconcileTimer = null;
			void this.inventory.reconcile().then(() => {
				for (const view of this.openViews()) view.update();
			});
		}, RECONCILE_DEBOUNCE_MS);
	}

	/**
	 * All comparison views that are actually instantiated. Leaves in the
	 * background are deferred (Obsidian 1.7.2+) and carry a DeferredView instead
	 * of ours — those are skipped, they rebuild themselves in onOpen().
	 */
	openViews(): OcrComparisonView[] {
		const views: OcrComparisonView[] = [];
		for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE)) {
			if (leaf.view instanceof OcrComparisonView) views.push(leaf.view);
		}
		return views;
	}

	openView(): OcrComparisonView | null {
		return this.openViews()[0] ?? null;
	}

	/** Opens the view tab, optionally with an entry. */
	async revealView(name?: string): Promise<boolean> {
		const { workspace } = this.app;
		let leaf = workspace.getLeavesOfType(VIEW_TYPE)[0];
		if (leaf === undefined) {
			leaf = workspace.getLeaf("tab");
			await leaf.setViewState({ type: VIEW_TYPE, active: true });
		}
		await workspace.revealLeaf(leaf);
		// A restored tab is deferred until it is loaded; without this its view is
		// a DeferredView and the entry below would silently never be opened.
		await leaf.loadIfDeferred();
		const view = leaf.view instanceof OcrComparisonView ? leaf.view : null;
		if (view === null) return false;
		if (name !== undefined) return await view.openPreview(name);
		if (view.activeName === null && !view.hasPendingPreview()) {
			const first = view.firstVisible();
			if (first !== null) return await view.openPreview(first);
		}
		return true;
	}

	private async jumpToNextPreview(): Promise<void> {
		try {
			await this.revealView();
			const view = this.openView();
			if (view === null) return;
			await view.waitForPreview();
			view.next();
		} catch (err) {
			console.error("OCR Preview: Could not open next preview", err);
			new Notice("OCR Preview: The next preview could not be opened.");
		}
	}

	async selectPdfAndConvert(): Promise<void> {
		if (!this.conversion.ensureIdle()) return;
		const modal = new PdfSelectModal(
			this.app,
			this.app.vault.getFiles().filter(isConvertible),
			"No PDFs or images in vault.",
		);
		modal.setPlaceholder("Search PDF or image for conversion…");
		modal.onSelection = (file) => this.selectPagesAndConvert(file);
		modal.open();
	}

	private selectPagesAndConvert(file: TFile): void {
		if (!this.conversion.ensureIdle()) return;
		const pageModal = new PageSelectModal(this.app, file);
		pageModal.onSelection = (pages) => void this.conversion.run(file, pages);
		pageModal.open();
	}

	/** Stage 1 for exactly this PDF: writes `<stem>-ocr.pdf` beside it. */
	createSearchableCopy(file: TFile): void {
		void runSearchableCopy(
			{ path: file.path, basename: file.basename },
			this.conversion,
			this.searchableCopyHost,
		);
	}

	private selectPdfForSearchableCopy(): void {
		if (!this.conversion.ensureIdle()) return;
		// Stage 1 only: `bin/pdf-auto` and the column split assume PDF input,
		// so this list stays PDF-only while conversion accepts images too
		// (Issue #100).
		const modal = new PdfSelectModal(
			this.app,
			this.app.vault.getFiles().filter((f) => f.extension === "pdf"),
		);
		modal.setPlaceholder("Search PDF for a searchable copy…");
		modal.onSelection = (file) => this.createSearchableCopy(file);
		modal.open();
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
		} else if (isConvertible(file)) {
			menu.addItem((i) =>
				i
					.setTitle("OCR → Markdown")
					.onClick(() => this.selectPagesAndConvert(file)),
			);
			// Stage 1 is PDF-only (Issue #100), unlike the conversion above.
			if (file.extension === "pdf") {
				menu.addItem((i) =>
					i
						.setTitle("Create searchable copy (OCR)")
						.setIcon("scan-text")
						.onClick(() => this.createSearchableCopy(file)),
				);
			}
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
