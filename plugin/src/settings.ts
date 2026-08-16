import { App, PluginSettingTab, Setting, TextComponent, normalizePath } from "obsidian";
import type OcrPreviewPlugin from "./main.ts";

export interface Settings {
	/** Folder where pdf2md.py writes (--out). */
	previewFolder: string;
	acceptedFolder: string;
	rejectedFolder: string;
	statusFile: string;
	markdownView: "rendered" | "source";
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
	columnWidths: [20, 40, 40],
	pdfZoomMax: 2,
	syncActive: true,
	mdEagerLimit: 200,
};

const PATH_DEBOUNCE_MS = 600;

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
