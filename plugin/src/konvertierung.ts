// Konvertierung einer PDF per Kindprozess: ruft das lokale pdf2md-Skript
// (Stufe 2 der Pipeline) mit `--out` auf und sammelt die letzten
// Ausgabezeilen. Bewusst schlank gehalten — Fortschrittsmodal, Abbruch und
// maschinenlesbarer Fortschritt sind Roadmap-Themen und brauchen keinen
// Platz in diesem Modul.

import { spawn, type ChildProcess } from "child_process";

export interface KonvertierenErgebnis {
	/** Exit-Code des Kindprozesses; `null` heisst: nicht ueber einen Code
	 *  beendet (Startfehler, Signal, Zeitueberschreitung). */
	code: number | null;
	/** Beendigungssignal, wenn der Prozess durch ein Signal endete. */
	signal: NodeJS.Signals | null;
	/** true, wenn der Prozess nach `timeoutMs` nicht beendet war und gekillt
	 *  wurde. */
	timeout: boolean;
	/** Letzte nicht-leere stdout-Zeilen (hoechstens 5). */
	stdoutLetzte: string[];
	/** Letzte nicht-leere stderr-Zeilen (hoechstens 5). */
	stderrLetzte: string[];
}

/** Steuerung fuer einen Lauf. */
export interface KonvertierenSteuerung {
	/** Haengt der Prozess laenger als `timeoutMs`, wird er gekillt und der
	 *  Lauf mit `timeout: true` aufgeloest. Wird das Kind zur Laufzeit an
	 *  diese Stelle gemeldet (fuer Abbruch beim Plugin-Unload). */
	timeoutMs?: number;
	onKind?: (kind: ChildProcess) => void;
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
	steuerung: KonvertierenSteuerung = {},
): Promise<KonvertierenErgebnis> {
	return new Promise((erledigt) => {
		let kind: ChildProcess;
		try {
			kind = spawnFn(pdf2md, [pdf, "--out", out], {
				cwd,
				stdio: ["ignore", "pipe", "pipe"],
			});
		} catch (fehler) {
			erledigt({
				code: null,
				signal: null,
				timeout: false,
				stdoutLetzte: [],
				stderrLetzte: [String(fehler)],
			});
			return;
		}
		steuerung.onKind?.(kind);
		const stdoutLetzte: string[] = [];
		const stderrLetzte: string[] = [];
		const stdoutPuffer = zeilenPuffer(stdoutLetzte);
		const stderrPuffer = zeilenPuffer(stderrLetzte);
		kind.stdout?.setEncoding("utf8");
		kind.stderr?.setEncoding("utf8");
		kind.stdout?.on("data", (stueck) => stdoutPuffer.schreibe(String(stueck)));
		kind.stderr?.on("data", (stueck) => stderrPuffer.schreibe(String(stueck)));
		// Haengender Lauf (z. B. blockierter Modell-Download): nicht ewig
		// blockieren — nach `timeoutMs` killen. Bewusst OHNE `unref`: der Timer
		// wird ueber `fertig` geloescht, sobald der Prozess (normal oder per
		// Kill) endet — bis dahin soll er den Event-Loop offen halten, sonst
		// koennte der Prozess vor dem eigentlichen Timeout-Kill beendet werden.
		const timer =
			steuerung.timeoutMs === undefined
				? null
				: setTimeout(() => {
						kind.kill();
						erledigt({
							code: null,
							signal: null,
							timeout: true,
							stdoutLetzte,
							stderrLetzte,
						});
					}, steuerung.timeoutMs);
		const fertig = (ergebnis: KonvertierenErgebnis) => {
			if (timer !== null) clearTimeout(timer);
			erledigt(ergebnis);
		};
		kind.on("error", (fehler) => {
			stdoutPuffer.flush();
			stderrPuffer.flush();
			// Fehlerzeile ueber `sammle`, damit das „hoechstens 5"-Versprechen
			// auch mit dem Fehlerereignis gilt.
			sammle(stderrLetzte, String(fehler));
			fertig({ code: null, signal: null, timeout: false, stdoutLetzte, stderrLetzte });
		});
		kind.on("close", (code, signal) => {
			stdoutPuffer.flush();
			stderrPuffer.flush();
			fertig({ code, signal: signal ?? null, timeout: false, stdoutLetzte, stderrLetzte });
		});
	});
}
