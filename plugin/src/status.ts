// review-status.json: lesen, schreiben, mit dem Dateisystem abgleichen.
//
// GRUNDREGEL: Das Dateisystem gewinnt. Immer.
//
// Die drei Ordner sind das, was der Nutzer im Finder sieht und anfassen kann.
// Das Manifest ist ein Cache mit Anmerkungen obendrauf (`notiz`,
// `geprueft-bis`). Niemals eine Datei bewegen, damit sie zum JSON passt — das
// wuerde einen bewussten Handverschub still rueckgaengig machen, und genau das
// zerstoert das Vertrauen in ein Werkzeug, das Dateien verschiebt.
//
// Folge daraus: das Manifest ist jederzeit loeschbar. Alles ausser `notiz` und
// `geprueft-bis` baut sich aus der Ordnerlage und dem Frontmatter neu auf.
//
// Reines Modul: kein Import aus `obsidian`.

import type {
	GefundeneDatei,
	Ordnerlage,
	Status,
	StatusEintrag,
	StatusManifest,
} from "./typen.ts";
import { textAusFrontmatter, zahlAusFrontmatter } from "./vorschau-parser.ts";

export function leeresManifest(jetzt: string): StatusManifest {
	return { version: 1, aktualisiert: jetzt, eintraege: {} };
}

const STATUS_WERTE: readonly Status[] = [
	"offen",
	"akzeptiert",
	"abgelehnt",
	"neu-erzeugt",
	"uebernommen",
];

function istStatus(wert: unknown): wert is Status {
	return typeof wert === "string" && (STATUS_WERTE as string[]).includes(wert);
}

function textOderNull(wert: unknown): string | null {
	return typeof wert === "string" && wert.length > 0 ? wert : null;
}

function zahlOderNull(wert: unknown): number | null {
	return typeof wert === "number" && Number.isFinite(wert) ? wert : null;
}

/** Liest das Manifest defensiv: unbekannte oder kaputte Felder werden auf
 *  sichere Werte gesetzt statt den ganzen Lauf scheitern zu lassen. Bei einem
 *  echten Parse-Fehler wirft die Funktion — der Aufrufer benennt die Datei dann
 *  nach `.kaputt` um und baut aus dem Dateisystem neu auf. */
export function manifestLesen(text: string, jetzt: string): StatusManifest {
	const roh: unknown = JSON.parse(text);
	if (typeof roh !== "object" || roh === null) return leeresManifest(jetzt);
	const obj = roh as Record<string, unknown>;
	const eintraegeRoh = obj["eintraege"];
	const eintraege: Record<string, StatusEintrag> = {};
	if (typeof eintraegeRoh === "object" && eintraegeRoh !== null) {
		for (const [name, wertRoh] of Object.entries(
			eintraegeRoh as Record<string, unknown>,
		)) {
			if (typeof wertRoh !== "object" || wertRoh === null) continue;
			const e = wertRoh as Record<string, unknown>;
			const vorherRoh = e["vorher"];
			const vorher =
				typeof vorherRoh === "object" && vorherRoh !== null
					? (vorherRoh as Record<string, unknown>)
					: null;
			eintraege[name] = {
				status: istStatus(e["status"]) ? e["status"] : "offen",
				pfad: textOderNull(e["pfad"]) ?? "",
				"quelle-pdf": textOderNull(e["quelle-pdf"]),
				"quelle-pdf-manuell": textOderNull(e["quelle-pdf-manuell"]),
				seiten: zahlOderNull(e["seiten"]),
				"seiten-ocr": zahlOderNull(e["seiten-ocr"]),
				"seiten-diagramm": zahlOderNull(e["seiten-diagramm"]),
				"ocr-datum": textOderNull(e["ocr-datum"]),
				entschieden: textOderNull(e["entschieden"]),
				"geprueft-bis": zahlOderNull(e["geprueft-bis"]),
				notiz: textOderNull(e["notiz"]),
				vorher:
					vorher !== null && istStatus(vorher["status"])
						? {
								status: vorher["status"],
								entschieden: textOderNull(vorher["entschieden"]),
								"ocr-datum": textOderNull(vorher["ocr-datum"]),
							}
						: null,
			};
		}
	}
	return {
		version: 1,
		aktualisiert: textOderNull(obj["aktualisiert"]) ?? jetzt,
		eintraege,
	};
}

export function manifestSchreiben(manifest: StatusManifest): string {
	// Stabile Schluesselreihenfolge, damit ein Diff des JSON lesbar bleibt.
	const sortiert: Record<string, StatusEintrag> = {};
	for (const name of Object.keys(manifest.eintraege).sort()) {
		const eintrag = manifest.eintraege[name];
		if (eintrag !== undefined) sortiert[name] = eintrag;
	}
	return `${JSON.stringify({ ...manifest, eintraege: sortiert }, null, 2)}\n`;
}

function neuerEintrag(
	datei: GefundeneDatei,
	lage: Ordnerlage,
	jetzt: string,
): StatusEintrag {
	const fm = datei.frontmatter ?? {};
	return {
		status: lage,
		pfad: datei.pfad,
		"quelle-pdf": textAusFrontmatter(fm, "quelle-pdf"),
		"quelle-pdf-manuell": null,
		seiten: zahlAusFrontmatter(fm, "seiten"),
		"seiten-ocr": zahlAusFrontmatter(fm, "seiten-ocr"),
		"seiten-diagramm": zahlAusFrontmatter(fm, "seiten-diagramm"),
		"ocr-datum": textAusFrontmatter(fm, "ocr-datum"),
		// Ein Eintrag, den erst der Abgleich anlegt, hat keine Entscheidung
		// dieses Werkzeugs hinter sich — auch wenn die Datei bereits in
		// _akzeptiert/ liegt. Kein Zeitpunkt erfinden.
		entschieden: null,
		"geprueft-bis": null,
		notiz: null,
		vorher: null,
	};
}

/** `ocr-datum` der ALTEN Fassung bei einer Ersetzung (Regel 6): erst das
 *  Frontmatter der Datei selbst — deren Wahrheit —, dann die Erinnerung an
 *  den Zustand vor der Neukonvertierung. Der Eintrag einer Neukonvertierung
 *  traegt inzwischen das NEUE Datum; das waere der falsche Dateiname. */
export function altesDatumAus(
	eintrag: StatusEintrag | undefined,
	frontmatter: Record<string, unknown>,
): string {
	return (
		textAusFrontmatter(frontmatter, "ocr-datum") ??
		eintrag?.vorher?.["ocr-datum"] ??
		"alt"
	);
}

export interface AbgleichErgebnis {
	manifest: StatusManifest;
	/** Namen, deren Status aus der Ordnerlage korrigiert wurde (Regel 2). */
	korrigiert: string[];
	/** Namen, die als Neukonvertierung erkannt wurden (Regel 6). */
	neuErzeugt: string[];
	/** Namen, deren Cache-Zeile verworfen wurde (Regel 4). */
	entfernt: string[];
}

/**
 * Gleicht Manifest und Dateisystem ab.
 *
 * @param dateien   Alle .md aus den drei Ordnern. Der Aufrufer muss mit
 *                  EXAKTEM Elternpfad filtern, nicht mit `startsWith` —
 *                  `_akzeptiert` liegt innerhalb von `_ocr-vorschau`, ein
 *                  Praefixtest listete angenommene Dateien als offen.
 * @param existiertImVault  Prueft, ob ein Pfad ausserhalb der drei Ordner noch
 *                  im Vault liegt. Damit unterscheidet Regel 4 „ins Wiki
 *                  uebernommen" von „geloescht".
 */
export function abgleichen(
	dateien: readonly GefundeneDatei[],
	vorher: StatusManifest,
	jetzt: string,
	existiertImVault: (pfad: string) => boolean = () => false,
): AbgleichErgebnis {
	const korrigiert: string[] = [];
	const neuErzeugt: string[] = [];
	const entfernt: string[] = [];

	// Nach Basename gruppieren: derselbe Name kann in zwei Ordnern liegen, und
	// genau das ist der Neukonvertierungsfall (Regel 5/6).
	const nachName = new Map<string, GefundeneDatei[]>();
	for (const datei of dateien) {
		const liste = nachName.get(datei.name);
		if (liste === undefined) nachName.set(datei.name, [datei]);
		else liste.push(datei);
	}

	const eintraege: Record<string, StatusEintrag> = {};

	for (const [name, gefunden] of nachName) {
		// Die Datei im offenen Ordner ist immer die frischeste: pdf2md.py:1706
		// schreibt ausschliesslich nach <out>/<stem>.md und kennt die
		// Unterordner nicht.
		const offene = gefunden.find((d) => d.lage === "offen");
		const entschiedene = gefunden.find((d) => d.lage !== "offen");
		const massgeblich = offene ?? entschiedene;
		if (massgeblich === undefined) continue;

		const alt = vorher.eintraege[name];
		if (alt === undefined) {
			// Regel 3 — Datei ohne Eintrag.
			eintraege[name] = neuerEintrag(massgeblich, massgeblich.lage, jetzt);
			continue;
		}

		const fm = massgeblich.frontmatter ?? {};
		const datumJetzt = textAusFrontmatter(fm, "ocr-datum");
		const eintrag: StatusEintrag = {
			...alt,
			pfad: massgeblich.pfad,
			"quelle-pdf": textAusFrontmatter(fm, "quelle-pdf") ?? alt["quelle-pdf"],
			seiten: zahlAusFrontmatter(fm, "seiten") ?? alt.seiten,
			"seiten-ocr": zahlAusFrontmatter(fm, "seiten-ocr") ?? alt["seiten-ocr"],
			"seiten-diagramm":
				zahlAusFrontmatter(fm, "seiten-diagramm") ?? alt["seiten-diagramm"],
			"ocr-datum": datumJetzt ?? alt["ocr-datum"],
		};

		const warEntschieden =
			alt.status === "akzeptiert" || alt.status === "abgelehnt";

		// Regel 5/6 — Neukonvertierung einer bereits entschiedenen Datei.
		// Zwei Signale, jedes fuer sich ausreichend:
		//   (a) derselbe Name liegt gleichzeitig offen UND entschieden vor,
		//   (b) das ocr-datum der offenen Datei weicht vom protokollierten ab.
		// Ohne (b) waere ein Handverschub zurueck in den offenen Ordner nicht
		// von einer Neukonvertierung zu unterscheiden.
		const zweiFassungen = offene !== undefined && entschiedene !== undefined;
		const anderesDatum =
			offene !== undefined &&
			datumJetzt !== null &&
			alt["ocr-datum"] !== null &&
			datumJetzt !== alt["ocr-datum"];

		if (warEntschieden && (zweiFassungen || anderesDatum)) {
			eintrag.status = "neu-erzeugt";
			eintrag.entschieden = null;
			eintrag.vorher = {
				status: alt.status,
				entschieden: alt.entschieden,
				"ocr-datum": alt["ocr-datum"],
			};
			neuErzeugt.push(name);
			eintraege[name] = eintrag;
			continue;
		}

		// Regel 2 — Ordnerlage weicht vom Status ab: die Ordnerlage gewinnt.
		// `neu-erzeugt` im offenen Ordner ist kein Widerspruch, sondern ein noch
		// nicht quittierter Zustand und bleibt erhalten.
		const passt =
			alt.status === massgeblich.lage ||
			(alt.status === "neu-erzeugt" && massgeblich.lage === "offen");
		if (!passt) {
			eintrag.status = massgeblich.lage;
			// Zurueck auf offen heisst: die Entscheidung ist zurueckgenommen.
			if (massgeblich.lage === "offen") eintrag.entschieden = null;
			korrigiert.push(name);
		}
		eintraege[name] = eintrag;
	}

	// Regel 4 — Eintrag ohne Datei in den drei Ordnern.
	//
	// Damit diese Regel „ins Wiki uebernommen" ueberhaupt sehen kann, muss `pfad`
	// einem Verschieben AUS den drei Ordnern heraus folgen. Das leistet
	// `Bestand.pfadNachziehen` am `rename`-Ereignis. Ohne das zeigte `pfad` immer
	// noch in den Vorschau-Ordner, `existiertImVault` waere dort stets falsch, und
	// jede Uebernahme landete faelschlich in `entfernt` — mitsamt `notiz` und
	// `geprueft-bis`, die das Manifest als einziges nicht wiederherstellen kann.
	for (const [name, alt] of Object.entries(vorher.eintraege)) {
		if (nachName.has(name)) continue;
		if (alt.pfad.length > 0 && existiertImVault(alt.pfad)) {
			// Liegt woanders im Vault — vermutlich bewusst ins Wiki uebernommen.
			// Eintrag behalten (als Gedaechtnis), aber nicht mehr listen.
			eintraege[name] = { ...alt, status: "uebernommen" };
		} else {
			entfernt.push(name);
		}
	}

	return {
		manifest: { version: 1, aktualisiert: jetzt, eintraege },
		korrigiert,
		neuErzeugt,
		entfernt,
	};
}

/** Trägt eine Entscheidung ein. Das Verschieben selbst macht der Aufrufer —
 *  diese Funktion kennt kein Dateisystem. */
export function entscheidungEintragen(
	manifest: StatusManifest,
	name: string,
	status: Ordnerlage,
	neuerPfad: string,
	jetzt: string,
): StatusManifest {
	const alt = manifest.eintraege[name];
	if (alt === undefined) return manifest;
	return {
		...manifest,
		aktualisiert: jetzt,
		eintraege: {
			...manifest.eintraege,
			[name]: {
				...alt,
				status,
				pfad: neuerPfad,
				entschieden: status === "offen" ? null : jetzt,
				// `vorher` ist die Erinnerung an die Entscheidung VOR einer
				// Neukonvertierung. Ist neu entschieden, ist sie beantwortet und
				// gehoert weg — sonst zeigt die Seitenleiste spaeter ein
				// „Vorher …" zu einem Zustand, den es nicht mehr gibt.
				vorher: null,
			},
		},
	};
}

/** Ordnerlage → Zielordner. Eine Stelle, damit Ansicht und Aktionen sich nicht
 *  auseinanderentwickeln. */
export function zielordner(
	lage: Ordnerlage,
	einstellungen: {
		vorschauOrdner: string;
		akzeptiertOrdner: string;
		abgelehntOrdner: string;
	},
): string {
	if (lage === "akzeptiert") return einstellungen.akzeptiertOrdner;
	if (lage === "abgelehnt") return einstellungen.abgelehntOrdner;
	return einstellungen.vorschauOrdner;
}
