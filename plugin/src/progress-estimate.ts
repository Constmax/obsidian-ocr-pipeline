// Stage-2 run progress for the progress notice (Issue #26): page position,
// derailed pages so far, and a rough estimate of the time left. Free of
// Obsidian imports so it runs under `node --test`.

import type { ProgressEvent } from "./conversion.ts";

type PageEvent = Extract<ProgressEvent, { type: "page" }>;

/** "4 min", "1 h 25 min", minutes rounded up; null under a minute. */
function roughDuration(seconds: number): string | null {
	if (seconds < 60) return null;
	const minutes = Math.ceil(seconds / 60);
	if (minutes < 60) return `${minutes} min`;
	const hours = Math.floor(minutes / 60);
	const rest = minutes % 60;
	return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`;
}

/** Time left as the notice shows it; the order of magnitude is the information. */
export function formatRemaining(seconds: number): string {
	const duration = roughDuration(seconds);
	return duration === null ? "under 1 min left" : `about ${duration} left`;
}

export class RunProgress {
	private total = 0;
	private finished = 0;
	private derailed = 0;
	private timedPages = 0;
	private timedSeconds = 0;

	/** The `start` event: the pages this run converts. */
	start(pages: number): void {
		this.total = pages;
	}

	record(event: PageEvent): void {
		this.total = event.total;
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

	/**
	 * "page n of m · k derailed · about x min left"; before the first page
	 * "m pages", empty before the `start` event.
	 */
	describe(): string {
		if (this.finished === 0) {
			if (this.total === 0) return "";
			return this.total === 1 ? "1 page" : `${this.total} pages`;
		}
		// `von` counts the pages of this run, `nr` is a page number: with a
		// page selection (`--pages 5-7`) `nr` exceeds `von`, so the position
		// is the count of finished pages.
		const parts = [`page ${this.finished} of ${this.total}`];
		if (this.derailed > 0) parts.push(`${this.derailed} derailed`);
		const left = this.remainingSeconds();
		if (left !== null) parts.push(formatRemaining(left));
		return parts.join(" · ");
	}

	/** The status-bar form: "OCR n/m · ~x min · k derailed". */
	status(): string {
		if (this.total === 0) return "OCR …";
		const parts = [`OCR ${this.finished}/${this.total}`];
		const left = this.remainingSeconds();
		if (left !== null) {
			const duration = roughDuration(left);
			parts.push(duration === null ? "<1 min" : `~${duration}`);
		}
		if (this.derailed > 0) parts.push(`${this.derailed} derailed`);
		return parts.join(" · ");
	}

	/** Mean time of the timed pages times the pages still to come. */
	private remainingSeconds(): number | null {
		const left = this.total - this.finished;
		if (left === 0 || this.timedPages === 0) return null;
		return (this.timedSeconds / this.timedPages) * left;
	}
}
