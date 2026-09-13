// Scroll coupling between PDF and Markdown columns.
//
// Mapping is FRACTIONAL, not rounded to whole pages: a PDF page and its
// Markdown block typically have very different heights, and jumping to page top feels wrong.
//
//   Driver:  Find block at viewport top edge
//            p = pageNum + (scrollTop - blockTop) / blockHeight      // real number
//   Follower: target = block(floor(p)).top + (p - floor(p)) * height
//
// Feedback loop protection — programmatically setting `scrollTop` fires `scroll` event in follower — is triple guarded:
//   1. Owner token with re-triggered timeout. Lock releases 120 ms after LAST event of driver.
//   2. No `scrollIntoView({behavior:'smooth'})`. Direct `scrollTop` writing, bundled to one write per frame.
//   3. Epsilon of 2 px against subpixel rounding jitter.

export type Column = "pdf" | "md";

export interface SyncSource {
	scrollEl: HTMLElement;
	/** Page number → anchoring element. Gaps are normal (e.g. `--nur-ocr`), clamped to nearest key. */
	elements(): Map<number, HTMLElement>;
}

interface Measurement {
	numbers: number[];
	top: Map<number, number>;
	height: Map<number, number>;
}

const LOCK_MS = 120;
const EPSILON = 2;

function measure(source: SyncSource): Measurement {
	const elements = source.elements();
	const base = source.scrollEl.getBoundingClientRect().top;
	const scrollTop = source.scrollEl.scrollTop;
	const top = new Map<number, number>();
	const height = new Map<number, number>();
	for (const [num, el] of elements) {
		const rect = el.getBoundingClientRect();
		top.set(num, rect.top - base + scrollTop);
		height.set(num, Math.max(rect.height, 1));
	}
	return {
		numbers: [...elements.keys()].sort((a, b) => a - b),
		top,
		height,
	};
}

/** Largest key <= num, otherwise smallest existing. */
function nextNumber(numbers: number[], num: number): number | null {
	if (numbers.length === 0) return null;
	let match: number | null = null;
	for (const candidate of numbers) {
		if (candidate <= num) match = candidate;
		else break;
	}
	return match ?? numbers[0] ?? null;
}

export class Coupling {
	active = true;
	/** Called on position change with current fractional page — column headers use it for "p. n / m". */
	onPage: ((page: number) => void) | null = null;

	private measurements = new Map<Column, Measurement>();
	private driver: Column | null = null;
	private lockTimer: number | null = null;
	private rafId: number | null = null;
	private pending: Array<[Column, number]> = [];
	private unbind: Array<() => void> = [];
	private sources: Record<Column, SyncSource>;

	constructor(sources: Record<Column, SyncSource>) {
		this.sources = sources;
		for (const col of ["pdf", "md"] as const) {
			const source = sources[col];
			const handler = () => this.onScroll(col);
			source.scrollEl.addEventListener("scroll", handler, { passive: true });
			this.unbind.push(() =>
				source.scrollEl.removeEventListener("scroll", handler),
			);
		}
	}

	destroy(): void {
		for (const un of this.unbind) un();
		this.unbind = [];
		if (this.lockTimer !== null) window.clearTimeout(this.lockTimer);
		if (this.rafId !== null) window.cancelAnimationFrame(this.rafId);
	}

	/** Call after events that change heights: block rendered, ResizeObserver, Canvas swapped, view mode toggled. */
	remeasure(): void {
		for (const col of ["pdf", "md"] as const) {
			this.measurements.set(col, measure(this.sources[col]));
		}
	}

	/** Current fractional page of a column. */
	position(column: Column): number | null {
		const m = this.measurements.get(column);
		if (m === undefined || m.numbers.length === 0) return null;
		const scrollTop = this.sources[column].scrollEl.scrollTop;
		let match = m.numbers[0] as number;
		for (const num of m.numbers) {
			const top = m.top.get(num);
			if (top === undefined) continue;
			if (top <= scrollTop + 1) match = num;
			else break;
		}
		const top = m.top.get(match) ?? 0;
		const height = m.height.get(match) ?? 1;
		const fraction = Math.min(Math.max((scrollTop - top) / height, 0), 0.999);
		return match + fraction;
	}

	/** Set both columns to a page — for "Go to page" and sidebar. */
	goToPage(num: number): void {
		this.driver = "pdf";
		this.retriggerLock();
		for (const col of ["pdf", "md"] as const) {
			const m = this.measurements.get(col);
			if (m === undefined) continue;
			const target = nextNumber(m.numbers, num);
			if (target === null) continue;
			this.write(col, m.top.get(target) ?? 0);
		}
		this.onPage?.(num);
	}

	private retriggerLock(): void {
		if (this.lockTimer !== null) window.clearTimeout(this.lockTimer);
		this.lockTimer = window.setTimeout(() => {
			this.driver = null;
			this.lockTimer = null;
		}, LOCK_MS);
	}

	private onScroll(column: Column): void {
		if (this.driver !== null && this.driver !== column) return;
		this.driver = column;
		this.retriggerLock();

		const p = this.position(column);
		if (p === null) return;
		this.onPage?.(p);
		if (!this.active) return;

		const follower: Column = column === "pdf" ? "md" : "pdf";
		const m = this.measurements.get(follower);
		if (m === undefined || m.numbers.length === 0) return;
		const whole = Math.floor(p);
		const target = nextNumber(m.numbers, whole);
		if (target === null) return;
		const top = m.top.get(target) ?? 0;
		const height = m.height.get(target) ?? 1;
		const fraction = target === whole ? p - whole : 0;
		this.schedule(follower, top + fraction * height);
	}

	private schedule(column: Column, target: number): void {
		this.pending = this.pending.filter(([s]) => s !== column);
		this.pending.push([column, target]);
		if (this.rafId !== null) return;
		this.rafId = window.requestAnimationFrame(() => {
			this.rafId = null;
			const work = this.pending;
			this.pending = [];
			for (const [col, t] of work) this.write(col, t);
		});
	}

	private write(column: Column, target: number): void {
		const el = this.sources[column].scrollEl;
		const clamped = Math.min(
			Math.max(target, 0),
			Math.max(el.scrollHeight - el.clientHeight, 0),
		);
		if (Math.abs(clamped - el.scrollTop) < EPSILON) return;
		el.scrollTop = clamped;
	}
}
