// Plugin entry point: registration of view, commands, ribbon, file menu,
// and vault listeners that trigger reconciliation. PDF conversion is
// delegated to the ConversionController.

import { Menu, Notice, Plugin, TAbstractFile, TFile } from "obsidian";

import { VIEW_TYPE, OcrComparisonView, PdfSelectModal, PageSelectModal } from "./view.ts";
import { Inventory } from "./file-actions.ts";
import { Settings, SettingsTab, DEFAULT_SETTINGS } from "./settings.ts";
import { ConversionController } from "./conversion-controller.ts";
import { createConversionHost } from "./conversion-host.ts";

const RECONCILE_DEBOUNCE_MS = 500;

export default class OcrPreviewPlugin extends Plugin {
	settings: Settings = { ...DEFAULT_SETTINGS };
	inventory!: Inventory;
	conversion!: ConversionController;

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
			this.app.vault.getFiles().filter((f) => f.extension === "pdf"),
		);
		modal.setPlaceholder("Search PDF for conversion…");
		modal.onSelection = (file) => this.selectPagesAndConvert(file);
		modal.open();
	}

	private selectPagesAndConvert(file: TFile): void {
		if (!this.conversion.ensureIdle()) return;
		const pageModal = new PageSelectModal(this.app, file);
		pageModal.onSelection = (pages) => void this.conversion.run(file, pages);
		pageModal.open();
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
			menu.addItem((i) =>
				i
					.setTitle("OCR → Markdown")
					.onClick(() => this.selectPagesAndConvert(file)),
			);
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
