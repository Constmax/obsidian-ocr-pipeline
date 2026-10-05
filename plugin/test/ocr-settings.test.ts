import assert from "node:assert/strict";
import { test } from "node:test";

import {
	DEFAULT_OCR_SETTINGS,
	OCR_ENGINES,
	isOcrEngine,
	parseOcrSettings,
} from "../src/ocr-settings.ts";

test("defaults: automatic engine", () => {
	assert.deepEqual(DEFAULT_OCR_SETTINGS, { ocrEngine: "auto" });
});

test("saved values survive a reload through data.json", () => {
	for (const ocrEngine of OCR_ENGINES) {
		const saved = { previewFolder: "_ocr-preview", ocrEngine };
		const reloaded = JSON.parse(JSON.stringify(saved)) as unknown;
		assert.deepEqual(parseOcrSettings(reloaded), { ocrEngine });
	}
});

test("data from before these settings existed gets the defaults", () => {
	for (const saved of [{ previewFolder: "_ocr-preview", syncAktiv: true }, {}, null, undefined, "garbage"]) {
		assert.deepEqual(parseOcrSettings(saved), DEFAULT_OCR_SETTINGS, JSON.stringify(saved));
	}
});

test("an invalid engine falls back to the default", () => {
	for (const ocrEngine of ["easyocr", "Paddle", "", 3, null, ["apple"], {}]) {
		assert.deepEqual(parseOcrSettings({ ocrEngine }), { ocrEngine: "auto" }, JSON.stringify(ocrEngine));
	}
});

test("only the OCR field is returned; settings removed in #180 drop out", () => {
	assert.deepEqual(
		Object.keys(
			parseOcrSettings({ ocrEngine: "apple", splitColumns: true, maxDpi: 300, previewFolder: "x" }),
		),
		["ocrEngine"],
	);
});

test("PaddleOCR is a valid stored engine (issue #73)", () => {
	assert.deepEqual([...OCR_ENGINES], ["auto", "apple", "tesseract", "paddle"]);
	assert.equal(isOcrEngine("paddle"), true);
	assert.deepEqual(parseOcrSettings({ ocrEngine: "paddle" }), { ocrEngine: "paddle" });
});
