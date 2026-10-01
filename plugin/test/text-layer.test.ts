import assert from "node:assert/strict";
import { test } from "node:test";

import { type PdfSource, type TextLayerRequest } from "../src/conversion-controller.ts";
import type { TextLayerResult } from "../src/conversion.ts";
import { DESKTOP_ONLY_MESSAGE, type OcrEngine, type OcrSettings } from "../src/ocr-settings.ts";
import {
	mergePageLists,
	normalizePageList,
	runAddTextLayer,
	type ExemptionOffer,
	type TextLayerHost,
} from "../src/text-layer.ts";

const SOURCE: PdfSource = { path: "raw/a/case-01.pdf", basename: "case-01" };
const DONE = "OCR Preview: Text layer added — raw/a/case-01.pdf.";

function result(overrides: Partial<TextLayerResult> = {}): TextLayerResult {
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

class FakeHost implements TextLayerHost {
	isDesktop = true;
	notices: string[] = [];
	ocr: OcrSettings = { ocrEngine: "tesseract", splitColumns: true };
	offers: ExemptionOffer[] = [];
	/** Answer of the engine check: null = usable, otherwise the reason. */
	engineProblem: string | null = null;
	engineChecks: string[] = [];

	notify(message: string): void {
		this.notices.push(message);
	}
	settings(): OcrSettings {
		return this.ocr;
	}
	offerExemptions(offer: ExemptionOffer): void {
		this.offers.push(offer);
	}
	async checkEngine(engine: OcrEngine): Promise<string | null> {
		this.engineChecks.push(engine);
		return this.engineProblem;
	}
}

class FakeController {
	idle = true;
	requests: TextLayerRequest[] = [];
	/** Answers in order; afterwards `answer` repeats. */
	queue: Array<TextLayerResult | null> = [];
	answer: TextLayerResult | null = result();

	ensureIdle(): boolean {
		return this.idle;
	}
	async runOcr(request: TextLayerRequest): Promise<TextLayerResult | null> {
		this.requests.push(request);
		return this.queue.length > 0 ? (this.queue.shift() ?? null) : this.answer;
	}
}

test("normal run: saved settings, no page exemptions, the PDF itself gets the layer", async () => {
	const host = new FakeHost();
	const controller = new FakeController();
	await runAddTextLayer(SOURCE, controller, host);

	// No destination, no force-OCR field: the source keeps its path and its existing text.
	assert.deepEqual(controller.requests, [{ source: SOURCE, engine: "tesseract", splitColumns: true }]);
	assert.deepEqual(host.notices, [DONE]);
});

test("settings are read when the action starts", async () => {
	const host = new FakeHost();
	const controller = new FakeController();
	host.ocr = { ocrEngine: "auto", splitColumns: false };
	await runAddTextLayer(SOURCE, controller, host);
	host.ocr = { ocrEngine: "apple", splitColumns: true };
	await runAddTextLayer({ path: "raw/other.pdf", basename: "other" }, controller, host);

	assert.deepEqual(
		controller.requests.map((r) => [r.engine, r.splitColumns]),
		[
			["auto", false],
			["apple", true],
		],
	);
});

test("PaddleOCR runs when the engine check finds it usable (issue #73)", async () => {
	const host = new FakeHost();
	host.ocr = { ocrEngine: "paddle", splitColumns: false };
	const controller = new FakeController();
	await runAddTextLayer(SOURCE, controller, host);

	assert.deepEqual(host.engineChecks, ["paddle"]);
	assert.deepEqual(
		controller.requests.map((r) => r.engine),
		["paddle"],
	);
	assert.deepEqual(host.notices, [DONE]);
});

test("PaddleOCR never splits columns, whatever the toggle says (issue #153)", async () => {
	const host = new FakeHost();
	host.ocr = { ocrEngine: "paddle", splitColumns: true };
	const controller = new FakeController();
	await runAddTextLayer(SOURCE, controller, host);

	assert.deepEqual(
		controller.requests.map((r) => [r.engine, r.splitColumns]),
		[["paddle", false]],
	);
});

test("a stored PaddleOCR that is not usable falls back to Automatic, visibly", async () => {
	const host = new FakeHost();
	host.ocr = { ocrEngine: "paddle", splitColumns: true };
	host.engineProblem = "PaddleOCR engine is not ready: model file missing: /m/x.onnx";
	const controller = new FakeController();
	await runAddTextLayer(SOURCE, controller, host);

	assert.deepEqual(
		controller.requests.map((r) => [r.engine, r.splitColumns]),
		[["auto", true]],
	);
	assert.deepEqual(host.notices, [
		"OCR Preview: This run uses Automatic, because PaddleOCR cannot run here — " +
			"PaddleOCR engine is not ready: model file missing: /m/x.onnx. Choose another engine in the settings.",
		DONE,
	]);
});

test("other engines are not checked before a run", async () => {
	const host = new FakeHost();
	const controller = new FakeController();
	for (const ocrEngine of ["auto", "apple", "tesseract"] as const) {
		host.ocr = { ocrEngine, splitColumns: false };
		await runAddTextLayer(SOURCE, controller, host);
	}
	assert.deepEqual(host.engineChecks, []);
});

test("failure without short pages, cancellation, or a refused run offers and reports nothing", async () => {
	for (const answer of [result({ code: 1 }), result({ code: null, signal: "SIGTERM" }), null]) {
		const host = new FakeHost();
		const controller = new FakeController();
		controller.answer = answer;
		await runAddTextLayer(SOURCE, controller, host);

		assert.equal(controller.requests.length, 1);
		assert.deepEqual(host.offers, [], JSON.stringify(answer));
		assert.deepEqual(host.notices, [], JSON.stringify(answer));
	}
});

test("a running conversion refuses before any engine check", async () => {
	const host = new FakeHost();
	host.ocr = { ocrEngine: "paddle", splitColumns: false };
	const controller = new FakeController();
	controller.idle = false;
	await runAddTextLayer(SOURCE, controller, host);

	assert.deepEqual(host.engineChecks, []);
	assert.deepEqual(controller.requests, []);
});

test("mobile shows the desktop-only message and starts nothing", async () => {
	const host = new FakeHost();
	host.isDesktop = false;
	const controller = new FakeController();
	await runAddTextLayer(SOURCE, controller, host);

	assert.deepEqual(host.notices, [DESKTOP_ONLY_MESSAGE]);
	assert.deepEqual(controller.requests, []);
});

// ── Page exemptions ────────────────────────────────────────────────────────

test("B5 failure offers the short pages and reruns only after confirmation", async () => {
	const host = new FakeHost();
	const controller = new FakeController();
	controller.queue = [result({ code: 1, shortPages: [1, 5] }), result()];
	await runAddTextLayer(SOURCE, controller, host);

	assert.equal(controller.requests.length, 1);
	assert.deepEqual(host.notices, []);
	assert.equal(host.offers.length, 1);
	const offer = host.offers[0]!;
	assert.deepEqual(offer.source, SOURCE);
	assert.deepEqual(offer.shortPages, [1, 5]);
	assert.equal(offer.prefill, "1,5");
	assert.equal(
		offer.message,
		'OCR Preview: No text layer added to "case-01" — pages 1, 5 have fewer than 50 characters of text. The PDF is unchanged.',
	);

	await offer.confirm(" 1 ");

	assert.equal(controller.requests.length, 2);
	assert.equal(controller.requests[1]!.allowPages, "1");
	assert.deepEqual(controller.requests[1]!.source, SOURCE);
	assert.deepEqual(host.notices, [DONE]);
});

test("exemptions do not hide failures on other pages", async () => {
	const host = new FakeHost();
	const controller = new FakeController();
	controller.queue = [
		result({ code: 1, shortPages: [1, 3] }),
		result({ code: 1, shortPages: [3] }),
		result(),
	];
	await runAddTextLayer(SOURCE, controller, host);
	await host.offers[0]!.confirm("1");

	assert.equal(host.offers.length, 2);
	const second = host.offers[1]!;
	assert.deepEqual(second.shortPages, [3]);
	assert.equal(second.prefill, "1,3");
	assert.equal(
		second.message,
		'OCR Preview: No text layer added to "case-01" — page 3 has fewer than 50 characters of text. The PDF is unchanged.',
	);
	assert.deepEqual(host.notices, []);

	await second.confirm(second.prefill);
	assert.deepEqual(
		controller.requests.map((r) => r.allowPages),
		[undefined, "1", "1,3"],
	);
	assert.deepEqual(host.notices, [DONE]);
});

test("a malformed confirmation reruns nothing", async () => {
	const host = new FakeHost();
	const controller = new FakeController();
	controller.queue = [result({ code: 1, shortPages: [2] })];
	await runAddTextLayer(SOURCE, controller, host);

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
