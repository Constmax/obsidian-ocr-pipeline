import assert from "node:assert/strict";
import { test } from "node:test";

import {
	DEFAULT_OCR_SETTINGS,
	OCR_ENGINES,
	isOcrEngine,
	parseOcrSettings,
} from "../src/ocr-settings.ts";

test("defaults: automatic engine, no column split, 300 DPI limit", () => {
	assert.deepEqual(DEFAULT_OCR_SETTINGS, { ocrEngine: "auto", splitColumns: false, maxDpi: 300 });
});

test("saved values survive a reload through data.json", () => {
	for (const ocrEngine of OCR_ENGINES) {
		for (const splitColumns of [true, false]) {
			const saved = { previewFolder: "_ocr-preview", ocrEngine, splitColumns, maxDpi: 250 };
			const reloaded = JSON.parse(JSON.stringify(saved)) as unknown;
			assert.deepEqual(parseOcrSettings(reloaded), { ocrEngine, splitColumns, maxDpi: 250 });
		}
	}
});

test("data from before these settings existed gets the defaults", () => {
	for (const saved of [{ previewFolder: "_ocr-preview", syncAktiv: true }, {}, null, undefined, "garbage"]) {
		assert.deepEqual(parseOcrSettings(saved), DEFAULT_OCR_SETTINGS, JSON.stringify(saved));
	}
});

test("invalid values fall back field by field", () => {
	for (const ocrEngine of ["easyocr", "Paddle", "", 3, null, ["apple"], {}]) {
		assert.deepEqual(
			parseOcrSettings({ ocrEngine, splitColumns: true }),
			{ ocrEngine: "auto", splitColumns: true, maxDpi: 300 },
			JSON.stringify(ocrEngine),
		);
	}
	for (const splitColumns of ["true", "false", 1, 0, null]) {
		assert.deepEqual(
			parseOcrSettings({ ocrEngine: "tesseract", splitColumns }),
			{ ocrEngine: "tesseract", splitColumns: false, maxDpi: 300 },
			JSON.stringify(splitColumns),
		);
	}
	for (const maxDpi of [-1, 2.5, "300", null, Number.NaN]) {
		assert.deepEqual(
			parseOcrSettings({ ocrEngine: "apple", splitColumns: true, maxDpi }),
			{ ocrEngine: "apple", splitColumns: true, maxDpi: 300 },
			JSON.stringify(maxDpi),
		);
	}
});

test("a DPI limit of 0 (off) is kept (issue #201)", () => {
	assert.equal(parseOcrSettings({ maxDpi: 0 }).maxDpi, 0);
});

test("only the OCR fields are returned", () => {
	assert.deepEqual(
		Object.keys(parseOcrSettings({ ocrEngine: "apple", splitColumns: true, previewFolder: "x" })).sort(),
		["maxDpi", "ocrEngine", "splitColumns"],
	);
});

test("PaddleOCR is a valid stored engine (issue #73)", () => {
	assert.deepEqual([...OCR_ENGINES], ["auto", "apple", "tesseract", "paddle"]);
	assert.equal(isOcrEngine("paddle"), true);
	assert.deepEqual(parseOcrSettings({ ocrEngine: "paddle", splitColumns: false }), {
		ocrEngine: "paddle",
		splitColumns: false,
		maxDpi: 300,
	});
});
