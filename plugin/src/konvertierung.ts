// Konvertierung einer PDF per Kindprozess: ruft das lokale pdf2md-Skript
// (Stufe 2 der Pipeline) mit `--out` auf und sammelt die letzten
// Ausgabezeilen. Bewusst schlank gehalten — Fortschrittsmodal, Abbruch und
// maschinenlesbarer Fortschritt sind Roadmap-Themen und brauchen keinen
// Platz in diesem Modul.

import { spawn, type ChildProcess } from "child_process";

export interface KonvertierenErgebnis {
	/** Exit-Code des Kindprozesses; `null` heisst: Start fehlgeschlagen. */
	code: number | null;
	/** Letzte nicht-leere stdout-Zeilen (hoechstens 5). */
	stdoutLetzte: string[];
	/** Letzte nicht-leere stderr-Zeilen (hoechstens 5). */
	stderrLetzte: string[];
}

/** Wie viele Zeilen pro Strom mitgenommen werden — genug fuer die Fehlerursache. */
const LETZTE_ZEILEN = 5;

/**
 * `spawn` in schlanker Signatur, damit ein Test eine Attrappe einschieben
 * kann. Der echte `spawn` erfuellt sie.
 */
export type SpawnFunktion = (
	befehl: string,
	args: readonly string[],
	optionen?: object,
) => ChildProcess;

function sammle(letzte: string[], zeile: string): void {
	const bereinigt = zeile.trim();
	if (bereinigt.length === 0) return;
	letzte.push(bereinigt);
	if (letzte.length > LETZTE_ZEILEN) letzte.shift();
}

/**
 * Startet `pdf2md <pdf> --out <out>` und loest auf, sobald der Prozess
 * endet (exit, Fehler beim Start oder beim Kind). Der Aufrufer entscheidet
 * anhand von `code` und den Zeilen, was er dem Nutzer zeigt.
 */
export function pdfKonvertieren(
	pdfAbs: string,
	outAbs: string,
	pdf2md: string,
	spawnFn: SpawnFunktion = spawn,
): Promise<KonvertierenErgebnis> {
	return new Promise((erledigt) => {
		let kind: ChildProcess;
		try {
			kind = spawnFn(pdf2md, [pdfAbs, "--out", outAbs], {
				stdio: ["ignore", "pipe", "pipe"],
			});
		} catch (fehler) {
			erledigt({ code: null, stdoutLetzte: [], stderrLetzte: [String(fehler)] });
			return;
		}
		const stdoutLetzte: string[] = [];
		const stderrLetzte: string[] = [];
		kind.stdout?.on("data", (stueck) => {
			for (const zeile of String(stueck).split("\n")) sammle(stdoutLetzte, zeile);
		});
		kind.stderr?.on("data", (stueck) => {
			for (const zeile of String(stueck).split("\n")) sammle(stderrLetzte, zeile);
		});
		kind.on("error", (fehler) =>
			erledigt({ code: null, stdoutLetzte, stderrLetzte: [...stderrLetzte, String(fehler)] }),
		);
		kind.on("close", (code) => erledigt({ code, stdoutLetzte, stderrLetzte }));
	});
}
