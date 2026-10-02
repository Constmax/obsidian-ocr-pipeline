// Stage-2 run progress for the progress notice (Issue #26): page position,
// derailed pages so far, and a rough estimate of the time left. Free of
// Obsidian imports so it runs under `node --test`.

import type { ProgressEvent } from "./conversion.ts";

type PageEvent = Extract<ProgressEvent, { type: "page" }>;

/** Time left as the notice shows it; the order of magnitude is the information. */
export function formatRemaining(seconds: number): string {
	if (seconds < 60) return "under 1 min left";
	const minutes = Math.ceil(seconds / 60);
	if (minutes < 60) return `about ${minutes} min left`;
	const hours = Math.floor(minutes / 60);
	const rest = minutes % 60;
	return rest === 0 ? `about ${hours} h left` : `about ${hours} h ${rest} min left`;
}

export class RunProgress {
	private last: PageEvent | null = null;
	private finished = 0;
	private derailed = 0;
	private timedPages = 0;
	private timedSeconds = 0;

	record(event: PageEvent): void {
		this.last = event;
		this.finished++;
		if (event.derailed) this.derailed++;
		// A page reused from the cache reports 0 s. Counting it would pull the
		// mean towards zero on a resumed run, whose remaining pages all need
		// the model.
		if (event.seconds > 0) {
			this.timedPages++;
			this.timedSeconds += event.seconds;
		}
	}

	/** "page n of m · k derailed · about x min left"; empty before the first page. */
	describe(): string {
		if (this.last === null) return "";
		const parts = [`page ${this.last.num} of ${this.last.total}`];
		if (this.derailed > 0) parts.push(`${this.derailed} derailed`);
		// `von` counts the pages of this run, `nr` is a page number: with a
		// page selection only the finished count says how many are left.
		const left = this.last.total - this.finished;
		if (left > 0 && this.timedPages > 0) {
			parts.push(formatRemaining((this.timedSeconds / this.timedPages) * left));
		}
		return parts.join(" · ");
	}
}
