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
		show(text: string): void;
		hide(): void;
	};
}

const NOTICE_PREFIX = "OCR Preview: ";

/**
 * The notice message, shortened for the status bar. Progress after " — "
 * moves to the front: the item clips its end, and a long file name must not
 * push the page count out of sight.
 */
export function statusBarText(message: string): string {
	let text = message.startsWith(NOTICE_PREFIX) ? message.slice(NOTICE_PREFIX.length) : message;
	// Progress follows the quoted name; greedy head, as the name may contain " — ".
	const progress = /^(.+") — (.+?)(?: …)?$/.exec(text);
	if (progress !== null) text = `${progress[2]} — ${progress[1]}`;
	return `OCR: ${text}`;
}

export class ProgressPresenter implements ProgressDisplay {
	private readonly surfaces: ProgressSurfaces;
	private readonly onCancel: () => void;
	private message: string;
	private notice: ProgressNotice | null;
	private cancelled = false;
	private ended = false;

	constructor(surfaces: ProgressSurfaces, message: string, onCancel: () => void) {
		this.surfaces = surfaces;
		this.onCancel = onCancel;
		this.message = message;
		this.notice = this.openNotice();
		surfaces.statusBar.show(statusBarText(message));
	}

	setMessage(message: string): void {
		if (this.ended) return;
		this.message = message;
		if (this.notice?.isShown()) this.notice.setMessage(message);
		this.surfaces.statusBar.show(statusBarText(message));
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
