// Konvertierung einer PDF per Kindprozess: ruft das lokale pdf2md-Skript
// (Stufe 2 der Pipeline) mit `--out` auf und sammelt die letzten
// Ausgabezeilen. Maschinlesbarer Fortschritt steht als `--fortschritt`-Flag
// zur Verfügung; das Plugin kann per `onFortschritt`-Callback daraufzugreifen.

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

/**
 * Maschinenlesbares Fortschrittsereignis, wie pdf2md es als JSON-Zeile auf
 * stderr schreibt (siehe `_fortschritt` in pdf2md.py). Diskriminierte Union
 * ueber `typ`, damit Aufrufer mit `ereignis.typ === "seite"` schmaler werden.
 */
export type FortschrittsEreignis =
	| { typ: "start"; datei: string; seiten: number; dpi: number }
	| {
			typ: "seite";
			nr: number;
			von: number;
			sekunden: number;
			herkunft: string;
			entgleist: boolean;
			grund?: string;
	  }
	| { typ: "fertig"; ziel: string; sekunden: number; entgleist: number };

/** Steuerung fuer einen Lauf. */
export interface KonvertierenSteuerung {
	/** Haengt der Prozess laenger als `timeoutMs`, wird er gekillt und der
	 *  Lauf mit `timeout: true` aufgeloest. Wird das Kind zur Laufzeit an
	 *  diese Stelle gemeldet (fuer Abbruch beim Plugin-Unload). */
	timeoutMs?: number;
	/** Nur diese Seiten konvertieren (z.B. "1,3-5,8"). Leer oder undefined
	 *  = alle Seiten. */
	seiten?: string;
	onKind?: (kind: ChildProcess) => void;
	onFortschritt?: (ereignis: FortschrittsEreignis) => void;
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
 * Versucht, eine Zeile als JSON-Fortschrittsereignis zu lesen. Liefert das
 * getypte Ereignis oder `null` fuer jede Zeile, die kein gueltiges
 * Fortschrittsereignis ist (kein JSON, unbekannter `typ`, fehlende Felder).
 * Die Felder werden einzeln geprueft, damit fremde JSON-Zeilen (z. B. von
 * Drittwerkzeugen) nicht als Ereignis durchrutschen.
 */
function fortSchrittsEreignis(zeile: string): FortschrittsEreignis | null {
	let obj: unknown;
	try {
		obj = JSON.parse(zeile) as unknown;
	} catch {
		return null;
	}
	if (typeof obj !== "object" || obj === null) return null;
	const e = obj as Record<string, unknown>;
	if (typeof e.typ !== "string") return null;
	switch (e.typ) {
		case "start":
			if (typeof e.datei !== "string" || typeof e.seiten !== "number" || typeof e.dpi !== "number") {
				return null;
			}
			return { typ: "start", datei: e.datei, seiten: e.seiten, dpi: e.dpi };
		case "seite":
			if (
				typeof e.nr !== "number" ||
				typeof e.von !== "number" ||
				typeof e.sekunden !== "number" ||
				typeof e.herkunft !== "string" ||
				typeof e.entgleist !== "boolean"
			) {
				return null;
			}
			return {
				typ: "seite",
				nr: e.nr,
				von: e.von,
				sekunden: e.sekunden,
				herkunft: e.herkunft,
				entgleist: e.entgleist,
				...(typeof e.grund === "string" ? { grund: e.grund } : {}),
			};
		case "fertig":
			if (typeof e.ziel !== "string" || typeof e.sekunden !== "number" || typeof e.entgleist !== "number") {
				return null;
			}
			return { typ: "fertig", ziel: e.ziel, sekunden: e.sekunden, entgleist: e.entgleist };
		default:
			return null;
	}
}

/**
 * Puffert einen Datenstrom zeilenweise: `data`-Ereignisse zerschneiden nicht
 * an Zeilengrenzen, eine Zeile kann also ueber mehrere Aufrufe verteilt sein.
 * Der Rest ohne abschliessendes `\n` wartet auf den naechsten Aufruf; `flush`
 * gibt ihn am Stromende (auch ohne trennendes `\n`) noch mit.
 *
 * Wenn `beiZeile` angegeben ist, wird fuer jede vollzuegzeilige Zeile
 * `beiZeile(zeile)` aufgerufen. Liefert sie `false`, wird die Zeile nicht
 * in `letzte` gesammelt (z. B. fuer maschinenlesbaren Fortschritt). Liefert
 * sie `true` oder `beiZeile` ist undefined, wird wie bisher gesammelt.
 */
function zeilenPuffer(letzte: string[], beiZeile?: (zeile: string) => boolean): { schreibe: (stueck: string) => void; flush: () => void } {
	let rest = "";
	return {
		schreibe(stueck: string): void {
			const teile = (rest + stueck).split("\n");
			rest = teile.pop() ?? "";
			for (const zeile of teile) {
				if (!beiZeile || beiZeile(zeile)) sammle(letzte, zeile);
			}
		},
		flush(): void {
			if (rest.length > 0) {
				if (!beiZeile || beiZeile(rest)) sammle(letzte, rest);
			}
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
			const args = [pdf, "--out", out];
			if (steuerung.seiten && steuerung.seiten.length > 0) {
				args.push("--seiten", steuerung.seiten);
			}
			args.push("--fortschritt");
			kind = spawnFn(pdf2md, args, {
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
		const stderrPuffer = zeilenPuffer(stderrLetzte, (zeile) => {
			const ereignis = fortSchrittsEreignis(zeile);
			if (ereignis && steuerung.onFortschritt) {
				steuerung.onFortschritt(ereignis);
				return false; // Fortschritt-Zeile nicht in stderrLetzte aufnehmen
			}
			return true; // sonst wie bisher sammeln
		});
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
