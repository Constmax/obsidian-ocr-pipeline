import assert from "node:assert/strict";
import { test } from "node:test";

import { RunProgress, formatRemaining } from "../src/progress-estimate.ts";

function page(num: number, total: number, seconds: number, derailed = false) {
	return { type: "page" as const, num, total, seconds, origin: "ocr", derailed };
}

test("formatRemaining: under a minute, minutes rounded up, hours", () => {
	assert.equal(formatRemaining(0), "under 1 min left");
	assert.equal(formatRemaining(59), "under 1 min left");
	assert.equal(formatRemaining(60), "about 1 min left");
	assert.equal(formatRemaining(61), "about 2 min left");
	assert.equal(formatRemaining(59 * 60), "about 59 min left");
	assert.equal(formatRemaining(60 * 60), "about 1 h left");
	assert.equal(formatRemaining(85 * 60), "about 1 h 25 min left");
});

test("estimate: mean page time times the pages still to come", () => {
	const run = new RunProgress();
	run.record(page(1, 10, 30));
	assert.equal(run.describe(), "page 1 of 10 · about 5 min left");
	run.record(page(2, 10, 90));
	// mean 60 s, 8 pages left
	assert.equal(run.describe(), "page 2 of 10 · about 8 min left");
});

test("a page selection counts finished pages, not page numbers", () => {
	const run = new RunProgress();
	// `--pages 5-7`: `nr` is the page number, `von` the pages of this run.
	run.record(page(5, 3, 60));
	assert.equal(run.describe(), "page 1 of 3 · about 2 min left");
	run.record(page(6, 3, 60));
	assert.equal(run.describe(), "page 2 of 3 · about 1 min left");
});

test("no estimate after the last page", () => {
	const run = new RunProgress();
	run.record(page(1, 2, 40));
	run.record(page(2, 2, 40));
	assert.equal(run.describe(), "page 2 of 2");
});

test("pages that took no time (cache) do not pull the mean down", () => {
	const run = new RunProgress();
	run.record(page(1, 6, 0));
	run.record(page(2, 6, 0));
	assert.equal(run.describe(), "page 2 of 6");
	run.record(page(3, 6, 60));
	assert.equal(run.describe(), "page 3 of 6 · about 3 min left");
});

test("derailed pages are counted and stay visible", () => {
	const run = new RunProgress();
	run.record(page(1, 4, 60, true));
	assert.equal(run.describe(), "page 1 of 4 · 1 derailed · about 3 min left");
	run.record(page(2, 4, 60));
	assert.equal(run.describe(), "page 2 of 4 · 1 derailed · about 2 min left");
	run.record(page(3, 4, 60, true));
	assert.equal(run.describe(), "page 3 of 4 · 2 derailed · about 1 min left");
});

test("before the first page: the page count of the run", () => {
	const run = new RunProgress();
	run.start(5);
	assert.equal(run.describe(), "5 pages");
	const single = new RunProgress();
	single.start(1);
	assert.equal(single.describe(), "1 page");
	run.record(page(1, 5, 60));
	assert.equal(run.describe(), "page 1 of 5 · about 4 min left");
});

test("status: the short status-bar form, page count first", () => {
	const run = new RunProgress();
	assert.equal(run.status(), "OCR …");
	run.start(5);
	assert.equal(run.status(), "OCR 0/5");
	run.record(page(1, 5, 60));
	assert.equal(run.status(), "OCR 1/5 · ~4 min");
	run.record(page(2, 5, 20, true));
	// mean 40 s, 3 pages left
	assert.equal(run.status(), "OCR 2/5 · ~2 min · 1 derailed");
	run.record(page(3, 5, 1));
	run.record(page(4, 5, 1));
	assert.equal(run.status(), "OCR 4/5 · <1 min · 1 derailed");
	run.record(page(5, 5, 60));
	assert.equal(run.status(), "OCR 5/5 · 1 derailed");
	const long = new RunProgress();
	long.record(page(1, 100, 60));
	assert.equal(long.status(), "OCR 1/100 · ~1 h 39 min");
});

test("nothing recorded yet: empty description", () => {
	assert.equal(new RunProgress().describe(), "");
});
