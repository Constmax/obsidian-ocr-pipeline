import assert from "node:assert/strict";
import { test } from "node:test";

import {
	DEFAULT_OCR_SETTINGS,
	OCR_ENGINES,
	isOcrEngine,
	parseOcrSettings,
} from "../src/ocr-settings.ts";

test("defaults: automatic engine, no column split", () => {
	assert.deepEqual(DEFAULT_OCR_SETTINGS, { ocrEngine: "auto", splitColumns: false });
});

test("saved values survive a reload through data.json", () => {
	for (const ocrEngine of OCR_ENGINES) {
		for (const splitColumns of [true, false]) {
			const saved = { previewFolder: "_ocr-preview", ocrEngine, splitColumns };
			const reloaded = JSON.parse(JSON.stringify(saved)) as unknown;
			assert.deepEqual(parseOcrSettings(reloaded), { ocrEngine, splitColumns });
		}
	}
});

test("data from before these settings existed gets the defaults", () => {
	for (const saved of [{ previewFolder: "_ocr-preview", syncAktiv: true }, {}, null, undefined, "garbage"]) {
		assert.deepEqual(parseOcrSettings(saved), DEFAULT_OCR_SETTINGS, JSON.stringify(saved));
	}
});

test("invalid values fall back field by field", () => {
	for (const ocrEngine of ["paddle", "Apple", "", 3, null, ["apple"], {}]) {
		assert.deepEqual(
			parseOcrSettings({ ocrEngine, splitColumns: true }),
			{ ocrEngine: "auto", splitColumns: true },
			JSON.stringify(ocrEngine),
		);
	}
	for (const splitColumns of ["true", "false", 1, 0, null]) {
		assert.deepEqual(
			parseOcrSettings({ ocrEngine: "tesseract", splitColumns }),
			{ ocrEngine: "tesseract", splitColumns: false },
			JSON.stringify(splitColumns),
		);
	}
});

test("only the two OCR fields are returned", () => {
	assert.deepEqual(
		Object.keys(parseOcrSettings({ ocrEngine: "apple", splitColumns: true, previewFolder: "x" })).sort(),
		["ocrEngine", "splitColumns"],
	);
});

test("PaddleOCR is not offered yet", () => {
	assert.deepEqual([...OCR_ENGINES], ["auto", "apple", "tesseract"]);
	assert.equal(isOcrEngine("paddle"), false);
});
