// A yes/no question in a modal. Closing it any other way counts as no, so a
// caller awaiting the answer is never left hanging.

import { App, Modal } from "obsidian";

export function confirmInModal(
	app: App,
	title: string,
	message: string,
	confirmLabel: string,
): Promise<boolean> {
	return new Promise((resolve) => {
		let answer = false;
		const modal = new Modal(app);
		modal.titleEl.setText(title);
		modal.contentEl.createEl("p", { text: message });
		const row = modal.contentEl.createDiv({ cls: "modal-button-container" });
		row.createEl("button", { text: "Cancel" }).addEventListener("click", () => modal.close());
		row.createEl("button", { cls: "mod-cta", text: confirmLabel }).addEventListener("click", () => {
			answer = true;
			modal.close();
		});
		modal.onClose = () => {
			modal.contentEl.empty();
			resolve(answer);
		};
		modal.open();
	});
}
