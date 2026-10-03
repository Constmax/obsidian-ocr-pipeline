import {
	App,
	DropdownComponent,
	Platform,
	PluginSettingTab,
	Setting,
	SettingGroup,
	normalizePath,
} from "obsidian";
import { checkEngineHere } from "./conversion-host.ts";
import type OcrPreviewPlugin from "./main.ts";
import {
	DEFAULT_OCR_SETTINGS,
	DESKTOP_ONLY_MESSAGE,
	OCR_ENGINES,
	MAX_DPI_LIMIT,
	isOcrEngine,
	parseMaxDpiInput,
	type OcrEngine,
	type OcrSettings,
} from "./ocr-settings.ts";

export type OperationMode = "review-flow" | "workbench";

export interface Settings extends OcrSettings {
	/** Folder where pdf2md.py writes (--out). */
	previewFolder: string;
	acceptedFolder: string;
	rejectedFolder: string;
	statusFile: string;
	markdownView: "rendered" | "source";
	/** Review flow prioritizes decisions; workbench also exposes editing tools. */
	operationMode: OperationMode;
	/** Column widths in percent: sidebar, PDF, Markdown. Set by dragging the
	 *  column borders in the view. */
	columnWidths: [number, number, number];
	syncActive: boolean;
}

export const DEFAULT_SETTINGS: Settings = {
	previewFolder: "_ocr-preview",
	acceptedFolder: "_ocr-preview/_accepted",
	rejectedFolder: "_ocr-preview/_rejected",
	statusFile: "_ocr-preview/review-status.json",
	markdownView: "rendered",
	operationMode: "review-flow",
	columnWidths: [20, 40, 40],
	syncActive: true,
	...DEFAULT_OCR_SETTINGS,
};

const PATH_DEBOUNCE_MS = 600;

/** One label per offered engine; the Record type rejects extra engines. */
const ENGINE_LABELS: Record<OcrEngine, string> = {
	auto: "Automatic",
	apple: "Apple Vision",
	tesseract: "Tesseract",
	paddle: "Apple Vision + RapidOCR (Paddle fast)",
};

type TabId = "general" | "folders" | "pdf-to-markdown" | "searchable-copy";

const TABS: ReadonlyArray<[TabId, string]> = [
	["general", "General"],
	["folders", "Folders"],
	["pdf-to-markdown", "PDF → Markdown"],
	["searchable-copy", "Searchable copy"],
];

export class SettingsTab extends PluginSettingTab {
	constructor(
		app: App,
		private plugin: OcrPreviewPlugin,
	) {
		super(app, plugin);
	}

	/** Kept across re-renders while the settings window stays open. */
	private activeTab: TabId = "general";

	display(): void {
		const { containerEl } = this;
		containerEl.empty();

		const nav = containerEl.createDiv({
			cls: "ocr-settings-tabs",
			attr: { role: "tablist" },
		});
		for (const [id, label] of TABS) {
			const active = id === this.activeTab;
			const button = nav.createEl("button", {
				cls: "ocr-settings-tab",
				text: label,
				attr: { role: "tab", "aria-selected": String(active) },
			});
			button.toggleClass("is-active", active);
			button.addEventListener("click", () => {
				this.activeTab = id;
				this.display();
			});
		}

		if (this.activeTab === "general") this.generalSettings();
		else if (this.activeTab === "folders") this.folderSettings();
		else if (this.activeTab === "pdf-to-markdown") this.pdfToMarkdownSettings();
		else this.searchableCopySettings();
	}

	/** Muted note above or below a group. */
	private hint(text: string): void {
		this.containerEl.createEl("p", { cls: "ocr-settings-note", text });
	}

	/** Placeholder: pdf2md options join here once the plugin passes any. */
	private pdfToMarkdownSettings(): void {
		new SettingGroup(this.containerEl).setHeading("PDF → Markdown");
		this.hint("Settings for converting PDFs to Markdown previews will appear here.");
	}

	private generalSettings(): void {
		const group = new SettingGroup(this.containerEl).setHeading("Review view");

		group.addSetting((setting) => {
			setting
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
		});

		group.addSetting((setting) => {
			setting
				.setName("Markdown column")
				.setDesc("How the right column is displayed when opened.")
				.addDropdown((d) =>
					d
						.addOption("rendered", "Rendered")
						.addOption("source", "Source code")
						.setValue(this.plugin.settings.markdownView)
						.onChange(async (val) => {
							this.plugin.settings.markdownView = val === "source" ? "source" : "rendered";
							await this.plugin.saveSettings();
						}),
				);
		});

		group.addSetting((setting) => {
			setting
				.setName("Scroll sync")
				.setDesc("Synchronize PDF and Markdown scrolling. Can also be toggled in the view.")
				.addToggle((t) =>
					t.setValue(this.plugin.settings.syncActive).onChange(async (val) => {
						this.plugin.settings.syncActive = val;
						await this.plugin.saveSettings();
						this.plugin.openView()?.applySettings();
					}),
				);
		});

		this.hint("Column widths are set by dragging the column borders in the view.");
	}

	private folderSettings(): void {
		this.hint(
			"The three folders represent the state: where a file is located determines " +
				"its status. review-status.json is only a cache with notes " +
				"and can be deleted at any time.",
		);
		const group = new SettingGroup(this.containerEl).setHeading("Review folders");

		this.folderField(
			group,
			"Preview folder",
			"The folder where pdf2md.py writes (--out). Open previews are located here.",
			"previewFolder",
		);
		this.folderField(group, "Accepted folder", "Destination for 'Accept'.", "acceptedFolder");
		this.folderField(
			group,
			"Rejected folder",
			"Destination for 'Reject'. Nothing is deleted.",
			"rejectedFolder",
		);
		this.folderField(group, "Status file", "Path to review-status.json.", "statusFile", true);
	}

	/** Engine and column split for "Create searchable copy"; desktop only. */
	private searchableCopySettings(): void {
		if (!Platform.isDesktopApp) {
			this.hint(DESKTOP_ONLY_MESSAGE);
			return;
		}
		const group = new SettingGroup(this.containerEl).setHeading("Searchable copy");

		group.addSetting((engineSetting) => {
			engineSetting
				.setName("OCR engine")
				.setDesc(
					"Used for new searchable copies. Automatic uses Apple Vision + RapidOCR " +
						"(Paddle fast) when it is ready, otherwise Apple Vision when its OCRmyPDF " +
						"plugin is installed, and Tesseract after that. With two-column splitting " +
						"on, it skips Paddle fast. Apple Vision + " +
						"RapidOCR (Paddle fast) reads with Apple Vision, re-reads citations with " +
						"RapidOCR, and keeps two-column pages in reading order without a column " +
						"split; it is " +
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
		});

		group.addSetting((setting) => {
			setting
				.setName("Split two-column pages")
				.setDesc(
					"Detects two-column pages, recognizes each column on its own, and merges " +
						"the pages back. Recommended for two-column scripts with Apple Vision or " +
						"Tesseract; needs pikepdf. Apple Vision + RapidOCR (Paddle fast) always " +
						"reads whole pages.",
				)
				.addToggle((t) =>
					t.setValue(this.plugin.settings.splitColumns).onChange(async (val) => {
						this.plugin.settings.splitColumns = val;
						await this.plugin.saveSettings();
					}),
				);
		});

		group.addSetting((setting) => {
			setting
				.setName("Maximum scan resolution")
				.setDesc(
					"Dots per inch. Scans above this resolution are downscaled to it before OCR, which makes " +
						"the copy smaller and OCR faster; scans at or below it keep their resolution. " +
						"0 turns it off.",
				)
				.addText((t) => {
					t.inputEl.type = "number";
					t.inputEl.min = "0";
					t.inputEl.max = String(MAX_DPI_LIMIT);
					t.setPlaceholder(String(DEFAULT_OCR_SETTINGS.maxDpi)).setValue(
						String(this.plugin.settings.maxDpi),
					);
					// "change", not onChange: saves once on blur or Enter, not a
					// half-typed "30" on the way to "300". Invalid input shows the
					// saved value again instead of silently diverging from it.
					t.inputEl.addEventListener("change", () => {
						const dpi = parseMaxDpiInput(t.getValue());
						if (dpi !== null && dpi !== this.plugin.settings.maxDpi) {
							this.plugin.settings.maxDpi = dpi;
							void this.plugin.saveSettings();
						}
						t.setValue(String(this.plugin.settings.maxDpi));
					});
				});
		});
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
				? `${ENGINE_LABELS.paddle} is not usable here: ${reason}. Searchable copies use Automatic until it is.`
				: `${ENGINE_LABELS.paddle} is not offered: ${reason}.`;
		setting.descEl.createDiv({ cls: "ocr-einstellungen-hinweis", text });
	}

	private folderField(
		group: SettingGroup,
		name: string,
		description: string,
		key: "previewFolder" | "acceptedFolder" | "rejectedFolder" | "statusFile",
		isFile = false,
	): void {
		group.addSetting((setting) => {
			// After setDesc(): it replaces descEl's content, the hint with it.
			setting.setName(name).setDesc(description);
			const hint = setting.descEl.createDiv({ cls: "ocr-pfad-hinweis" });
			const setHint = (path: string) => {
				const exists = isFile || this.app.vault.getFolderByPath(path) !== null;
				hint.setText(exists ? "" : "Folder does not exist — will be created upon first move.");
			};

			// Debounced: `onChange` fires on EVERY keystroke. Without delay,
			// intermediate typed paths would enter settings and trigger vault runs.
			let timer: number | null = null;
			setting.addText((t) =>
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
			setHint(this.plugin.settings[key]);
		});
	}
}
