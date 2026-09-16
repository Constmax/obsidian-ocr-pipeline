import assert from "node:assert/strict";
import { test } from "node:test";

import {
	INDEX_WAIT_MS,
	INDEX_WAIT_STEPS,
	type PdfSource,
	type SearchableCopyRequest,
} from "../src/conversion-controller.ts";
import type { ConversionResult } from "../src/conversion.ts";
import { DESKTOP_ONLY_MESSAGE, type OcrSettings } from "../src/ocr-settings.ts";
import {
	runSearchableCopy,
	searchableCopyPath,
	type SearchableCopyHost,
} from "../src/searchable-copy.ts";

const SOURCE: PdfSource = { path: "raw/a/case-01.pdf", basename: "case-01" };

function result(overrides: Partial<ConversionResult> = {}): ConversionResult {
	return { code: 0, signal: null, timeout: false, stdoutLast: [], stderrLast: [], ...overrides };
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
}

class FakeController {
	idle = true;
	requests: SearchableCopyRequest[] = [];
	answer: ConversionResult | null = result();

	ensureIdle(): boolean {
		return this.idle;
	}
	async runOcr(request: SearchableCopyRequest): Promise<ConversionResult | null> {
		this.requests.push(request);
		return this.answer;
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

test("failure, cancellation, or a refused run opens nothing and adds no notice", async () => {
	for (const answer of [result({ code: 1 }), result({ code: null, signal: "SIGTERM" }), null]) {
		const host = new FakeHost();
		const controller = new FakeController();
		controller.answer = answer;
		await runSearchableCopy(SOURCE, controller, host);

		assert.equal(controller.requests.length, 1);
		assert.deepEqual(host.openAttempts, [], JSON.stringify(answer));
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
