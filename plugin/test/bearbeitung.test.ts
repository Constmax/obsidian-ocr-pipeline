import { test } from "node:test";
import assert from "node:assert/strict";

import { blockErsetzen } from "../src/bearbeitung.ts";
import { vorschauParsen } from "../src/vorschau-parser.ts";

const DATEI = [
	"---",
	"titel: Fall 8",
	"seiten: 3",
	"---",
	"",
	"Quelle: [[raw/VwR/Fall 8.pdf]]",
	"",
	"%% S. 1 | textlayer %%",
	"",
	"Erste Seite.",
	"",
	"%% S. 2 | ocr %%",
	"",
	"Zweite Seite mit Schadensersalz.",
	"",
	"%% S. 3 | diagramm %%",
	"",
	"![[fall-8-s003.png]]",
	"",
].join("\n");

test("ersetzt nur den angesprochenen Block", () => {
	const neu = blockErsetzen(DATEI, 2, "Zweite Seite mit Schadensersatz.");
	assert.notEqual(neu, null);
	const v = vorschauParsen(neu as string);
	assert.deepEqual(
		v.bloecke.map((b) => b.markdown),
		["Erste Seite.", "Zweite Seite mit Schadensersatz.", "![[fall-8-s003.png]]"],
	);
});

test("Marker, Herkunft und Frontmatter bleiben unangetastet", () => {
	const neu = blockErsetzen(DATEI, 2, "Anderer Text.") as string;
	assert.ok(neu.includes("%% S. 2 | ocr %%"));
	assert.ok(neu.startsWith("---\ntitel: Fall 8\n"));
	assert.ok(neu.includes("Quelle: [[raw/VwR/Fall 8.pdf]]"));
	const v = vorschauParsen(neu);
	assert.equal(v.bloecke[1]?.herkunft, "ocr");
	assert.equal(v.quellePdf, "raw/VwR/Fall 8.pdf");
});

test("letzter Block: Datei endet weiterhin mit genau einem Zeilenumbruch", () => {
	const neu = blockErsetzen(DATEI, 3, "Neuer Schluss.") as string;
	assert.ok(neu.endsWith("Neuer Schluss.\n"));
	assert.ok(!neu.endsWith("\n\n"));
});

test("unbekannte Seitennummer schreibt nichts", () => {
	assert.equal(blockErsetzen(DATEI, 9, "Text"), null);
});

test("leerer Text loescht den Blockinhalt, nicht den Marker", () => {
	const neu = blockErsetzen(DATEI, 1, "   ") as string;
	assert.ok(neu.includes("%% S. 1 | textlayer %%"));
	const v = vorschauParsen(neu);
	assert.equal(v.bloecke[0]?.markdown, "");
	assert.equal(v.bloecke.length, 3);
});

test("Marker im Codeblock ist keine Blockgrenze", () => {
	const mitZaun = [
		"%% S. 1 %%",
		"",
		"Vor dem Zaun.",
		"",
		"```",
		"%% S. 99 %%",
		"```",
		"",
		"Nach dem Zaun.",
		"",
		"%% S. 2 %%",
		"",
		"Zweite Seite.",
		"",
	].join("\n");
	// Ohne Zaun-Erkennung endete Block 1 am `%% S. 99 %%` und „Nach dem Zaun."
	// bliebe beim Ersetzen stehen.
	const neu = blockErsetzen(mitZaun, 1, "Ersetzt.") as string;
	const v = vorschauParsen(neu);
	assert.deepEqual(
		v.bloecke.map((b) => b.markdown),
		["Ersetzt.", "Zweite Seite."],
	);
	assert.equal(blockErsetzen(mitZaun, 99, "Text"), null);
});
