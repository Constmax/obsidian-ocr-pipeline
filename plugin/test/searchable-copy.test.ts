import assert from "node:assert/strict";
import { test } from "node:test";

import {
	INDEX_WAIT_MS,
	INDEX_WAIT_STEPS,
	type PdfSource,
	type SearchableCopyRequest,
} from "../src/conversion-controller.ts";
import type { SearchableCopyResult } from "../src/conversion.ts";
import { DESKTOP_ONLY_MESSAGE, type OcrSettings } from "../src/ocr-settings.ts";
import {
	mergePageLists,
	normalizePageList,
	runSearchableCopy,
	searchableCopyPath,
	type ExemptionOffer,
	type SearchableCopyHost,
} from "../src/searchable-copy.ts";

const SOURCE: PdfSource = { path: "raw/a/case-01.pdf", basename: "case-01" };

function result(overrides: Partial<SearchableCopyResult> = {}): SearchableCopyResult {
	return {
		code: 0,
		signal: null,
		timeout: false,
		stdoutLast: [],
		stderrLast: [],
		shortPages: [],
		...overrides,
	};
}

class FakeHost implements SearchableCopyHost {
	isDesktop = true;
	notices: string[] = [];
	existing = new Set<string>();
	checked: string[] = [];
	ocr: OcrSettings = { ocrEngine: "tesseract", splitColumns: true };
	/** openPdf succeeds from this attempt on; 1 means immediately. */
	indexedAfterAttempts = 1;
	openAttempts: string[] = [];
	waits: number[] = [];
	offers: ExemptionOffer[] = [];

	notify(message: string): void {
		this.notices.push(message);
	}
	async exists(path: string): Promise<boolean> {
		this.checked.push(path);
		return this.existing.has(path);
	}
	settings(): OcrSettings {
		return this.ocr;
	}
	async openPdf(path: string): Promise<boolean> {
		this.openAttempts.push(path);
		return this.openAttempts.length >= this.indexedAfterAttempts;
	}
	async wait(ms: number): Promise<void> {
		this.waits.push(ms);
	}
	offerExemptions(offer: ExemptionOffer): void {
		this.offers.push(offer);
	}
}

class FakeController {
	idle = true;
	requests: SearchableCopyRequest[] = [];
	/** Answers in order; afterwards `answer` repeats. */
	queue: Array<SearchableCopyResult | null> = [];
	answer: SearchableCopyResult | null = result();

	ensureIdle(): boolean {
		return this.idle;
	}
	async runOcr(request: SearchableCopyRequest): Promise<SearchableCopyResult | null> {
		this.requests.push(request);
		return this.queue.length > 0 ? (this.queue.shift() ?? null) : this.answer;
	}
}

test("searchableCopyPath: <stem>-ocr.pdf beside the source", () => {
	assert.equal(searchableCopyPath("raw/a/case-01.pdf"), "raw/a/case-01-ocr.pdf");
	assert.equal(searchableCopyPath("case.pdf"), "case-ocr.pdf");
	assert.equal(searchableCopyPath("raw/Scan.PDF"), "raw/Scan-ocr.pdf");
	assert.equal(searchableCopyPath("raw.d/v1.2 final.pdf"), "raw.d/v1.2 final-ocr.pdf");
});

test("normal run: saved settings, no page exemptions, opens the new PDF", async () => {
	const host = new FakeHost();
	const controller = new FakeController();
	await runSearchableCopy(SOURCE, controller, host);

	assert.deepEqual(host.checked, ["raw/a/case-01-ocr.pdf"]);
	// The request has no force-OCR field: existing text is always preserved.
	assert.deepEqual(controller.requests, [
		{ source: SOURCE, destination: "raw/a/case-01-ocr.pdf", engine: "tesseract", splitColumns: true },
	]);
	assert.deepEqual(host.openAttempts, ["raw/a/case-01-ocr.pdf"]);
	assert.deepEqual(host.notices, ["OCR Preview: Searchable copy created — raw/a/case-01-ocr.pdf."]);
});

test("settings are read when the action starts", async () => {
	const host = new FakeHost();
	const controller = new FakeController();
	host.ocr = { ocrEngine: "auto", splitColumns: false };
	await runSearchableCopy(SOURCE, controller, host);
	host.ocr = { ocrEngine: "apple", splitColumns: true };
	await runSearchableCopy({ path: "raw/other.pdf", basename: "other" }, controller, host);

	assert.deepEqual(
		controller.requests.map((r) => [r.engine, r.splitColumns]),
		[
			["auto", false],
			["apple", true],
		],
	);
});

test("existing destination stops before spawning", async () => {
	const host = new FakeHost();
	host.existing.add("raw/a/case-01-ocr.pdf");
	const controller = new FakeController();
	await runSearchableCopy(SOURCE, controller, host);

	assert.deepEqual(controller.requests, []);
	assert.deepEqual(host.openAttempts, []);
	assert.deepEqual(host.notices, [
		"OCR Preview: raw/a/case-01-ocr.pdf already exists — no searchable copy was started. Rename or move it first.",
	]);
});

test("duplicate basenames in different folders each use their own folder", async () => {
	const host = new FakeHost();
	host.existing.add("raw/a/case-01-ocr.pdf");
	const controller = new FakeController();

	await runSearchableCopy(SOURCE, controller, host);
	await runSearchableCopy({ path: "raw/b/case-01.pdf", basename: "case-01" }, controller, host);

	assert.deepEqual(host.checked, ["raw/a/case-01-ocr.pdf", "raw/b/case-01-ocr.pdf"]);
	assert.deepEqual(
		controller.requests.map((r) => [r.source.path, r.destination]),
		[["raw/b/case-01.pdf", "raw/b/case-01-ocr.pdf"]],
	);
	assert.deepEqual(host.openAttempts, ["raw/b/case-01-ocr.pdf"]);
});

test("failure without short pages, cancellation, or a refused run offers and opens nothing", async () => {
	for (const answer of [result({ code: 1 }), result({ code: null, signal: "SIGTERM" }), null]) {
		const host = new FakeHost();
		const controller = new FakeController();
		controller.answer = answer;
		await runSearchableCopy(SOURCE, controller, host);

		assert.equal(controller.requests.length, 1);
		assert.deepEqual(host.openAttempts, [], JSON.stringify(answer));
		assert.deepEqual(host.offers, [], JSON.stringify(answer));
		assert.deepEqual(host.notices, [], JSON.stringify(answer));
	}
});

test("a running conversion refuses before checking the destination", async () => {
	const host = new FakeHost();
	const controller = new FakeController();
	controller.idle = false;
	await runSearchableCopy(SOURCE, controller, host);

	assert.deepEqual(host.checked, []);
	assert.deepEqual(controller.requests, []);
});

test("mobile shows the desktop-only message and starts nothing", async () => {
	const host = new FakeHost();
	host.isDesktop = false;
	const controller = new FakeController();
	await runSearchableCopy(SOURCE, controller, host);

	assert.deepEqual(host.notices, [DESKTOP_ONLY_MESSAGE]);
	assert.deepEqual(host.checked, []);
	assert.deepEqual(controller.requests, []);
});

test("waits for the vault index before opening, bounded", async () => {
	const late = new FakeHost();
	late.indexedAfterAttempts = 3;
	await runSearchableCopy(SOURCE, new FakeController(), late);
	assert.deepEqual(late.waits, [INDEX_WAIT_MS, INDEX_WAIT_MS]);
	assert.deepEqual(late.notices, ["OCR Preview: Searchable copy created — raw/a/case-01-ocr.pdf."]);

	const never = new FakeHost();
	never.indexedAfterAttempts = Number.POSITIVE_INFINITY;
	await runSearchableCopy(SOURCE, new FakeController(), never);
	assert.equal(never.waits.length, INDEX_WAIT_STEPS);
	assert.equal(never.openAttempts.length, INDEX_WAIT_STEPS + 1);
	assert.deepEqual(never.notices, [
		"OCR Preview: Searchable copy created at raw/a/case-01-ocr.pdf, but Obsidian has not listed it yet.",
	]);
});

// ── Page exemptions ────────────────────────────────────────────────────────

test("B5 failure offers the short pages and reruns only after confirmation", async () => {
	const host = new FakeHost();
	const controller = new FakeController();
	controller.queue = [result({ code: 1, shortPages: [1, 5] }), result()];
	await runSearchableCopy(SOURCE, controller, host);

	assert.equal(controller.requests.length, 1);
	assert.deepEqual(host.openAttempts, []);
	assert.deepEqual(host.notices, []);
	assert.equal(host.offers.length, 1);
	const offer = host.offers[0]!;
	assert.deepEqual(offer.source, SOURCE);
	assert.deepEqual(offer.shortPages, [1, 5]);
	assert.equal(offer.prefill, "1,5");
	assert.equal(
		offer.message,
		'OCR Preview: No searchable copy of "case-01" — pages 1, 5 have fewer than 50 characters of text. Nothing was written.',
	);

	await offer.confirm(" 1 ");

	assert.equal(controller.requests.length, 2);
	assert.equal(controller.requests[1]!.allowPages, "1");
	assert.equal(controller.requests[1]!.destination, "raw/a/case-01-ocr.pdf");
	assert.deepEqual(host.openAttempts, ["raw/a/case-01-ocr.pdf"]);
	assert.deepEqual(host.notices, ["OCR Preview: Searchable copy created — raw/a/case-01-ocr.pdf."]);
});

test("exemptions do not hide failures on other pages", async () => {
	const host = new FakeHost();
	const controller = new FakeController();
	controller.queue = [
		result({ code: 1, shortPages: [1, 3] }),
		result({ code: 1, shortPages: [3] }),
		result(),
	];
	await runSearchableCopy(SOURCE, controller, host);
	await host.offers[0]!.confirm("1");

	assert.equal(host.offers.length, 2);
	const second = host.offers[1]!;
	assert.deepEqual(second.shortPages, [3]);
	assert.equal(second.prefill, "1,3");
	assert.equal(
		second.message,
		'OCR Preview: No searchable copy of "case-01" — page 3 has fewer than 50 characters of text. Nothing was written.',
	);
	assert.deepEqual(host.openAttempts, []);

	await second.confirm(second.prefill);
	assert.deepEqual(
		controller.requests.map((r) => r.allowPages),
		[undefined, "1", "1,3"],
	);
	assert.deepEqual(host.openAttempts, ["raw/a/case-01-ocr.pdf"]);
});

test("a malformed confirmation reruns nothing", async () => {
	const host = new FakeHost();
	const controller = new FakeController();
	controller.queue = [result({ code: 1, shortPages: [2] })];
	await runSearchableCopy(SOURCE, controller, host);

	for (const input of ["", "abc", "0", "3-1"]) await host.offers[0]!.confirm(input);
	assert.equal(controller.requests.length, 1);
});

test("normalizePageList accepts explicit pages and ranges only", () => {
	assert.equal(normalizePageList("1"), "1");
	assert.equal(normalizePageList(" 1, 5-7 ,9 "), "1,5-7,9");
	assert.equal(normalizePageList("4-4"), "4-4");
	for (const bad of ["", "   ", "0", "5-3", "1,,2", "a", "1-", "-2", "1;2", "1-2-3"]) {
		assert.equal(normalizePageList(bad), null, JSON.stringify(bad));
	}
});

test("mergePageLists keeps earlier exemptions and adds uncovered short pages", () => {
	assert.equal(mergePageLists(undefined, [5, 1, 5]), "1,5");
	assert.equal(mergePageLists("1,4-6", [5, 7, 1]), "1,4-6,7");
	assert.equal(mergePageLists("2", []), "2");
});
