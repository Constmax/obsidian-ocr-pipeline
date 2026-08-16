import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
	buildMarker,
	textFromFrontmatter,
	parsePreview,
	buildPreview,
	numberFromFrontmatter,
} from "../src/preview-parser.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE = readFileSync(
	join(HERE, "fixtures", "beispiel-vorschau.md"),
	"utf-8",
);

test("frontmatter is read flat", () => {
	const v = parsePreview(FIXTURE);
	assert.equal(v.frontmatter["titel"], "Verwaltungsrecht AT Fall 8");
	assert.equal(v.frontmatter["seiten"], "3");
	assert.equal(v.frontmatter["seiten-ocr"], "1");
	assert.equal(v.frontmatter["ocr-datum"], "2026-07-30");
});

test("source-pdf comes from frontmatter", () => {
	const v = parsePreview(FIXTURE);
	assert.equal(v.sourcePdf, "raw/VwR/Verwaltungsrecht AT Fall 8.pdf");
});

test("three page blocks with correct numbers", () => {
	const v = parsePreview(FIXTURE);
	assert.deepEqual(
		v.blocks.map((b) => b.pageNumber),
		[1, 2, 3],
	);
});

test("origin is read from marker extra", () => {
	const v = parsePreview(FIXTURE);
	assert.equal(v.blocks[0]?.origin, "textlayer");

	assert.equal(v.blocks[1]?.origin, "ocr");
	assert.equal(v.blocks[2]?.origin, "diagram");
});

test("marker without extra keeps origin undefined, NOT guessed", () => {
	const v = parsePreview("%% p. 1 %%\n\nText\n");
	assert.equal(v.blocks[0]?.origin, undefined);
	assert.equal(v.blocks[0]?.layout, undefined);
});

test("layout extra is passed through", () => {
	const v = parsePreview(FIXTURE);
	assert.equal(v.blocks[1]?.layout, "zweispaltig, senkrecht @48%");
	assert.equal(v.blocks[2]?.layout, undefined);
});

test("marker inside a code block is not page boundary", () => {
	const v = parsePreview(FIXTURE);
	assert.equal(v.blocks.length, 3, "S. 99 in code block must not separate");
	const second = v.blocks[1]?.markdown ?? "";
	assert.ok(second.includes("%% S. 99 %%"), "marker remains block content");
	assert.ok(second.includes("Nach dem Codeblock geht Seite 2 weiter."));
});

test("block boundaries: marker itself is not part of content", () => {
	const v = parsePreview(FIXTURE);
	assert.ok(v.blocks[0]?.markdown.startsWith("**A. Zulässigkeit"));
	assert.ok(!v.blocks[0]?.markdown.includes("%% S. 1 %%"));
	assert.ok(v.blocks[2]?.markdown.startsWith("![[Verwaltungsrecht-AT"));
});

test("footnotes stay in their block — exact reason for blockwise rendering", () => {
	const v = parsePreview(FIXTURE);
	assert.ok(v.blocks[0]?.markdown.includes("[^1]: § 40 Abs. 1 S. 1 VwGO."));
	assert.ok(v.blocks[1]?.markdown.includes("[^1]: BVerwGE 100, 83."));
});

test("source line does not end up in preamble", () => {
	const v = parsePreview(FIXTURE);
	assert.equal(v.preamble, "");
});

test("source link carries over if source-pdf missing in frontmatter", () => {
	const without = [
		"---",
		"title: Test",
		"pages: 1",
		"---",
		"",
		"Source: [[raw/ZR/script.pdf]]",
		"",
		"%% p. 1 %%",
		"",
		"Text.",
		"",
	].join("\n");
	const v = parsePreview(without);
	assert.equal(v.sourcePdf, "raw/ZR/script.pdf");
});

test("file without frontmatter is still parsed", () => {
	const v = parsePreview("%% p. 1 %%\n\nOne\n\n%% p. 2 %%\n\nTwo\n");
	assert.equal(v.blocks.length, 2);
	assert.equal(v.blocks[1]?.markdown, "Two");
	assert.equal(v.sourcePdf, null);
});

test("broken frontmatter without closing --- is discarded, not guessed", () => {
	const v = parsePreview("---\ntitle: Broken\n\n%% p. 1 %%\n\nText\n");
	assert.deepEqual(v.frontmatter, {});
});

test("unknown marker extra is treated as layout, not origin", () => {
	const v = parsePreview("%% p. 4 | something %%\n\nText\n");
	assert.equal(v.blocks[0]?.origin, undefined);
	assert.equal(v.blocks[0]?.layout, "something");
});

test("marker with whitespace variations matches", () => {
	const v = parsePreview("%%S. 5%%\n\nA\n\n%%   p.  6   |  ocr  %%\n\nB\n");
	assert.deepEqual(
		v.blocks.map((b) => b.pageNumber),
		[5, 6],
	);
	assert.equal(v.blocks[1]?.origin, "ocr");
});

test("pdf2md format since marker extension: textlayer form", () => {
	const v = parsePreview("%% p. 1 | textlayer %%\n\nText\n");
	assert.equal(v.blocks[0]?.origin, "textlayer");
	assert.equal(v.blocks[0]?.layout, undefined);
});

test("frontmatter helpers read numbers and text", () => {
	const fm = { pages: "14", "pages-ocr": 9, title: "  Case 8 ", empty: "  " };
	assert.equal(numberFromFrontmatter(fm, "pages"), 14);
	assert.equal(numberFromFrontmatter(fm, "pages-ocr"), 9);
	assert.equal(numberFromFrontmatter(fm, "missing"), null);
	assert.equal(textFromFrontmatter(fm, "title"), "Case 8");
	assert.equal(textFromFrontmatter(fm, "empty"), null);
});

test("buildPreview: fixture is re-serialized losslessly", () => {
	const v = parsePreview(FIXTURE);
	const result = buildPreview(v);
	assert.equal(result, FIXTURE);
});

test("buildPreview: modified page block is correctly included", () => {
	const v = parsePreview(FIXTURE);
	assert.ok(v.blocks[0]);
	v.blocks[0].markdown = "**A. Zulässigkeit der Klage (corrected)**";
	const result = buildPreview(v);
	assert.ok(result.includes("**A. Zulässigkeit der Klage (corrected)**"));
	assert.ok(!result.includes("**A. Zulässigkeit der Klage**\n"));
	assert.ok(result.includes("%% S. 1 | textlayer %%"));
	assert.ok(result.includes("%% S. 2 | ocr | zweispaltig, senkrecht @48% %%"));
});

test("buildMarker creates valid markers with and without extra", () => {
	assert.equal(buildMarker({ pageNumber: 1, markdown: "" }), "%% S. 1 %%");
	assert.equal(
		buildMarker({ pageNumber: 2, origin: "ocr", layout: "two-column", markdown: "" }),
		"%% S. 2 | ocr | two-column %%",
	);
	assert.equal(
		buildMarker({ pageNumber: 3, origin: "textlayer", markdown: "" }),
		"%% S. 3 | textlayer %%",
	);
});

test("buildPreview without rawPreamble builds fallback", () => {
	const result = buildPreview({
		frontmatter: { title: "Test" },
		sourcePdf: "path/to/file.pdf",
		preamble: "Preamble text",
		blocks: [{ pageNumber: 1, origin: "ocr", markdown: "Content" }],
	});
	assert.ok(result.includes("---"));
	assert.ok(result.includes("title: Test"));
	assert.ok(result.includes("Quelle: [[path/to/file.pdf]]"));
	assert.ok(result.includes("Preamble text"));
	assert.ok(result.includes("%% S. 1 | ocr %%\n\nContent"));
});
