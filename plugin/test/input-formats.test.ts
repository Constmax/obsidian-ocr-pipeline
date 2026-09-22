// Which vault files Stage 2 accepts (Issue #100).

import assert from "node:assert/strict";
import test from "node:test";

import {
	CONVERTIBLE_EXTENSIONS,
	isConvertible,
	isImageSource,
} from "../src/input-formats.ts";

test("PDFs and the image formats fitz opens are convertible", () => {
	for (const extension of ["pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp"]) {
		assert.equal(isConvertible({ extension }), true, extension);
	}
});

test("extensions are matched case-insensitively", () => {
	assert.equal(isConvertible({ extension: "PNG" }), true);
	assert.equal(isImageSource({ extension: "JPG" }), true);
});

test("formats fitz cannot open are not offered", () => {
	for (const extension of ["webp", "heic", "md", "txt", "docx", ""]) {
		assert.equal(isConvertible({ extension }), false, extension);
	}
});

test("a PDF is not an image source — it renders in the comparison column", () => {
	assert.equal(isImageSource({ extension: "pdf" }), false);
	assert.equal(CONVERTIBLE_EXTENSIONS.has("pdf"), true);
});
