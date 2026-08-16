// The three-column view: Previews | Original PDF | Markdown.

import {
	App,
	ItemView,
	Menu,
	Modal,
	Notice,
	Setting,
	SuggestModal,
	TFile,
	ViewStateResult,
	WorkspaceLeaf,
	loadPdfJs,
	normalizePath,
	setIcon,
} from "obsidian";

import type OcrPreviewPlugin from "./main.ts";
import { Inventory, type InventoryEntry } from "./file-actions.ts";
import { MarkdownColumn, type Representation } from "./md-pane.ts";
import { PdfColumn } from "./pdf-pane.ts";
import { Sidebar } from "./sidebar.ts";
import { Coupling } from "./sync.ts";
import type { FolderLocation, Preview } from "./types.ts";
import { parsePreview, buildPreview } from "./preview-parser.ts";

export const VIEW_TYPE = "ocr-preview-comparison";

const COLUMN_MIN = 10;
const CHECKED_UNTIL_MS = 800;

export class OcrComparisonView extends ItemView {
	activeName: string | null = null;

	private get inventory(): Inventory {
		return this.plugin.inventory;
	}
	private sidebar!: Sidebar;
	private pdfColumn!: PdfColumn;
	private mdColumn!: MarkdownColumn;
	private coupling!: Coupling;
	private frame!: HTMLElement;
	private pdfHeaderTitle!: HTMLElement;
	private pdfPagesDisplay!: HTMLElement;
	private pdfErrorBanner!: HTMLElement;
	private zoomDisplay!: HTMLElement;
	private representationToggle!: HTMLElement;
	private pdfFile: TFile | null = null;
	private pdfPages = 0;
	private openRun = 0;
	private checkedUntilTimer: number | null = null;
	private mdSaveTimer: number | null = null;
	private mdWriteChain: Promise<void> = Promise.resolve();
	private editButton!: HTMLButtonElement;

	constructor(
		leaf: WorkspaceLeaf,
		private plugin: OcrPreviewPlugin,
	) {
		super(leaf);
		this.navigation = true;
	}

	getViewType(): string {
		return VIEW_TYPE;
	}

	getDisplayText(): string {
		return "OCR Comparison";
	}

	getIcon(): string {
		return "columns-3";
	}

	private uiBuilt = false;

	private ensureUiBuilt(): void {
		if (this.uiBuilt) return;
		this.uiBuilt = true;

		this.frame = this.contentEl.createDiv({ cls: "ocr-abgleich" });

		// ── Left: Preview list ──────────────────────────────────────────────
		const listCol = this.frame.createDiv({ cls: "ocr-spalte ocr-spalte-liste" });
		this.sidebar = new Sidebar(
			listCol,
			() => this.plugin.settings.previewFolder,
		);
		this.sidebar.onSelect = (name) => void this.openPreview(name);
		this.sidebar.onRefresh = () => void this.reconcile();
		this.sidebar.onSettings = () => openSettings(this.plugin);

		// ── Center: Original PDF ─────────────────────────────────────────────
		const handle1 = this.frame.createDiv({
			cls: "ocr-griff",
			attr: { "aria-label": "Adjust column width", title: "Adjust column width" },
		});
		const pdfCol = this.frame.createDiv({ cls: "ocr-spalte ocr-spalte-pdf" });
		const pdfHeader = pdfCol.createDiv({ cls: "ocr-spaltenkopf" });
		const pdfRow = pdfHeader.createDiv({ cls: "ocr-kopf-zeile" });
		this.pdfHeaderTitle = pdfRow.createSpan({ cls: "ocr-kopf-titel", text: "Original PDF" });
		this.pdfPagesDisplay = pdfRow.createSpan({ cls: "ocr-kopf-seiten" });
		const pdfTools = pdfHeader.createDiv({ cls: "ocr-kopf-werkzeuge" });
		this.smallButton(pdfTools, "−", "Zoom out", () => this.zoom(-0.25));
		this.zoomDisplay = pdfTools.createSpan({ cls: "ocr-kopf-zoom", text: "100 %" });
		this.smallButton(pdfTools, "+", "Zoom in", () => this.zoom(0.25));
		const inViewer = this.smallButton(pdfTools, "Open in PDF viewer", "", () => {
			void this.openInPdfViewer();
		});
		inViewer.addClass("ocr-knopf-haupt");
		this.pdfErrorBanner = pdfHeader.createDiv({ cls: "ocr-fehlbanner" });
		this.pdfErrorBanner.hide();

		this.pdfColumn = new PdfColumn(this.app, pdfCol, () => this.plugin.settings.pdfZoomMax);
		this.pdfColumn.onMeasurementNeeded = () => this.coupling.remeasure();
		this.pdfColumn.onLoaded = (name, pages) => this.pdfLoaded(name, pages);
		this.pdfColumn.onError = (error) => this.showPdfError(error);

		// ── Right: Markdown ──────────────────────────────────────────────────
		const handle2 = this.frame.createDiv({
			cls: "ocr-griff",
			attr: { "aria-label": "Adjust column width", title: "Adjust column width" },
		});
		const mdCol = this.frame.createDiv({ cls: "ocr-spalte ocr-spalte-md" });
		const mdHeader = mdCol.createDiv({ cls: "ocr-spaltenkopf" });
		const mdRow = mdHeader.createDiv({ cls: "ocr-kopf-zeile" });
		mdRow.createSpan({ cls: "ocr-kopf-titel", text: "Markdown" });
		this.representationToggle = mdRow.createDiv({ cls: "ocr-umschalter" });
		const renderedBtn = this.representationToggle.createEl("button", {
			cls: "ocr-umschalter-option",
			text: "Rendered",
		});
		renderedBtn.dataset["darstellung"] = "rendered";
		const sourceBtn = this.representationToggle.createEl("button", {
			cls: "ocr-umschalter-option",
			text: "Source",
		});
		sourceBtn.dataset["darstellung"] = "source";
		renderedBtn.addEventListener("click", () => this.setRepresentation("rendered"));
		sourceBtn.addEventListener("click", () => this.setRepresentation("source"));
		const mdTools = mdRow.createDiv({ cls: "ocr-kopf-werkzeuge" });
		const acceptBtn = this.smallButton(mdTools, "Accept", "a — accept as correct", () => {
			void this.decide("accepted");
		});
		acceptBtn.addClass("ocr-knopf-annehmen");
		const rejectBtn = this.smallButton(mdTools, "Reject", "x — for revision", () => {
			void this.decide("rejected");
		});
		rejectBtn.addClass("ocr-knopf-ablehnen");
		this.smallButton(mdTools, "Open in Obsidian", "", () => void this.openInObsidian());
		this.editButton = mdTools.createEl("button", {
			cls: "ocr-ikonknopf",
		});
		setIcon(this.editButton.createSpan({ cls: "ocr-ikon" }), "pencil");
		this.editButton.addEventListener("click", () => this.toggleEdit());
		this.updateEditButton();
		const moreBtn = mdTools.createEl("button", {
			cls: "ocr-ikonknopf",
			attr: { "aria-label": "More", title: "More" },
		});
		setIcon(moreBtn.createSpan({ cls: "ocr-ikon" }), "horizontal-three-dots");
		moreBtn.addEventListener("click", (e) => this.moreMenu(e));

		this.mdColumn = new MarkdownColumn(
			this.app,
			mdCol,
			this,
			() => this.plugin.settings.mdEagerLimit,
		);
		this.mdColumn.onMeasurementNeeded = () => this.coupling.remeasure();
		this.mdColumn.onChange = () => this.triggerSaveChange();
		this.mdColumn.onFocusLost = () => void this.saveChangeImmediately();
		this.highlightToggle(this.plugin.settings.markdownView);

		// ── Coupling and Widths ──────────────────────────────────────────────
		this.coupling = new Coupling({
			pdf: { scrollEl: this.pdfColumn.scrollEl, elements: () => this.pdfColumn.elements() },
			md: { scrollEl: this.mdColumn.scrollEl, elements: () => this.mdColumn.elements() },
		});
		this.coupling.onPage = (p) => this.showPage(p);

		this.applySettings();
		this.wireHandle(handle1, 0, 1);
		this.wireHandle(handle2, 1, 2);

		this.registerHotkeys();
	}

	async onOpen(): Promise<void> {
		this.ensureUiBuilt();
		this.update();
		if (this.activeName === null) {
			const first = this.firstVisible();
			if (first !== null) void this.openPreview(first);
		}
	}

	async onClose(): Promise<void> {
		await this.saveChangeImmediately();
		this.coupling?.destroy();
		this.pdfColumn?.destroy();
		this.mdColumn?.clear();
		if (this.checkedUntilTimer !== null) window.clearTimeout(this.checkedUntilTimer);
	}

	getState(): Record<string, unknown> {
		return { datei: this.activeName };
	}

	async setState(state: unknown, result: ViewStateResult): Promise<void> {
		result.history = false;
		const file =
			(state as { datei?: unknown; file?: unknown } | null)?.file ??
			(state as { datei?: unknown } | null)?.datei;
		if (typeof file === "string") {
			await this.openPreview(file);
		} else if (this.activeName === null) {
			const first = this.firstVisible();
			if (first !== null) await this.openPreview(first);
		}
	}

	next(): void {
		const name = this.activeName;
		if (name === null) {
			const first = this.sidebar.moveSelection(1);
			if (first !== null) void this.openPreview(first);
			return;
		}
		const nextItem = this.sidebar.nextAfter(name);
		if (nextItem !== null) void this.openPreview(nextItem);
	}

	firstVisible(): string | null {
		return this.sidebar?.firstVisible() ?? null;
	}

	private async reconcile(): Promise<void> {
		await this.inventory?.reconcile();
		this.update();
	}

	update(): void {
		this.ensureUiBuilt();
		const folderMissing =
			this.app.vault.getFolderByPath(this.plugin.settings.previewFolder) === null;
		const entries = this.inventory?.entries ?? [];
		this.sidebar?.update(entries, folderMissing);
		if (
			this.activeName !== null &&
			!entries.some((b) => b.name === this.activeName)
		) {
			this.noSelection();
		}
	}

	async openPreview(name: string): Promise<void> {
		if (typeof name !== "string" || name.length === 0) return;
		this.ensureUiBuilt();
		await this.saveChangeImmediately();
		const run = ++this.openRun;
		const item = this.inventory?.entries.find((b) => b.name === name);
		if (item === undefined) {
			new Notice(`"${name}" is no longer in preview list.`);
			this.update();
			return;
		}
		this.activeName = name;
		this.sidebar.setSelected(name);
		this.app.workspace.requestSaveLayout();

		let preview: Preview | null = null;
		try {
			const text = await this.app.vault.read(item.file);
			if (run !== this.openRun) return;
			preview = parsePreview(text);
		} catch (err) {
			console.error("OCR Preview: File readable error", err);
			new Notice(`"${name}" could not be read.`);
		}
		if (run !== this.openRun) return;

		if (preview !== null) {
			await this.mdColumn.open(
				item.file,
				preview,
				this.plugin.settings.markdownView,
			);
		} else {
			this.mdColumn.clear("The file could not be read.");
		}
		if (run !== this.openRun) return;

		const pdfFile = await this.findOriginal(item, preview);
		if (run !== this.openRun) return;
		this.pdfFile = pdfFile;
		if (pdfFile === null) {
			await this.pdfColumn.open(null);
			this.showPdfError("Original PDF not found.");
		} else {
			this.showPdfError(null);
			await this.pdfColumn.open(pdfFile);
		}

		this.coupling.remeasure();
		const until = item.entry["checked-until"];
		if (until !== null && until > 1) this.coupling.goToPage(until);
	}

	private async findOriginal(
		item: InventoryEntry,
		preview: Preview | null,
	): Promise<TFile | null> {
		const file = item.file;
		const candidates: Array<string | null> = [];
		if (preview !== null) candidates.push(preview.sourcePdf);
		const link = this.app.metadataCache.getFileCache(file)?.links?.[0]?.link;
		if (link !== undefined && link.length > 0) candidates.push(link);
		candidates.push(
			this.app.vault.getFiles().find(
				(f) => f.extension === "pdf" && f.basename === file.basename,
			)?.path ?? null,
		);
		candidates.push(item.entry["manual-source-pdf"]);

		for (const candidate of candidates) {
			if (candidate === null || candidate.length === 0) continue;
			const byPath = this.app.vault.getFileByPath(normalizePath(candidate));
			if (byPath instanceof TFile) return byPath;
			const dest = this.app.metadataCache.getFirstLinkpathDest(candidate, file.path);
			if (dest !== null) return dest;
		}
		return null;
	}

	private async decide(location: FolderLocation): Promise<void> {
		await this.saveChangeImmediately();
		const name = this.sidebar.selectedName();
		if (name === null) return;
		const result = await this.inventory.decide(name, location);
		if (result === "collision") {
			this.reportCollision(name, location);
			return;
		}
		if (result !== "ok") return;

		const label =
			location === "accepted" ? "Accepted" : location === "rejected" ? "Rejected" : "Reset";
		const notice = new Notice(`${label}: ${name.replace(/\.md$/, "")}`, 6000);
		const undoBtn = notice.messageEl.createEl("button", {
			cls: "ocr-knopf ocr-knopf-klein",
			text: "Undo",
		});
		undoBtn.addEventListener("click", () => void this.undo());

		this.update();
		if (location === "open") return;
		const nextItem = this.sidebar.nextAfter(name);
		if (nextItem !== null) void this.openPreview(nextItem);
		else this.noSelection();
	}

	private reportCollision(name: string, location: FolderLocation): void {
		const stem = name.replace(/\.md$/, "");
		const notice = new Notice(
			`"${stem}" already exists in target folder — likely the previous version.`,
			10000,
		);
		const btn = notice.messageEl.createEl("button", {
			cls: "ocr-knopf ocr-knopf-klein",
			text: "Replace old version and continue",
		});
		btn.addEventListener("click", () => {
			notice.hide();
			void this.replaceAndDecide(name, location);
		});
	}

	private async replaceAndDecide(name: string, location: FolderLocation): Promise<void> {
		if (!(await this.inventory.replaceOldVersion(name))) {
			new Notice(`OCR Preview: "${name}" could not be replaced.`);
			this.update();
			return;
		}
		await this.decide(location);
	}

	private async undo(): Promise<void> {
		const name = await this.inventory.undo();
		this.update();
		if (name !== null) void this.openPreview(name);
	}

	private noSelection(): void {
		this.activeName = null;
		this.sidebar.setSelected(null);
		this.mdColumn.clear("No preview selected — select an entry on the left.");
		void this.pdfColumn.open(null);
		this.pdfLoaded(null, 0);
		this.app.workspace.requestSaveLayout();
	}

	private pdfLoaded(name: string | null, pages: number): void {
		this.pdfPages = pages;
		this.pdfHeaderTitle.setText(name === null ? "Original PDF" : name.replace(/\.pdf$/, ""));
		this.pdfPagesDisplay.setText(pages > 0 ? `p. 1 / ${pages}` : "");
	}

	private showPdfError(error: string | null): void {
		this.pdfErrorBanner.empty();
		if (error === null) {
			this.pdfErrorBanner.hide();
			return;
		}
		this.pdfErrorBanner.show();
		this.pdfErrorBanner.createSpan({ text: error });
		const assignBtn = this.pdfErrorBanner.createEl("button", {
			cls: "ocr-knopf ocr-knopf-klein",
			text: "Assign PDF…",
		});
		assignBtn.addEventListener("click", () => this.assignPdf());
	}

	private showPage(p: number): void {
		if (this.pdfFile === null || this.pdfPages === 0) return;
		const nr = Math.min(Math.max(Math.floor(p), 1), this.pdfPages);
		this.pdfPagesDisplay.setText(`p. ${nr} / ${this.pdfPages}`);
		this.updateCheckedUntil(nr);
	}

	private updateCheckedUntil(nr: number): void {
		const name = this.activeName;
		if (name === null) return;
		const item = this.inventory.entries.find((b) => b.name === name);
		if (item === undefined) return;
		const current = item.entry["checked-until"] ?? 0;
		if (nr <= current) return;
		if (this.checkedUntilTimer !== null) window.clearTimeout(this.checkedUntilTimer);
		this.checkedUntilTimer = window.setTimeout(() => {
			this.checkedUntilTimer = null;
			void this.inventory.updateEntry(name, { "checked-until": nr });
		}, CHECKED_UNTIL_MS);
	}

	private triggerSaveChange(): void {
		if (this.mdSaveTimer !== null) window.clearTimeout(this.mdSaveTimer);
		this.mdSaveTimer = window.setTimeout(() => {
			this.mdSaveTimer = null;
			void this.saveChangeImmediately();
		}, 500);
	}

	private async saveChangeImmediately(): Promise<void> {
		if (this.mdSaveTimer !== null) {
			window.clearTimeout(this.mdSaveTimer);
			this.mdSaveTimer = null;
		}
		const preview = this.mdColumn?.currentPreview();
		const file = this.mdColumn?.currentFile();
		if (preview === null || preview === undefined || file === null || file === undefined) return;
		const text = buildPreview(preview);
		this.mdWriteChain = this.mdWriteChain.then(async () => {
			try {
				await this.app.vault.modify(file, text);
			} catch (err) {
				console.error("OCR Preview: Save converted text failed", err);
				new Notice("OCR Preview: Changes could not be saved to file.");
			}
		});
		await this.mdWriteChain;
	}

	private setRepresentation(representation: Representation): void {
		void this.saveChangeImmediately();
		this.mdColumn.setRepresentation(representation);
		this.highlightToggle(representation);
	}

	private highlightToggle(representation: Representation): void {
		const options =
			this.representationToggle.querySelectorAll<HTMLElement>(".ocr-umschalter-option");
		for (const opt of options) {
			opt.toggleClass(
				"ocr-umschalter-aktiv",
				opt.dataset["darstellung"] === representation,
			);
		}
	}

	private toggleEdit(): void {
		const newStatus = !this.mdColumn.isEditable();
		this.mdColumn.setEditable(newStatus);
		if (newStatus && this.mdColumn.currentRepresentation() === "rendered") {
			this.setRepresentation("source");
		}
		if (!newStatus) {
			void this.saveChangeImmediately();
		}
		this.updateEditButton();
	}

	private updateEditButton(): void {
		if (this.editButton === undefined) return;
		const active = this.mdColumn.isEditable();
		this.editButton.toggleClass("ocr-ikonknopf-aktiv", active);
		const label = active ? "Disable edit mode (e)" : "Edit (e)";
		this.editButton.setAttribute("aria-label", label);
		this.editButton.setAttribute("title", label);
	}

	private zoom(step: number): void {
		this.pdfColumn.zoom(step);
		this.zoomDisplay.setText(`${Math.round(this.pdfColumn.currentZoom() * 100)} %`);
	}

	private async openInPdfViewer(): Promise<void> {
		if (this.pdfFile === null) return;
		await this.app.workspace.openLinkText(this.pdfFile.path, "");
	}

	private async openInObsidian(): Promise<void> {
		if (this.activeName === null) return;
		const item = this.inventory.entries.find((b) => b.name === this.activeName);
		if (item === undefined) return;
		await this.app.workspace.openLinkText(item.file.path, "");
	}

	private moreMenu(e: MouseEvent): void {
		const name = this.activeName;
		if (name === null) return;
		const item = this.inventory.entries.find((b) => b.name === name);
		if (item === undefined) return;
		const menu = new Menu();
		menu.addItem((i) =>
			i
				.setTitle("Note…")
				.setIcon("sticky-note")
				.onClick(() => this.noteDialog(item)),
		);
		if (item.entry.status === "re-created") {
			menu.addItem((i) =>
				i
					.setTitle("Replace old version")
					.setIcon("archive")
					.onClick(() => {
						void this.inventory.replaceOldVersion(name).then(() => {
							this.update();
						});
					}),
			);
		}
		menu.addItem((i) =>
			i
				.setTitle("Reset status")
				.setIcon("rotate-ccw")
				.onClick(() => void this.decide("open")),
		);
		menu.addItem((i) =>
			i
				.setTitle("Copy path")
				.setIcon("clipboard-copy")
				.onClick(() => {
					navigator.clipboard
						.writeText(item.file.path)
						.then(() => new Notice("Path copied."))
						.catch(() => new Notice("Copy failed."));
				}),
		);
		menu.showAtMouseEvent(e);
	}

	private assignPdf(): void {
		const name = this.activeName;
		if (name === null) return;
		const modal = new PdfSelectModal(
			this.app,
			this.app.vault.getFiles().filter((f) => f.extension === "pdf"),
		);
		modal.setPlaceholder("Search original PDF…");
		modal.onSelection = (file) => {
			void this.inventory.updateEntry(name, { "manual-source-pdf": file.path });
			void this.openPreview(name);
		};
		modal.open();
	}

	private noteDialog(item: InventoryEntry): void {
		const modal = new NoteModal(this.app, item.entry.note ?? "");
		modal.onSaved = (text) => {
			const cleaned = text.trim();
			void this.inventory.updateEntry(item.name, {
				note: cleaned.length > 0 ? cleaned : null,
			});
		};
		modal.open();
	}

	private registerHotkeys(): void {
		const scope = this.scope;
		if (scope === null) return;
		const key = (k: string, fn: () => void) =>
			scope.register([], k, (evt) => {
				if (isInputTarget(evt.target)) return true;
				fn();
				return false;
			});

		key("j", () => {
			const name = this.sidebar.moveSelection(1);
			if (name !== null) void this.openPreview(name);
		});
		key("k", () => {
			const name = this.sidebar.moveSelection(-1);
			if (name !== null) void this.openPreview(name);
		});
		key("a", () => void this.decide("accepted"));
		key("x", () => void this.decide("rejected"));
		key("t", () =>
			this.setRepresentation(
				this.mdColumn.currentRepresentation() === "rendered" ? "source" : "rendered",
			),
		);
		key("e", () => this.toggleEdit());
		key(" ", () => this.nextPage());
		key("g", () => this.goToPage());
		key("s", () => this.toggleSync());
		key("Escape", () => this.noSelection());
	}

	private nextPage(): void {
		const el = this.pdfColumn.scrollEl;
		el.scrollBy({ top: Math.max(el.clientHeight - 60, 1) });
	}

	private toggleSync(): void {
		this.coupling.active = !this.coupling.active;
		this.plugin.settings.syncActive = this.coupling.active;
		void this.plugin.saveSettings();
		new Notice(`Scroll sync ${this.coupling.active ? "on" : "off"}.`);
	}

	private goToPage(): void {
		const modal = new Modal(this.app);
		modal.titleEl.setText("Go to page");
		const input: HTMLInputElement | null = ((): HTMLInputElement | null => {
			let field: HTMLInputElement | null = null;
			new Setting(modal.contentEl).setName("Page number").addText((t) => {
				t.inputEl.type = "number";
				t.inputEl.min = "1";
				t.inputEl.placeholder = this.pdfPages > 0 ? `1–${this.pdfPages}` : "1…";
				field = t.inputEl;
				window.setTimeout(() => field?.focus(), 0);
			});
			return field;
		})();
		if (input === null) {
			modal.close();
			return;
		}
		const row = modal.contentEl.createDiv({ cls: "modal-button-container" });
		const jumpBtn = row.createEl("button", { cls: "mod-cta", text: "Go" });
		const execute = () => {
			const nr = Number.parseInt(input.value, 10);
			if (Number.isFinite(nr) && nr > 0) this.coupling.goToPage(nr);
			modal.close();
		};
		jumpBtn.addEventListener("click", () => execute());
		input.addEventListener("keydown", (e) => {
			if (e.key === "Enter") execute();
		});
		modal.open();
	}

	applySettings(): void {
		this.coupling.active = this.plugin.settings.syncActive;
		this.applyColumnWidths(this.plugin.settings.columnWidths);
	}

	private smallButton(
		parent: HTMLElement,
		text: string,
		ariaLabel: string,
		action: () => void,
	): HTMLButtonElement {
		const btn = parent.createEl("button", {
			cls: "ocr-knopf ocr-knopf-klein",
			text,
			...(ariaLabel.length > 0
				? { attr: { "aria-label": ariaLabel, title: ariaLabel } }
				: {}),
		});
		btn.addEventListener("click", () => action());
		return btn;
	}

	private applyColumnWidths(widths: readonly [number, number, number]): void {
		const cols = this.frame.querySelectorAll<HTMLElement>(".ocr-spalte");
		for (let i = 0; i < cols.length; i++) {
			const col = cols[i];
			const width = widths[i];
			if (col === undefined || width === undefined) continue;
			col.style.setProperty("--ocr-spalte-basis", `${width}%`);
		}
	}

	private wireHandle(handle: HTMLElement, leftIdx: 0 | 1, rightIdx: 1 | 2): void {
		handle.addEventListener("mousedown", (start) => {
			start.preventDefault();
			const frameWidth = Math.max(this.frame.getBoundingClientRect().width, 1);
			const startX = start.clientX;
			const startWidths = [...this.plugin.settings.columnWidths] as [
				number,
				number,
				number,
			];
			let last: [number, number, number] = startWidths;
			const third: 0 | 2 = leftIdx === 0 ? 2 : 0;
			const computeWidths = (delta: number): [number, number, number] => {
				const b = [...startWidths] as [number, number, number];
				const leftVal = b[leftIdx];
				const rightVal = b[rightIdx];
				const leftNew = Math.min(
					Math.max(leftVal + delta, COLUMN_MIN),
					100 - 2 * COLUMN_MIN,
				);
				const rightNew = Math.min(
					Math.max(rightVal - (leftNew - leftVal), COLUMN_MIN),
					100 - leftNew - COLUMN_MIN,
				);
				b[leftIdx] = leftNew;
				b[rightIdx] = rightNew;
				b[third] = 100 - leftNew - rightNew;
				return b;
			};
			const move = (e: MouseEvent) => {
				const delta = ((e.clientX - startX) / frameWidth) * 100;
				last = computeWidths(delta);
				this.applyColumnWidths(last);
				this.coupling.remeasure();
			};
			const release = () => {
				window.removeEventListener("mousemove", move);
				window.removeEventListener("mouseup", release);
				this.plugin.settings.columnWidths = last;
				void this.plugin.saveSettings();
			};
			window.addEventListener("mousemove", move);
			window.addEventListener("mouseup", release);
		});
	}
}

export class PdfSelectModal extends SuggestModal<TFile> {
	onSelection: ((file: TFile) => void) | null = null;

	constructor(
		app: App,
		private pdfs: TFile[],
	) {
		super(app);
		this.emptyStateText = "No PDFs in vault.";
	}

	getSuggestions(query: string): TFile[] {
		const q = query.trim().toLowerCase();
		if (q.length === 0) return this.pdfs.slice(0, 50);
		return this.pdfs.filter((f) => f.path.toLowerCase().includes(q)).slice(0, 50);
	}

	renderSuggestion(file: TFile, el: HTMLElement): void {
		el.createDiv({ text: file.basename });
		el.createDiv({ cls: "suggestion-note", text: file.path });
	}

	onChooseSuggestion(file: TFile): void {
		this.onSelection?.(file);
	}
}

class NoteModal extends Modal {
	onSaved: ((text: string) => void) | null = null;

	constructor(
		app: App,
		private initial: string,
	) {
		super(app);
	}

	onOpen(): void {
		this.titleEl.setText("Preview note");
		const field = this.contentEl.createEl("textarea", {
			cls: "ocr-notiz-feld",
			text: this.initial,
		});
		const row = this.contentEl.createDiv({ cls: "modal-button-container" });
		const cancelBtn = row.createEl("button", { text: "Cancel" });
		cancelBtn.addEventListener("click", () => this.close());
		const saveBtn = row.createEl("button", { cls: "mod-cta", text: "Save" });
		saveBtn.addEventListener("click", () => {
			this.onSaved?.(field.value);
			this.close();
		});
	}

	onClose(): void {
		this.contentEl.empty();
	}
}

export class PageSelectModal extends Modal {
	onSelection: ((pages: string) => void) | null = null;
	private input: HTMLInputElement | null = null;

	constructor(
		app: App,
		private file: TFile,
	) {
		super(app);
	}

	async onOpen(): Promise<void> {
		this.titleEl.setText("Select pages");
		this.contentEl.createDiv({
			cls: "setting-item-description",
			text: `PDF: ${this.file.basename}`,
		});
		try {
			const loaded = (typeof window.pdfjsLib === "undefined" ? await loadPdfJs() : window.pdfjsLib) as {
				getDocument: (opts: object) => { promise: Promise<{ numPages: number }> };
			};
			const pdfjs = window.pdfjsLib ?? loaded;
			if (typeof window.pdfjsLib === "undefined" && pdfjs) {
				(window as unknown as { pdfjsLib: unknown }).pdfjsLib = pdfjs;
			}
			const doc = await pdfjs.getDocument({
				url: this.app.vault.getResourcePath(this.file),
			}).promise;
			this.contentEl.createDiv({
				cls: "setting-item-description",
				text: `Total ${doc.numPages} pages`,
			});
		} catch {
			this.contentEl.createDiv({
				cls: "setting-item-description",
				text: "(Could not determine page count)",
			});
		}
		new Setting(this.contentEl)
			.setName("Pages")
			.setDesc("e.g. 1,3-5,8 — leave empty = all pages")
			.addText((t) => {
				t.inputEl.placeholder = "all pages";
				t.inputEl.addClass("ocr-seiten-eingabe");
				this.input = t.inputEl;
				window.setTimeout(() => t.inputEl.focus(), 0);
			});
		const row = this.contentEl.createDiv({ cls: "modal-button-container" });
		const cancelBtn = row.createEl("button", { text: "Cancel" });
		cancelBtn.addEventListener("click", () => this.close());
		const convertBtn = row.createEl("button", {
			cls: "mod-cta",
			text: "Convert",
		});
		const execute = () => {
			const val = this.input?.value.trim() ?? "";
			this.onSelection?.(val);
			this.close();
		};
		convertBtn.addEventListener("click", () => execute());
		this.input?.addEventListener("keydown", (e) => {
			if (e.key === "Enter") execute();
		});
	}

	onClose(): void {
		this.contentEl.empty();
	}
}

function openSettings(plugin: OcrPreviewPlugin): void {
	const setting = (
		plugin.app as unknown as {
			setting?: { openTabById?: (id: string) => void; open?: () => void };
		}
	).setting;
	if (setting?.openTabById !== undefined) setting.openTabById(plugin.manifest.id);
	else if (setting?.open !== undefined) setting.open();
}

function isInputTarget(target: EventTarget | null): boolean {
	if (target instanceof HTMLInputElement) return true;
	if (target instanceof HTMLTextAreaElement) return true;
	if (target instanceof HTMLSelectElement) return true;
	return target instanceof HTMLElement && target.isContentEditable;
}
