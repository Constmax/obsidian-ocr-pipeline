import assert from "node:assert/strict";
import { test } from "node:test";

import { LEGACY_FOLDERS, legacyStart } from "../src/legacy-install.ts";

const saved = { previewFolder: "_ocr-vorschau", ocrEngine: "tesseract" };

test("settings saved under the legacy plugin id are carried over", () => {
	assert.deepEqual(legacyStart(saved, { legacyFolder: true, currentFolder: false }), saved);
	// Even when the new default folder exists: the user's own choice wins.
	assert.deepEqual(legacyStart(saved, { legacyFolder: false, currentFolder: true }), saved);
});

test("without legacy settings, an existing legacy preview folder is kept", () => {
	assert.deepEqual(
		legacyStart(null, { legacyFolder: true, currentFolder: false }),
		LEGACY_FOLDERS,
	);
	assert.deepEqual(LEGACY_FOLDERS, {
		previewFolder: "_ocr-vorschau",
		acceptedFolder: "_ocr-vorschau/_akzeptiert",
		rejectedFolder: "_ocr-vorschau/_abgelehnt",
		statusFile: "_ocr-vorschau/review-status.json",
	});
});

test("a vault already using the new folder, or neither, gets the defaults", () => {
	assert.equal(legacyStart(null, { legacyFolder: true, currentFolder: true }), null);
	assert.equal(legacyStart(null, { legacyFolder: false, currentFolder: false }), null);
});

test("unreadable legacy data counts as none", () => {
	for (const garbage of [undefined, "garbage", 42, [], ["x"]]) {
		assert.equal(
			legacyStart(garbage, { legacyFolder: false, currentFolder: false }),
			null,
			JSON.stringify(garbage),
		);
	}
});
