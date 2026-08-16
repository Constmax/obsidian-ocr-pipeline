import { test } from "node:test";
import assert from "node:assert/strict";

// ── Runtime Shim ───────────────────────────────────────────────────────────

let rafCallbacks: Array<() => void> = [];

Object.defineProperty(globalThis, "window", {
	configurable: true,
	value: {
		setTimeout: (fn: () => void, ms?: number) => setTimeout(fn, ms),
		clearTimeout: (id?: NodeJS.Timeout) => clearTimeout(id),
		requestAnimationFrame: (cb: () => void) => {
			rafCallbacks.push(cb);
			return rafCallbacks.length;
		},
		cancelAnimationFrame: () => undefined,
	},
});

function runRaf(): void {
	const work = rafCallbacks;
	rafCallbacks = [];
	for (const cb of work) cb();
}

function sleep(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

import type { SyncSource } from "../src/sync.ts";
const { Coupling } = await import("../src/sync.ts");
type Coupling = InstanceType<typeof Coupling>;

// ── Fake DOM ────────────────────────────────────────────────────────────

class FakeScrollEl {
	scrollTop = 0;
	scrollHeight: number;
	clientHeight: number;
	private handler: (() => void) | null = null;

	constructor(scrollHeight: number, clientHeight: number) {
		this.scrollHeight = scrollHeight;
		this.clientHeight = clientHeight;
	}

	addEventListener(_type: string, handler: () => void): void {
		this.handler = handler;
	}

	removeEventListener(_type: string, _handler: () => void): void {
		this.handler = null;
	}

	getBoundingClientRect(): { top: number } {
		return { top: 0 };
	}

	fire(): void {
		this.handler?.();
	}
}

class FakeBlock {
	private readonly top: number;
	private readonly height: number;

	constructor(top: number, height: number) {
		this.top = top;
		this.height = height;
	}

	getBoundingClientRect(): { top: number; height: number } {
		return { top: this.top, height: this.height };
	}
}

interface Setup {
	coupling: Coupling;
	pdf: FakeScrollEl;
	md: FakeScrollEl;
}

function build(
	pdfBlocks: Array<[number, number, number]>,
	mdBlocks: Array<[number, number, number]>,
): Setup {
	const pdf = new FakeScrollEl(2000, 600);
	const md = new FakeScrollEl(2000, 600);
	const column = (
		el: FakeScrollEl,
		blocks: Array<[number, number, number]>,
	): SyncSource => ({
		scrollEl: el as unknown as HTMLElement,
		elements: () =>
			new Map<number, HTMLElement>(
				blocks.map(
					([nr, top, height]) =>
						[nr, new FakeBlock(top, height) as unknown as HTMLElement] as const,
				),
			),
	});
	const coupling = new Coupling({ pdf: column(pdf, pdfBlocks), md: column(md, mdBlocks) });
	coupling.remeasure();
	return { coupling, pdf, md };
}

const BLOCKS: Array<[number, number, number]> = [
	[1, 0, 100],
	[2, 100, 100],
	[3, 200, 100],
];

// ── Mapping ───────────────────────────────────────────────────────────────

test("position: fraction over block height, not over page number", () => {
	const { coupling, pdf } = build(
		[
			[1, 0, 100],
			[2, 100, 200],
			[3, 300, 100],
		],
		BLOCKS,
	);
	pdf.scrollTop = 150;
	assert.equal(coupling.position("pdf"), 2.25);
});

test("position clamps to end, never past last page", () => {
	const { coupling, pdf } = build(BLOCKS, BLOCKS);
	pdf.scrollTop = 9999;
	assert.equal(coupling.position("pdf"), 3.999);
});

test("without measurement or without elements coupling remains silent", () => {
	const pdf = new FakeScrollEl(2000, 600);
	const md = new FakeScrollEl(2000, 600);
	const coupling = new Coupling({
		pdf: { scrollEl: pdf as unknown as HTMLElement, elements: () => new Map() },
		md: { scrollEl: md as unknown as HTMLElement, elements: () => new Map() },
	});
	assert.equal(coupling.position("pdf"), null);
	pdf.scrollTop = 150;
	pdf.fire();
	runRaf();
	assert.equal(md.scrollTop, 0);
});

// ── Driver → Follower ────────────────────────────────────────────────────────

test("scroll on pdf drives md to same fraction", () => {
	const { coupling, pdf, md } = build(BLOCKS, BLOCKS);
	let reported: number | null = null;
	coupling.onPage = (s) => (reported = s);
	pdf.scrollTop = 150;
	pdf.fire();
	assert.equal(reported, 2.5);
	runRaf();
	assert.equal(md.scrollTop, 150);
});

test("owner token: echo scroll of follower does not play back", () => {
	const { pdf, md } = build(BLOCKS, BLOCKS);
	pdf.scrollTop = 250;
	pdf.fire();
	runRaf();
	assert.equal(md.scrollTop, 250);
	md.fire();
	runRaf();
	assert.equal(md.scrollTop, 250, "md stays still");
	assert.equal(pdf.scrollTop, 250, "pdf is not written back");
});

test("epsilon: jitter below 2 px is not written", () => {
	const { pdf, md } = build(BLOCKS, BLOCKS);
	md.scrollTop = 151.9;
	pdf.scrollTop = 150;
	pdf.fire();
	runRaf();
	assert.equal(md.scrollTop, 151.9);
});

test("epsilon: distance of exactly 2 px is still a write operation", () => {
	const { pdf, md } = build(BLOCKS, BLOCKS);
	md.scrollTop = 148;
	pdf.scrollTop = 150;
	pdf.fire();
	runRaf();
	assert.equal(md.scrollTop, 150);
});

test("write clamps to scroll bounds of follower", () => {
	const { pdf, md } = build(BLOCKS, BLOCKS);
	md.scrollHeight = 350;
	md.clientHeight = 100;
	pdf.scrollTop = 9999;
	pdf.fire();
	runRaf();
	assert.equal(md.scrollTop, 250);
});

test("two driver events in same frame become single write operation", () => {
	const { pdf, md } = build(BLOCKS, BLOCKS);
	pdf.scrollTop = 150;
	pdf.fire();
	pdf.scrollTop = 250;
	pdf.fire();
	assert.equal(rafCallbacks.length, 1, "exactly one frame write operation");
	runRaf();
	assert.equal(md.scrollTop, 250, "last state wins");
});

test("clamping: missing follower page — fraction dropped, exact start", () => {
	const { pdf, md } = build(BLOCKS, [
		[1, 0, 100],
		[3, 100, 100],
	]);
	pdf.scrollTop = 150;
	pdf.fire();
	runRaf();
	assert.equal(md.scrollTop, 0);
});

// ── Lock ────────────────────────────────────────────────────────────────────

test("lock: retriggered — events in window extend it", async () => {
	const { pdf, md } = build(BLOCKS, BLOCKS);
	pdf.scrollTop = 150;
	pdf.fire();
	runRaf();
	await sleep(80);
	pdf.scrollTop = 250;
	pdf.fire();
	runRaf();
	assert.equal(md.scrollTop, 250);
	await sleep(80);
	md.fire();
	runRaf();
	assert.equal(pdf.scrollTop, 250, "md not heard yet");
	await sleep(80);
	md.scrollTop = 50;
	md.fire();
	runRaf();
	assert.equal(pdf.scrollTop, 50, "md now drives pdf");
});

test("goToPage sets both columns to page start", () => {
	const { coupling, pdf, md } = build(BLOCKS, BLOCKS);
	const reported: number[] = [];
	coupling.onPage = (s) => reported.push(s);
	coupling.goToPage(2);
	assert.equal(pdf.scrollTop, 100);
	assert.equal(md.scrollTop, 100);
	assert.deepEqual(reported, [2]);
});

test("goToPage clamps past end to last page", () => {
	const { coupling, pdf, md } = build(BLOCKS, BLOCKS);
	coupling.goToPage(99);
	assert.equal(pdf.scrollTop, 200);
	assert.equal(md.scrollTop, 200);
});

test("active=false: position reported, not written", () => {
	const { coupling, pdf, md } = build(BLOCKS, BLOCKS);
	coupling.active = false;
	const reported: number[] = [];
	coupling.onPage = (s) => reported.push(s);
	pdf.scrollTop = 150;
	pdf.fire();
	assert.deepEqual(reported, [2.5]);
	runRaf();
	assert.equal(md.scrollTop, 0);
});

test("destroy detaches listeners and timers", () => {
	const { coupling, pdf, md } = build(BLOCKS, BLOCKS);
	coupling.destroy();
	pdf.scrollTop = 150;
	pdf.fire();
	runRaf();
	assert.equal(md.scrollTop, 0);
	assert.equal(pdf.scrollTop, 150);
});
