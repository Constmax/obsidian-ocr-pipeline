import assert from "node:assert/strict";
import { test } from "node:test";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
	DEFAULT_PDF2MD_SETTINGS,
	expandHome,
	parseDpi,
	parsePdf2mdSettings,
	parseTileFrom,
	pdf2mdExecutable,
	pdf2mdPathProblem,
} from "../src/pdf2md-settings.ts";

test("defaults: PATH search, pdf2md's own dpi and tile threshold", () => {
	assert.deepEqual(DEFAULT_PDF2MD_SETTINGS, { pdf2mdPath: "", pdf2mdDpi: null, pdf2mdTileFrom: null });
});

test("DPI: empty is the default, whole numbers in range are kept, the rest is refused", () => {
	assert.deepEqual(parseDpi(""), { value: null });
	assert.deepEqual(parseDpi("  "), { value: null });
	assert.deepEqual(parseDpi(" 200 "), { value: 200 });
	assert.deepEqual(parseDpi("72"), { value: 72 });
	assert.deepEqual(parseDpi("600"), { value: 600 });
	for (const bad of ["71", "601", "150.5", "abc", "-150", "1e3"]) {
		assert.ok("error" in parseDpi(bad), bad);
	}
});

test("tile threshold: empty is the default, 0 and up are kept, the rest is refused", () => {
	assert.deepEqual(parseTileFrom(""), { value: null });
	assert.deepEqual(parseTileFrom("0"), { value: 0 });
	assert.deepEqual(parseTileFrom("2500"), { value: 2500 });
	for (const bad of ["-1", "2.5", "x"]) {
		assert.ok("error" in parseTileFrom(bad), bad);
	}
});

test("expandHome expands ~ and ~/ only", () => {
	assert.equal(expandHome("~/bin/pdf2md", "/home/u"), "/home/u/bin/pdf2md");
	assert.equal(expandHome("~", "/home/u"), "/home/u");
	assert.equal(expandHome("/opt/pdf2md", "/home/u"), "/opt/pdf2md");
	assert.equal(expandHome("~other/pdf2md", "/home/u"), "~other/pdf2md");
});

test("path check: relative, missing, directory and non-executable paths are named", () => {
	const dir = mkdtempSync(join(tmpdir(), "pdf2md-path-"));
	try {
		const script = join(dir, "pdf2md");
		writeFileSync(script, "#!/bin/sh\n");
		chmodSync(script, 0o644);
		assert.equal(pdf2mdPathProblem("bin/pdf2md"), "Enter an absolute path (or ~/…).");
		assert.equal(pdf2mdPathProblem(join(dir, "missing")), `Not found: ${join(dir, "missing")}`);
		assert.equal(pdf2mdPathProblem(dir), `Not a file: ${dir}`);
		assert.equal(pdf2mdPathProblem(script), `Not executable: ${script}`);
		chmodSync(script, 0o755);
		assert.equal(pdf2mdPathProblem(` ${script} `), null);
		assert.equal(pdf2mdPathProblem("~/pdf2md", dir), null);
	} finally {
		rmSync(dir, { recursive: true, force: true });
	}
});

test("executable: the configured path wins, home-expanded; empty falls back to the search", () => {
	const settings = { ...DEFAULT_PDF2MD_SETTINGS, pdf2mdPath: " ~/tools/pdf2md " };
	assert.equal(pdf2mdExecutable(settings, "/home/u"), "/home/u/tools/pdf2md");
	// Nothing exists under this home, so the search falls back to ~/bin.
	const fallback = pdf2mdExecutable(DEFAULT_PDF2MD_SETTINGS, "/nonexistent-home");
	assert.ok(fallback.endsWith("/pdf2md"), fallback);
});

test("saved values survive a reload; invalid ones fall back field by field", () => {
	const saved = { pdf2mdPath: "/opt/pdf2md", pdf2mdDpi: 200, pdf2mdTileFrom: 0 };
	assert.deepEqual(parsePdf2mdSettings(JSON.parse(JSON.stringify(saved))), saved);
	for (const data of [{}, null, undefined, "garbage", { previewFolder: "_ocr-preview" }]) {
		assert.deepEqual(parsePdf2mdSettings(data), DEFAULT_PDF2MD_SETTINGS, JSON.stringify(data));
	}
	assert.deepEqual(
		parsePdf2mdSettings({ pdf2mdPath: 3, pdf2mdDpi: 5000, pdf2mdTileFrom: -1 }),
		DEFAULT_PDF2MD_SETTINGS,
	);
	assert.deepEqual(
		parsePdf2mdSettings({ pdf2mdPath: "/opt/pdf2md", pdf2mdDpi: "200", pdf2mdTileFrom: 1500 }),
		{ pdf2mdPath: "/opt/pdf2md", pdf2mdDpi: null, pdf2mdTileFrom: 1500 },
	);
});
