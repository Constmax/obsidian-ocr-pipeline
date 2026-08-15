import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
	vorschauParsen,
	zahlAusFrontmatter,
	textAusFrontmatter,
} from "../src/vorschau-parser.ts";

const HIER = dirname(fileURLToPath(import.meta.url));
const FIXTURE = readFileSync(
	join(HIER, "fixtures", "beispiel-vorschau.md"),
	"utf-8",
);

test("Frontmatter wird flach gelesen", () => {
	const v = vorschauParsen(FIXTURE);
	assert.equal(v.frontmatter["titel"], "Verwaltungsrecht AT Fall 8");
	assert.equal(v.frontmatter["seiten"], "3");
	assert.equal(v.frontmatter["seiten-ocr"], "1");
	assert.equal(v.frontmatter["ocr-datum"], "2026-07-30");
});

test("quelle-pdf kommt aus dem Frontmatter", () => {
	const v = vorschauParsen(FIXTURE);
	assert.equal(v.quellePdf, "raw/VwR/Verwaltungsrecht AT Fall 8.pdf");
});

test("drei Seitenbloecke mit richtigen Nummern", () => {
	const v = vorschauParsen(FIXTURE);
	assert.deepEqual(
		v.bloecke.map((b) => b.nr),
		[1, 2, 3],
	);
});

test("Herkunft wird aus dem Marker-Zusatz gelesen", () => {
	const v = vorschauParsen(FIXTURE);
	assert.equal(v.bloecke[0]?.herkunft, "textlayer");

	assert.equal(v.bloecke[1]?.herkunft, "ocr");
	assert.equal(v.bloecke[2]?.herkunft, "diagramm");
});

test("Marker ohne Zusatz bleibt herkunft undefined, wird NICHT geraten", () => {
	const v = vorschauParsen("%% S. 1 %%\n\nText\n");
	assert.equal(v.bloecke[0]?.herkunft, undefined);
	assert.equal(v.bloecke[0]?.layout, undefined);
});

test("„None“ aus alten pdf2md-Laeufen ist kein Layout", () => {
	// pdf2md.py schrieb vor dem Fix in seite_verarbeiten() `%% S. 1 | None %%`.
	// Ungefiltert stuende im Seitenkopf woertlich „None".
	const v = vorschauParsen("%% S. 1 | None %%\n\nText\n");
	assert.equal(v.bloecke[0]?.layout, undefined);
	assert.equal(v.bloecke[0]?.herkunft, undefined);

	const mitHerkunft = vorschauParsen("%% S. 2 | ocr | None %%\n\nText\n");
	assert.equal(mitHerkunft.bloecke[0]?.herkunft, "ocr");
	assert.equal(mitHerkunft.bloecke[0]?.layout, undefined);
});

test("Layout-Zusatz wird durchgereicht", () => {
	const v = vorschauParsen(FIXTURE);
	assert.equal(v.bloecke[1]?.layout, "zweispaltig, senkrecht @48%");
	assert.equal(v.bloecke[2]?.layout, undefined);
});

test("Marker in einem Codeblock ist keine Seitengrenze", () => {
	const v = vorschauParsen(FIXTURE);
	assert.equal(v.bloecke.length, 3, "S. 99 im Codeblock darf nicht trennen");
	const zweite = v.bloecke[1]?.markdown ?? "";
	assert.ok(zweite.includes("%% S. 99 %%"), "Marker bleibt Blockinhalt");
	assert.ok(zweite.includes("Nach dem Codeblock geht Seite 2 weiter."));
});

test("Blockgrenzen: Marker selbst ist nicht Teil des Inhalts", () => {
	const v = vorschauParsen(FIXTURE);
	assert.ok(v.bloecke[0]?.markdown.startsWith("**A. Zulässigkeit"));
	assert.ok(!v.bloecke[0]?.markdown.includes("%% S. 1 %%"));
	assert.ok(v.bloecke[2]?.markdown.startsWith("![[Verwaltungsrecht-AT"));
});

test("Fussnoten bleiben in ihrem Block — genau darum wird blockweise gerendert", () => {
	const v = vorschauParsen(FIXTURE);
	assert.ok(v.bloecke[0]?.markdown.includes("[^1]: § 40 Abs. 1 S. 1 VwGO."));
	assert.ok(v.bloecke[1]?.markdown.includes("[^1]: BVerwGE 100, 83."));
});

test("Quelle-Zeile landet nicht im Vorspann", () => {
	const v = vorschauParsen(FIXTURE);
	assert.equal(v.vorspann, "");
});

test("Quelle-Link traegt, wenn quelle-pdf im Frontmatter fehlt", () => {
	const ohne = [
		"---",
		"titel: Test",
		"seiten: 1",
		"---",
		"",
		"Quelle: [[raw/ZR/skript.pdf]]",
		"",
		"%% S. 1 %%",
		"",
		"Text.",
		"",
	].join("\n");
	const v = vorschauParsen(ohne);
	assert.equal(v.quellePdf, "raw/ZR/skript.pdf");
});

test("Datei ohne Frontmatter wird trotzdem zerlegt", () => {
	const v = vorschauParsen("%% S. 1 %%\n\nEins\n\n%% S. 2 %%\n\nZwei\n");
	assert.equal(v.bloecke.length, 2);
	assert.equal(v.bloecke[1]?.markdown, "Zwei");
	assert.equal(v.quellePdf, null);
});

test("Kaputtes Frontmatter ohne schliessendes --- wird verworfen, nicht geraten", () => {
	const v = vorschauParsen("---\ntitel: Kaputt\n\n%% S. 1 %%\n\nText\n");
	assert.deepEqual(v.frontmatter, {});
});

test("unbekannter Marker-Zusatz wird als Layout gefuehrt, nicht als Herkunft", () => {
	const v = vorschauParsen("%% S. 4 | irgendwas %%\n\nText\n");
	assert.equal(v.bloecke[0]?.herkunft, undefined);
	assert.equal(v.bloecke[0]?.layout, "irgendwas");
});

test("Marker mit Leerraumvarianten matcht", () => {
	const v = vorschauParsen("%%S. 5%%\n\nA\n\n%%   S.  6   |  ocr  %%\n\nB\n");
	assert.deepEqual(
		v.bloecke.map((b) => b.nr),
		[5, 6],
	);
	assert.equal(v.bloecke[1]?.herkunft, "ocr");
});

test("pdf2md-Format seit der Marker-Erweiterung: textlayer-Form", () => {
	const v = vorschauParsen("%% S. 1 | textlayer %%\n\nText\n");
	assert.equal(v.bloecke[0]?.herkunft, "textlayer");
	assert.equal(v.bloecke[0]?.layout, undefined);
});

test("Frontmatter-Helfer lesen Zahlen und Text", () => {
	const fm = { seiten: "14", "seiten-ocr": 9, titel: "  Fall 8 ", leer: "  " };
	assert.equal(zahlAusFrontmatter(fm, "seiten"), 14);
	assert.equal(zahlAusFrontmatter(fm, "seiten-ocr"), 9);
	assert.equal(zahlAusFrontmatter(fm, "fehlt"), null);
	assert.equal(textAusFrontmatter(fm, "titel"), "Fall 8");
	assert.equal(textAusFrontmatter(fm, "leer"), null);
});
