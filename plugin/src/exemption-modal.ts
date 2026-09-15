// "Run with page exemptions…": shows the pages that failed the B5 text check
// and reruns the searchable copy only with the list the user confirms. There is
// deliberately no way to skip the check for the whole document.

import { App, Modal, Setting } from "obsidian";

import { normalizePageList, type ExemptionOffer } from "./searchable-copy.ts";

export class ExemptionModal extends Modal {
	constructor(
		app: App,
		private offer: ExemptionOffer,
	) {
		super(app);
	}

	onOpen(): void {
		const { contentEl } = this;
		const offer = this.offer;
		this.titleEl.setText("Run with page exemptions");
		contentEl.createDiv({ cls: "setting-item-description", text: `PDF: ${offer.source.path}` });
		contentEl.createDiv({
			cls: "setting-item-description",
			text: `Too little text on: ${offer.shortPages.join(", ")}`,
		});
		contentEl.createEl("p", {
			text:
				"Exempt pages skip the text check, for example a cover or a diagram. " +
				"Every other page must still pass.",
		});

		let value = offer.prefill;
		let input: HTMLInputElement | null = null;
		new Setting(contentEl)
			.setName("Exempt pages")
			.setDesc("Page numbers or ranges, e.g. 1,5-7")
			.addText((t) => {
				t.setValue(offer.prefill);
				t.inputEl.addClass("ocr-seiten-eingabe");
				t.onChange((text) => {
					value = text;
					hint.setText("");
				});
				input = t.inputEl;
				window.setTimeout(() => t.inputEl.focus(), 0);
			});
		const hint = contentEl.createDiv({ cls: "setting-item-description" });

		const row = contentEl.createDiv({ cls: "modal-button-container" });
		const cancelBtn = row.createEl("button", { text: "Cancel" });
		cancelBtn.addEventListener("click", () => this.close());
		const runBtn = row.createEl("button", { cls: "mod-cta", text: "Run with exemptions" });
		const run = () => {
			const list = normalizePageList(value);
			if (list === null) {
				hint.setText("Enter page numbers or ranges, such as 1,5-7.");
				return;
			}
			this.close();
			void offer.confirm(list);
		};
		runBtn.addEventListener("click", run);
		(input as HTMLInputElement | null)?.addEventListener("keydown", (e) => {
			if (e.key === "Enter") run();
		});
	}

	onClose(): void {
		this.contentEl.empty();
	}
}
