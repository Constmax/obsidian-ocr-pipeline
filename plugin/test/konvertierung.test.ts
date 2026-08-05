import { EventEmitter } from "node:events";
import assert from "node:assert/strict";
import { test } from "node:test";

import {
	pdfKonvertieren,
	type KonvertierenErgebnis,
	type SpawnFunktion,
} from "../src/konvertierung.ts";

/** Kindprozess-Attrappe: EventEmitter mit stdout/stderr, wie `spawn` sie liefert. */
class FakeKind extends EventEmitter {
	stdout = new EventEmitter();
	stderr = new EventEmitter();
	kill = () => true;
}

function spawnAttrappe(aufrufe: Array<{ befehl: string; args: string[] }>, kind: FakeKind): SpawnFunktion {
	return (befehl: string, args: readonly string[]) => {
		aufrufe.push({ befehl, args: [...args] });
		return kind as unknown as ReturnType<SpawnFunktion>;
	};
}

test("ruft pdf2md mit --out auf und liefert Exit-Code 0 mit den Zeilen", async () => {
	const aufrufe: Array<{ befehl: string; args: string[] }> = [];
	const kind = new FakeKind();
	const versprechen = pdfKonvertieren(
		"/vault/raw/fall-01.pdf",
		"/vault/_ocr-vorschau",
		"/Users/test/bin/pdf2md",
		spawnAttrappe(aufrufe, kind),
	);

	kind.stdout.emit("data", "Analysiere fall-01.pdf (Scanseiten @ 150 dpi) ...\n");
	kind.stdout.emit("data", "→ S.1: 12.3 s | 100 Z. Textlayer → OCR\n");
	kind.stderr.emit("data", "\n");
	kind.emit("close", 0);

	const ergebnis = await versprechen;
	assert.deepEqual(aufrufe, [
		{
			befehl: "/Users/test/bin/pdf2md",
			args: ["/vault/raw/fall-01.pdf", "--out", "/vault/_ocr-vorschau"],
		},
	]);
	assert.equal(ergebnis.code, 0);
	assert.deepEqual(ergebnis.stdoutLetzte, [
		"Analysiere fall-01.pdf (Scanseiten @ 150 dpi) ...",
		"→ S.1: 12.3 s | 100 Z. Textlayer → OCR",
	]);
	assert.deepEqual(ergebnis.stderrLetzte, []);
});

test("Exit-Code ungleich 0: stderr-Zeilen kommen mit, nur die letzten 5", async () => {
	const kind = new FakeKind();
	const versprechen = pdfKonvertieren(
		"/vault/raw/kaputt.pdf",
		"/vault/_ocr-vorschau",
		"/Users/test/bin/pdf2md",
		spawnAttrappe([], kind),
	);

	kind.stdout.emit("data", "irgendwas\n");
	for (let i = 1; i <= 7; i++) {
		kind.stderr.emit("data", `Zeile ${i}\n`);
	}
	kind.emit("close", 1);

	const ergebnis = await versprechen;
	assert.equal(ergebnis.code, 1);
	assert.deepEqual(ergebnis.stderrLetzte, ["Zeile 3", "Zeile 4", "Zeile 5", "Zeile 6", "Zeile 7"]);
});

test("Fehler beim Start (Kind wirft 'error') ergibt code null", async () => {
	const kind = new FakeKind();
	const versprechen = pdfKonvertieren(
		"/vault/raw/x.pdf",
		"/vault/_ocr-vorschau",
		"/Users/test/bin/pdf2md",
		spawnAttrappe([], kind),
	);

	kind.emit("error", new Error("ENOENT: pdf2md fehlt"));

	const ergebnis = await versprechen;
	assert.equal(ergebnis.code, null);
	assert.ok(
		ergebnis.stderrLetzte.some((z) => z.includes("ENOENT")),
		`erwartete ENOENT-Meldung, bekam: ${JSON.stringify(ergebnis.stderrLetzte)}`,
	);
});

test("spawnFn wirft synchron: code null, Meldung in stderrLetzte", async () => {
	const ergebnis: KonvertierenErgebnis = await pdfKonvertieren(
		"/vault/raw/x.pdf",
		"/vault/_ocr-vorschau",
		"/Users/test/bin/pdf2md",
		() => {
			throw new Error("spawn nicht verfuegbar");
		},
	);
	assert.equal(ergebnis.code, null);
	assert.deepEqual(ergebnis.stderrLetzte, ["Error: spawn nicht verfuegbar"]);
});
