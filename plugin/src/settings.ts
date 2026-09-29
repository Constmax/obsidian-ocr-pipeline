import {
	App,
	DropdownComponent,
	Platform,
	PluginSettingTab,
	Setting,
	TextComponent,
	normalizePath,
} from "obsidian";
import { checkEngineHere, checkPdf2mdHere } from "./conversion-host.ts";
import type OcrPreviewPlugin from "./main.ts";
import {
	DEFAULT_OCR_SETTINGS,
	DESKTOP_ONLY_MESSAGE,
	OCR_ENGINES,
	isOcrEngine,
	type OcrEngine,
	type OcrSettings,
} from "./ocr-settings.ts";
import {
	DEFAULT_PDF2MD_SETTINGS,
	PDF2MD_DEFAULTS,
	parseDpi,
	parseTileFrom,
	pdf2mdExecutable,
	pdf2mdPathProblem,
	type Parsed,
	type Pdf2mdSettings,
} from "./pdf2md-settings.ts";

export type OperationMode = "review-flow" | "workbench";

export interface Settings extends OcrSettings, Pdf2mdSettings {
	/** Folder where pdf2md.py writes (--out). */
	previewFolder: string;
	acceptedFolder: string;
	rejectedFolder: string;
	statusFile: string;
	markdownView: "rendered" | "source";
	/** Review flow prioritizes decisions; workbench also exposes editing tools. */
	operationMode: OperationMode;
	/** Column widths in percent: sidebar, PDF, Markdown. */
	columnWidths: [number, number, number];
	/** Upper limit for render factor. Memory limiter, not quality setting:
	 *  an A4 canvas at factor 2 is already ~4.5 MB RGBA. */
	pdfZoomMax: number;
	syncActive: boolean;
	/** Above this many pages, the Markdown column no longer fully renders
	 *  in advance. Safety valve for outliers, rarely reached in normal usage. */
	mdEagerLimit: number;
}

export const DEFAULT_SETTINGS: Settings = {
	previewFolder: "_ocr-preview",
	acceptedFolder: "_ocr-preview/_accepted",
	rejectedFolder: "_ocr-preview/_rejected",
	statusFile: "_ocr-preview/review-status.json",
	markdownView: "rendered",
	operationMode: "review-flow",
	columnWidths: [20, 40, 40],
	pdfZoomMax: 2,
	syncActive: true,
	mdEagerLimit: 200,
	...DEFAULT_OCR_SETTINGS,
	...DEFAULT_PDF2MD_SETTINGS,
};

const PATH_DEBOUNCE_MS = 600;

/** One label per offered engine; the Record type rejects extra engines. */
const ENGINE_LABELS: Record<OcrEngine, string> = {
	auto: "Automatic",
	apple: "Apple Vision",
	tesseract: "Tesseract",
	paddle: "PaddleOCR (fast)",
};

export class SettingsTab extends PluginSettingTab {
	constructor(
		app: App,
		private plugin: OcrPreviewPlugin,
	) {
		super(app, plugin);
	}

	display(): void {
		const { containerEl } = this;
		containerEl.empty();

		containerEl.createEl("p", {
			cls: "ocr-einstellungen-hinweis",
			text:
				"The three folders represent the state: where a file is located determines " +
				"its status. review-status.json is only a cache with notes " +
				"and can be deleted at any time.",
		});

		this.folderField(
			"Preview folder",
			"The folder where pdf2md.py writes (--out). Open previews are located here.",
			"previewFolder",
		);
		this.folderField(
			"Accepted folder",
			"Destination for 'Accept'.",
			"acceptedFolder",
		);
		this.folderField(
			"Rejected folder",
			"Destination for 'Reject'. Nothing is deleted.",
			"rejectedFolder",
		);
		this.folderField(
			"Status file",
			"Path to review-status.json.",
			"statusFile",
			true,
		);

		new Setting(containerEl)
			.setName("Operating mode")
			.setDesc(
				"Review flow keeps the three column headers and emphasizes decisions. " +
					"Workbench combines the controls in one toolbar and enables editing " +
					"of generated Markdown. A new conversion overwrites manual edits.",
			)
			.addDropdown((d) =>
				d
					.addOption("review-flow", "Review flow")
					.addOption("workbench", "Workbench")
					.setValue(this.plugin.settings.operationMode)
					.onChange(async (value) => {
						this.plugin.settings.operationMode =
							value === "workbench" ? "workbench" : "review-flow";
						await this.plugin.saveSettings();
						this.plugin.openView()?.applySettings();
					}),
			);

		new Setting(containerEl)
			.setName("Markdown column")
			.setDesc("How the right column is displayed when opened.")
			.addDropdown((d) =>
				d
					.addOption("rendered", "Rendered")
					.addOption("source", "Source code")
					.setValue(this.plugin.settings.markdownView)
					.onChange(async (val) => {
						this.plugin.settings.markdownView =
							val === "source" ? "source" : "rendered";
						await this.plugin.saveSettings();
					}),
			);

		new Setting(containerEl)
			.setName("Scroll sync")
			.setDesc("Synchronize PDF and Markdown scrolling. Can also be toggled in the view.")
			.addToggle((t) =>
				t
					.setValue(this.plugin.settings.syncActive)
					.onChange(async (val) => {
						this.plugin.settings.syncActive = val;
						await this.plugin.saveSettings();
						this.plugin.openView()?.applySettings();
					}),
			);

		new Setting(containerEl)
			.setName("PDF render factor")
			.setDesc(
				"Upper limit for rasterization — memory limiter, not quality choice: " +
					"an A4 canvas at factor 2 is already ~4.5 MB RGBA.",
			)
			.addSlider((s) =>
				s
					.setLimits(1, 4, 0.25)
					.setValue(this.plugin.settings.pdfZoomMax)
					.onChange(async (val) => {
						this.plugin.settings.pdfZoomMax = val;
						await this.plugin.saveSettings();
					}),
			);

		new Setting(containerEl)
			.setName("Markdown eager limit")
			.setDesc(
				"Above this many pages, the Markdown column no longer fully renders " +
					"in advance — safety valve for outliers, rarely reached in normal usage.",
			)
			.addSlider((s) =>
				s
					.setLimits(0, 1000, 10)
					.setValue(this.plugin.settings.mdEagerLimit)
					.onChange(async (val) => {
						this.plugin.settings.mdEagerLimit = val;
						await this.plugin.saveSettings();
					}),
			);

		new Setting(containerEl)
			.setName("Column widths")
			.setDesc(
				"Sidebar · PDF · Markdown in percent. Adjustable also by dragging handles " +
					"on column borders.",
			)
			.addText((t) => this.widthField(t, 0))
			.addText((t) => this.widthField(t, 1))
			.addText((t) => this.widthField(t, 2));

		this.conversionSettings();
		this.searchableCopySettings();
	}

	/** Which pdf2md runs, its --dpi and --tile-from, and its preflight; desktop only. */
	private conversionSettings(): void {
		const { containerEl } = this;
		new Setting(containerEl).setName("Conversion").setHeading();

		if (!Platform.isDesktopApp) {
			containerEl.createEl("p", {
				cls: "ocr-einstellungen-hinweis",
				text: "Conversion is only available in Obsidian for desktop: it runs the locally installed pdf2md command.",
			});
			return;
		}

		const pathSetting = new Setting(containerEl)
			.setName("Path to pdf2md")
			.setDesc(
				"Leave empty to search ~/bin, /usr/local/bin and PATH, where setup.sh " +
					"installs it. Enter a path for an installation elsewhere.",
			);
		const pathHint = pathSetting.descEl.createDiv();
		const showPath = (problem: string | null) => {
			pathHint.className = problem === null ? "ocr-einstellungen-info" : "ocr-pfad-hinweis";
			if (problem !== null) {
				pathHint.setText(problem);
			} else if (this.plugin.settings.pdf2mdPath.length === 0) {
				const found = pdf2mdExecutable(this.plugin.settings);
				const missing = pdf2mdPathProblem(found);
				pathHint.setText(missing === null ? `Found: ${found}` : `Not found by the search — ${missing}`);
				pathHint.className = missing === null ? "ocr-einstellungen-info" : "ocr-pfad-hinweis";
			} else {
				pathHint.setText("");
			}
		};
		pathSetting.addText((t) =>
			this.debounced(t, this.plugin.settings.pdf2mdPath, "~/bin/pdf2md", async (val) => {
				const cleaned = val.trim();
				// A path that cannot run is not saved: the next conversion would fail.
				const problem = cleaned.length > 0 ? pdf2mdPathProblem(cleaned) : null;
				if (problem === null) {
					this.plugin.settings.pdf2mdPath = cleaned;
					await this.plugin.saveSettings();
				}
				showPath(problem === null ? null : `Not saved — ${problem}`);
			}),
		);
		showPath(null);

		this.numberField(
			"Render resolution (DPI)",
			"Resolution scan pages are rendered at before OCR (--dpi). Empty uses pdf2md's default.",
			"pdf2mdDpi",
			PDF2MD_DEFAULTS.dpi,
			parseDpi,
		);
		this.numberField(
			"Tile threshold",
			"Scan pages whose text layer has at least this many characters are read in " +
				"horizontal tiles (--tile-from); 0 tiles every such page. Empty uses pdf2md's default.",
			"pdf2mdTileFrom",
			PDF2MD_DEFAULTS.tileFrom,
			parseTileFrom,
		);

		const checkSetting = new Setting(containerEl)
			.setName("Installation check")
			.setDesc("Runs pdf2md --check: Python, PyMuPDF, the OCR runtime and model, write access to the preview folder, memory.");
		const result = checkSetting.descEl.createDiv({ cls: "ocr-check-result" });
		checkSetting.addButton((b) =>
			b.setButtonText("Check installation").onClick(async () => {
				b.setDisabled(true);
				result.empty();
				result.createDiv({ text: "Checking …" });
				try {
					this.showCheck(result, await checkPdf2mdHere(this.app, this.plugin.settings));
				} finally {
					b.setDisabled(false);
				}
			}),
		);
	}

	private showCheck(el: HTMLElement, check: Awaited<ReturnType<typeof checkPdf2mdHere>>): void {
		el.empty();
		if ("error" in check) {
			el.createDiv({ cls: "ocr-check-fehlt", text: `pdf2md could not run its check: ${check.error}` });
			return;
		}
		for (const item of check.checks) {
			el.createDiv({
				cls: item.ok ? "ocr-check-ok" : "ocr-check-fehlt",
				text: `${item.ok ? "✓" : "✗"} ${item.name}: ${item.detail}`,
			});
		}
		for (const warning of check.warnings) {
			el.createDiv({ cls: "ocr-pfad-hinweis", text: `⚠ ${warning}` });
		}
		el.createDiv({
			cls: check.ok ? "ocr-check-ok" : "ocr-check-fehlt",
			text: check.ok ? "All checks passed." : "At least one check failed.",
		});
	}

	/** Whole-number setting; empty stores null (pdf2md's default), invalid input is not saved. */
	private numberField(
		name: string,
		description: string,
		key: "pdf2mdDpi" | "pdf2mdTileFrom",
		placeholder: number,
		parse: (text: string) => Parsed,
	): void {
		const setting = new Setting(this.containerEl).setName(name).setDesc(description);
		const hint = setting.descEl.createDiv({ cls: "ocr-pfad-hinweis" });
		const stored = this.plugin.settings[key];
		setting.addText((t) =>
			this.debounced(t, stored === null ? "" : String(stored), String(placeholder), async (val) => {
				const parsed = parse(val);
				if ("error" in parsed) {
					hint.setText(`Not saved — ${parsed.error}`);
					return;
				}
				hint.setText("");
				this.plugin.settings[key] = parsed.value;
				await this.plugin.saveSettings();
			}),
		);
	}

	/** Text field that saves after typing pauses; `onChange` fires on every keystroke. */
	private debounced(
		t: TextComponent,
		value: string,
		placeholder: string,
		save: (value: string) => Promise<void>,
	): void {
		let timer: number | null = null;
		t.setPlaceholder(placeholder)
			.setValue(value)
			.onChange((val) => {
				if (timer !== null) window.clearTimeout(timer);
				timer = window.setTimeout(() => {
					timer = null;
					void save(val);
				}, PATH_DEBOUNCE_MS);
			});
	}

	/** Engine and column split for "Create searchable copy"; desktop only. */
	private searchableCopySettings(): void {
		const { containerEl } = this;
		new Setting(containerEl).setName("Searchable copy").setHeading();

		if (!Platform.isDesktopApp) {
			containerEl.createEl("p", { cls: "ocr-einstellungen-hinweis", text: DESKTOP_ONLY_MESSAGE });
			return;
		}

		const engineSetting = new Setting(containerEl)
			.setName("OCR engine")
			.setDesc(
				"Used for new searchable copies. Automatic uses Apple Vision when its " +
					"OCRmyPDF plugin is installed and Tesseract otherwise. PaddleOCR (fast) " +
					"keeps two-column pages in reading order without a column split; it is " +
					"offered once the installation check passes.",
			)
			.addDropdown((d) => {
				const stored = this.plugin.settings.ocrEngine;
				for (const engine of OCR_ENGINES) {
					// PaddleOCR joins after the check below, unless it is the stored value.
					if (engine !== "paddle" || stored === "paddle") {
						d.addOption(engine, ENGINE_LABELS[engine]);
					}
				}
				d.setValue(stored).onChange(async (value) => {
					if (!isOcrEngine(value)) return;
					this.plugin.settings.ocrEngine = value;
					await this.plugin.saveSettings();
				});
				void this.offerPaddle(d, engineSetting);
			});

		new Setting(containerEl)
			.setName("Split two-column pages")
			.setDesc(
				"Detects two-column pages, recognizes each column on its own, and merges " +
					"the pages back. Recommended for two-column scripts; needs pikepdf.",
			)
			.addToggle((t) =>
				t
					.setValue(this.plugin.settings.splitColumns)
					.onChange(async (val) => {
						this.plugin.settings.splitColumns = val;
						await this.plugin.saveSettings();
					}),
			);
	}

	/**
	 * Adds PaddleOCR to the engine list once `reprocess-raw --check-engine`
	 * finds it usable; otherwise names the reason under the setting.
	 */
	private async offerPaddle(dropdown: DropdownComponent, setting: Setting): Promise<void> {
		const problem = await checkEngineHere(this.app, "paddle");
		if (problem === null) {
			if (!dropdown.selectEl.querySelector('option[value="paddle"]')) {
				dropdown.addOption("paddle", ENGINE_LABELS.paddle);
			}
			return;
		}
		const reason = problem.replace(/\.$/, "");
		const text =
			this.plugin.settings.ocrEngine === "paddle"
				? `PaddleOCR is not usable here: ${reason}. Searchable copies use Automatic until it is.`
				: `PaddleOCR is not offered: ${reason}.`;
		setting.descEl.createDiv({ cls: "ocr-einstellungen-hinweis", text });
	}

	/** Percentage field for a column width: Invalid falls back to default,
	 *  clamped to 5–95 %. */
	private widthField(t: TextComponent, index: 0 | 1 | 2): void {
		t
			.setPlaceholder(String(DEFAULT_SETTINGS.columnWidths[index]))
			.setValue(String(this.plugin.settings.columnWidths[index]))
			.onChange((val) => {
				const n = Number.parseInt(val.trim(), 10);
				const widths = [...this.plugin.settings.columnWidths] as [
					number,
					number,
					number,
				];
				widths[index] = Number.isFinite(n)
					? Math.min(Math.max(n, 5), 95)
					: DEFAULT_SETTINGS.columnWidths[index];
				this.plugin.settings.columnWidths = widths;
				void this.plugin.saveSettings();
				this.plugin.openView()?.applySettings();
			});
	}

	private folderField(
		name: string,
		description: string,
		key: "previewFolder" | "acceptedFolder" | "rejectedFolder" | "statusFile",
		isFile = false,
	): void {
		// Debounced: `onChange` fires on EVERY keystroke. Without delay,
		// intermediate typed paths would enter settings and trigger vault runs.
		let timer: number | null = null;
		const setting = new Setting(this.containerEl)
			.setName(name)
			.setDesc(description)
			.addText((t) =>
				t
					.setPlaceholder(DEFAULT_SETTINGS[key])
					.setValue(this.plugin.settings[key])
					.onChange((val) => {
						if (timer !== null) window.clearTimeout(timer);
						timer = window.setTimeout(() => {
							timer = null;
							const cleaned = normalizePath(val.trim() || DEFAULT_SETTINGS[key]);
							this.plugin.settings[key] = cleaned;
							void this.plugin.saveSettings();
							setHint(cleaned);
							this.plugin.triggerReconcile();
						}, PATH_DEBOUNCE_MS);
					}),
			);

		const hint = setting.descEl.createDiv({ cls: "ocr-pfad-hinweis" });
		const setHint = (path: string) => {
			if (isFile) {
				hint.setText("");
				return;
			}
			const exists = this.app.vault.getFolderByPath(path) !== null;
			hint.setText(
				exists
					? ""
					: "Folder does not exist — will be created upon first move.",
			);
		};
		setHint(this.plugin.settings[key]);
	}
}
