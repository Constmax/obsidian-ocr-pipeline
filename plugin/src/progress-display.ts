// Progress display of a running conversion (Issue #26): a persistent notice
// the user can hide without ending the run, and a status-bar item that shows
// the same progress for the whole run and brings the notice back on click.
// Free of Obsidian imports so it runs under `node --test`; conversion-host.ts
// supplies the Notice and the status-bar element.

import type { ProgressDisplay } from "./conversion-controller.ts";

export interface ProgressNotice {
	setMessage(message: string): void;
	hide(): void;
	/** False once the notice was hidden, by the user or by `hide()`. */
	isShown(): boolean;
}

export interface ProgressSurfaces {
	/** Opens a notice; `onCancel` null means no Cancel control. */
	openNotice(message: string, onCancel: (() => void) | null): ProgressNotice;
	statusBar: {
		/** `tooltip` is the full notice message. */
		show(text: string, tooltip: string): void;
		hide(): void;
	};
}

export class ProgressPresenter implements ProgressDisplay {
	private readonly surfaces: ProgressSurfaces;
	private readonly onCancel: () => void;
	private message: string;
	/** Short status-bar text; the run's progress, not its name. */
	private status = "OCR …";
	private notice: ProgressNotice | null;
	private cancelled = false;
	private ended = false;

	constructor(surfaces: ProgressSurfaces, message: string, onCancel: () => void) {
		this.surfaces = surfaces;
		this.onCancel = onCancel;
		this.message = message;
		this.notice = this.openNotice();
		surfaces.statusBar.show(this.status, message);
	}

	setMessage(message: string, status: string = this.status): void {
		if (this.ended) return;
		this.message = message;
		this.status = status;
		if (this.notice?.isShown()) this.notice.setMessage(message);
		this.surfaces.statusBar.show(status, message);
	}

	/** End of the run: removes the notice and the status-bar item. */
	hide(): void {
		if (this.ended) return;
		this.ended = true;
		this.notice?.hide();
		this.notice = null;
		this.surfaces.statusBar.hide();
	}

	/** Status-bar click: brings back a notice the user hid. */
	reopen(): void {
		if (this.ended || this.notice?.isShown()) return;
		this.notice = this.openNotice();
	}

	private openNotice(): ProgressNotice {
		const onCancel = this.cancelled
			? null
			: () => {
					this.cancelled = true;
					this.onCancel();
				};
		return this.surfaces.openNotice(this.message, onCancel);
	}
}
