import { EventEmitter } from "node:events";
import assert from "node:assert/strict";
import { test } from "node:test";
import type { ChildProcess } from "node:child_process";

import {
	pdfKonvertieren,
	kindAbbrechen,
	type KonvertierenErgebnis,
	type SpawnFunktion,
} from "../src/konvertierung.ts";

/**
 * Kindprozess-Attrappe: EventEmitter mit stdout/stderr, wie `spawn` sie
 * liefert. `kill` zeichnet die Signale auf; bei SIGKILL (oder bei
 * `endeBeiSigterm`, dann schon auf SIGTERM) beendet sich der Fake selbst
 * und emittiert exit/close wie ein echtes Kind.
 */
class FakeKind extends EventEmitter {
	stdout = Object.assign(new EventEmitter(), { setEncoding: () => {} });
	stderr = Object.assign(new EventEmitter(), { setEncoding: () => {} });
	exitCode: number | null = null;
	signalCode: NodeJS.Signals | null = null;
	signale: NodeJS.Signals[] = [];
	/** Beendet den Fake bei SIGTERM geordnet mit diesem Exit-Code. */
	endeBeiSigterm?: number;
	kill = (signal?: NodeJS.Signals) => {
		this.signale.push(signal ?? "SIGTERM");
		if (signal === "SIGKILL") {
			this.signalCode = "SIGKILL";
			this.emit("exit", null, "SIGKILL");
			this.emit("close", null, "SIGKILL");
		} else if (this.endeBeiSigterm !== undefined) {
			this.exitCode = this.endeBeiSigterm;
			this.emit("exit", this.endeBeiSigterm, null);
			this.emit("close", this.endeBeiSigterm, null);
		}
		return true;
	};
}

function spawnAttrappe(
	aufrufe: Array<{ befehl: string; args: string[]; optionen: unknown }>,
	kind: FakeKind,
): SpawnFunktion {
	return (befehl: string, args: readonly string[], optionen?: object) => {
		aufrufe.push({ befehl, args: [...args], optionen });
		return kind as unknown as ReturnType<SpawnFunktion>;
	};
}

test("ruft pdf2md mit --out und cwd auf und liefert Exit-Code 0 mit den Zeilen", async () => {
	const aufrufe: Array<{ befehl: string; args: string[]; optionen: unknown }> = [];
	const kind = new FakeKind();
	const versprechen = pdfKonvertieren(
		"raw/fall-01.pdf",
		"_ocr-vorschau",
		"/Users/test/bin/pdf2md",
		"/vault",
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
			args: ["raw/fall-01.pdf", "--out", "_ocr-vorschau", "--fortschritt"],
			optionen: { cwd: "/vault", stdio: ["ignore", "pipe", "pipe"] },
		},
	]);
	assert.equal(ergebnis.code, 0);
	assert.equal(ergebnis.signal, null);
	assert.equal(ergebnis.timeout, false);
	assert.deepEqual(ergebnis.stdoutLetzte, [
		"Analysiere fall-01.pdf (Scanseiten @ 150 dpi) ...",
		"→ S.1: 12.3 s | 100 Z. Textlayer → OCR",
	]);
	assert.deepEqual(ergebnis.stderrLetzte, []);
});

test("Zeile ueber zwei data-Ereignisse verteilt zaehlt als eine", async () => {
	const kind = new FakeKind();
	const versprechen = pdfKonvertieren(
		"raw/fall-01.pdf",
		"_ocr-vorschau",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnAttrappe([], kind),
	);

	kind.stdout.emit("data", "→ S.1: 12.3 s | 100 Z. Text");
	kind.stdout.emit("data", "layer → OCR\n");
	kind.stdout.emit("data", "letzte Zeile ohne Zeilenumbruch");
	kind.emit("close", 0);

	const ergebnis = await versprechen;
	assert.deepEqual(ergebnis.stdoutLetzte, [
		"→ S.1: 12.3 s | 100 Z. Textlayer → OCR",
		"letzte Zeile ohne Zeilenumbruch",
	]);
});

test("Exit-Code ungleich 0: stderr-Zeilen kommen mit, nur die letzten 5", async () => {
	const kind = new FakeKind();
	const versprechen = pdfKonvertieren(
		"raw/kaputt.pdf",
		"_ocr-vorschau",
		"/Users/test/bin/pdf2md",
		"/vault",
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
		"raw/x.pdf",
		"_ocr-vorschau",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnAttrappe([], kind),
	);

	kind.emit("error", new Error("ENOENT: pdf2md fehlt"));

	const ergebnis = await versprechen;
	assert.equal(ergebnis.code, null);
	assert.equal(ergebnis.signal, null);
	assert.equal(ergebnis.timeout, false);
	assert.ok(
		ergebnis.stderrLetzte.some((z) => z.includes("ENOENT")),
		`erwartete ENOENT-Meldung, bekam: ${JSON.stringify(ergebnis.stderrLetzte)}`,
	);
});

test("spawnFn wirft synchron: code null, Meldung in stderrLetzte", async () => {
	const ergebnis: KonvertierenErgebnis = await pdfKonvertieren(
		"raw/x.pdf",
		"_ocr-vorschau",
		"/Users/test/bin/pdf2md",
		"/vault",
		() => {
			throw new Error("spawn nicht verfuegbar");
		},
	);
	assert.equal(ergebnis.code, null);
	assert.deepEqual(ergebnis.stderrLetzte, ["Error: spawn nicht verfuegbar"]);
});

test("onKind meldet den Kindprozess", async () => {
	const kind = new FakeKind();
	let gemeldet: unknown = null;
	const versprechen = pdfKonvertieren(
		"raw/fall-01.pdf",
		"_ocr-vorschau",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnAttrappe([], kind),
		{
			onKind: (k) => {
				gemeldet = k;
			},
		},
	);

	kind.emit("close", 0);
	await versprechen;
	assert.equal(gemeldet, kind);
});

test("Timeout: haengendes Kind erst SIGTERM, nach Frist SIGKILL, timeout: true", async () => {
	const kind = new FakeKind();
	const versprechen = pdfKonvertieren(
		"raw/haengt.pdf",
		"_ocr-vorschau",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnAttrappe([], kind),
		{ timeoutMs: 20, abbruchFristMs: 30 },
	);

	kind.stdout.emit("data", "→ S.1: 12.3 s\n");

	const ergebnis = await versprechen;
	assert.deepEqual(kind.signale, ["SIGTERM", "SIGKILL"]);
	assert.equal(ergebnis.timeout, true);
	assert.equal(ergebnis.code, null);
	assert.equal(ergebnis.signal, "SIGKILL");
	assert.deepEqual(ergebnis.stdoutLetzte, ["→ S.1: 12.3 s"]);
});

test("Timeout: Kind beendet auf SIGTERM geordnet (Code 6), kein SIGKILL", async () => {
	const kind = new FakeKind();
	kind.endeBeiSigterm = 6;
	const versprechen = pdfKonvertieren(
		"raw/haengt.pdf",
		"_ocr-vorschau",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnAttrappe([], kind),
		{ timeoutMs: 20, abbruchFristMs: 200 },
	);

	const ergebnis = await versprechen;
	assert.deepEqual(kind.signale, ["SIGTERM"]);
	assert.equal(ergebnis.timeout, true);
	assert.equal(ergebnis.code, 6);
	assert.equal(ergebnis.signal, null);
});

test("kindAbbrechen: haengendes Kind erst SIGTERM, nach Frist SIGKILL", async () => {
	const kind = new FakeKind();

	kindAbbrechen(kind as unknown as ChildProcess, 20);
	assert.deepEqual(kind.signale, ["SIGTERM"]);

	await new Promise((fertig) => setTimeout(fertig, 50));
	assert.deepEqual(kind.signale, ["SIGTERM", "SIGKILL"]);
});

test("kindAbbrechen: beendetes Kind bekommt kein Signal", () => {
	const kind = new FakeKind();
	kind.exitCode = 6;

	kindAbbrechen(kind as unknown as ChildProcess, 20);
	assert.deepEqual(kind.signale, []);
});

test("Prozess durch Signal beendet: Signal wird gemeldet", async () => {
	const kind = new FakeKind();
	const versprechen = pdfKonvertieren(
		"raw/fall-01.pdf",
		"_ocr-vorschau",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnAttrappe([], kind),
	);

	kind.emit("close", null, "SIGTERM");

	const ergebnis = await versprechen;
	assert.equal(ergebnis.code, null);
	assert.equal(ergebnis.signal, "SIGTERM");
});

test("ruft pdf2md mit --seiten auf, wenn seiten gesetzt", async () => {
	const aufrufe: Array<{ befehl: string; args: string[]; optionen: unknown }> = [];
	const kind = new FakeKind();
	const versprechen = pdfKonvertieren(
		"raw/fall-01.pdf",
		"_ocr-vorschau",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnAttrappe(aufrufe, kind),
		{ seiten: "1,3-5" },
	);

	kind.emit("close", 0);
	await versprechen;
	assert.deepEqual(aufrufe[0]!.args, [
		"raw/fall-01.pdf",
		"--out",
		"_ocr-vorschau",
		"--seiten",
		"1,3-5",
		"--fortschritt",
	]);
});

test("ohne seiten: kein --seiten in den Args", async () => {
	const aufrufe: Array<{ befehl: string; args: string[]; optionen: unknown }> = [];
	const kind = new FakeKind();
	const versprechen = pdfKonvertieren(
		"raw/fall-01.pdf",
		"_ocr-vorschau",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnAttrappe(aufrufe, kind),
	);

	kind.emit("close", 0);
	await versprechen;
	assert.deepEqual(aufrufe[0]!.args, [
		"raw/fall-01.pdf",
		"--out",
		"_ocr-vorschau",
		"--fortschritt",
	]);
});

test("leerer seiten-String: kein --seiten in den Args", async () => {
	const aufrufe: Array<{ befehl: string; args: string[]; optionen: unknown }> = [];
	const kind = new FakeKind();
	const versprechen = pdfKonvertieren(
		"raw/fall-01.pdf",
		"_ocr-vorschau",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnAttrappe(aufrufe, kind),
		{ seiten: "" },
	);

	kind.emit("close", 0);
	await versprechen;
	assert.deepEqual(aufrufe[0]!.args, [
		"raw/fall-01.pdf",
		"--out",
		"_ocr-vorschau",
		"--fortschritt",
	]);
});
