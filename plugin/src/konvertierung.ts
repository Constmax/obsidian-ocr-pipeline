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
 * Puffert einen Datenstrom zeilenweise: `data`-Ereignisse zerschneiden nicht
 * an Zeilengrenzen, eine Zeile kann also ueber mehrere Aufrufe verteilt sein.
 * Der Rest ohne abschliessendes `\n` wartet auf den naechsten Aufruf; `flush`
 * gibt ihn am Stromende (auch ohne trennendes `\n`) noch mit.
 */
function zeilenPuffer(letzte: string[]): { schreibe: (stueck: string) => void; flush: () => void } {
	let rest = "";
	return {
		schreibe(stueck: string): void {
			const teile = (rest + stueck).split("\n");
			rest = teile.pop() ?? "";
			for (const zeile of teile) sammle(letzte, zeile);
		},
		flush(): void {
			if (rest.length > 0) sammle(letzte, rest);
			rest = "";
		},
	};
}

/**
 * Startet `pdf2md <pdf> --out <out>` mit `cwd` als Arbeitsverzeichnis und
 * loest auf, sobald der Prozess endet (exit, Fehler beim Start oder beim
 * Kind). Der Aufrufer entscheidet anhand von `code` und den Zeilen, was er
 * dem Nutzer zeigt.
 *
 * `pdf` und `out` sollten relativ zu `cwd` sein (typischerweise die
 * Vault-Wurzel): pdf2md.py schreibt den PDF-Pfad unveraendert in die
 * erzeugte Notiz (`quelle-pdf`, `Quelle: [[…]]`) — nur ein Vault-relativer
 * Pfad ergibt dort einen aufloesbaren Link.
 */
export function pdfKonvertieren(
	pdf: string,
	out: string,
	pdf2md: string,
	cwd: string,
	spawnFn: SpawnFunktion = spawn,
): Promise<KonvertierenErgebnis> {
	return new Promise((erledigt) => {
		let kind: ChildProcess;
		try {
			kind = spawnFn(pdf2md, [pdf, "--out", out], {
				cwd,
				stdio: ["ignore", "pipe", "pipe"],
			});
		} catch (fehler) {
			erledigt({ code: null, stdoutLetzte: [], stderrLetzte: [String(fehler)] });
			return;
		}
		const stdoutLetzte: string[] = [];
		const stderrLetzte: string[] = [];
		const stdoutPuffer = zeilenPuffer(stdoutLetzte);
		const stderrPuffer = zeilenPuffer(stderrLetzte);
		kind.stdout?.setEncoding("utf8");
		kind.stderr?.setEncoding("utf8");
		kind.stdout?.on("data", (stueck) => stdoutPuffer.schreibe(String(stueck)));
		kind.stderr?.on("data", (stueck) => stderrPuffer.schreibe(String(stueck)));
		kind.on("error", (fehler) => {
			stdoutPuffer.flush();
			stderrPuffer.flush();
			erledigt({ code: null, stdoutLetzte, stderrLetzte: [...stderrLetzte, String(fehler)] });
		});
		kind.on("close", (code) => {
			stdoutPuffer.flush();
			stderrPuffer.flush();
			erledigt({ code, stdoutLetzte, stderrLetzte });
		});
	});
}
