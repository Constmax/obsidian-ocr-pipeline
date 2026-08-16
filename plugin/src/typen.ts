// Gemeinsame Typen. Bewusst frei von `obsidian`-Importen, damit die reinen
// Module (vorschau-parser, status) ohne laufende App testbar bleiben.

/** Woher der Text einer Seite stammt. `undefined` heisst: die Datei stammt aus
 *  einem pdf2md-Lauf vor der Marker-Erweiterung — dann wird KEIN Badge gezeigt.
 *  Niemals auf `textlayer` defaulten: ein falsches Badge ist schlimmer als kein
 *  Badge, weil es zum Ueberspringen einer OCR-Seite verleitet. */
export type Herkunft = "textlayer" | "ocr" | "diagramm";

export interface Seitenblock {
	/** Seitennummer aus dem Marker, 1-basiert wie im PDF. */
	nr: number;
	herkunft?: Herkunft;
	/** Freitext hinter der Herkunft, z.B. "zweispaltig, senkrecht @48%". */
	layout?: string;
	/** Die urspruengliche Markerzeile aus der Datei, z.B. "%% S. 1 | textlayer %%". */
	markerZeile?: string;
	/** Blockinhalt ohne den Marker, ohne fuehrende/abschliessende Leerzeilen. */
	markdown: string;
}

export interface Vorschau {
	frontmatter: Record<string, string>;
	/** Wert von `quelle-pdf` aus dem Frontmatter, sonst der `Quelle: [[…]]`-Link. */
	quellePdf: string | null;
	/** Text vor dem ersten Seitenmarker (Frontmatter und `Quelle:`-Zeile
	 *  ausgenommen). Normalerweise leer. */
	vorspann: string;
	/** Der gesamte Kopfbereich vor dem ersten Seitenmarker im Originalzustand. */
	roherVorspann?: string;
	bloecke: Seitenblock[];
}

export type Status =
	| "offen"
	| "akzeptiert"
	| "abgelehnt"
	| "neu-erzeugt"
	| "uebernommen";

/** Eine Zeile in review-status.json. Schluessel ist der Basename der .md. */
export interface StatusEintrag {
	status: Status;
	/** Zuletzt gesehener Pfad. Nur Diagnose — die Ordnerlage entscheidet. */
	pfad: string;
	"quelle-pdf": string | null;
	/** Von Hand zugeordnetes Original, wenn `quelle-pdf` nicht aufloest.
	 *  Steht hier und nicht im Frontmatter: die .md ist erzeugte Ausgabe. */
	"quelle-pdf-manuell": string | null;
	seiten: number | null;
	"seiten-ocr": number | null;
	"seiten-diagramm": number | null;
	"ocr-datum": string | null;
	/** Feingranularer Erzeugungszeitpunkt (ISO, mit Uhrzeit) — unterscheidet
	 *  Neukonvertierungen am selben Tag, die `ocr-datum` (nur Datum) nicht
	 *  sieht. Null bei Dateien aus Laeufen vor Einfuehrung des Felds. */
	"ocr-zeitpunkt": string | null;
	/** ISO-Zeitpunkt der Entscheidung, null solange offen. */
	entschieden: string | null;
	/** Zuletzt betrachtete Seite — damit ein 40-Seiten-Durchgang fortsetzbar ist. */
	"geprueft-bis": number | null;
	notiz: string | null;
	/** Bei `neu-erzeugt`: die vorherige Entscheidung, damit sie nicht verschwindet. */
	vorher: {
		status: Status;
		entschieden: string | null;
		"ocr-datum": string | null;
	} | null;
}

export interface StatusManifest {
	version: 1;
	aktualisiert: string;
	eintraege: Record<string, StatusEintrag>;
}

/** Welchem der drei Ordner eine Datei aktuell angehoert. */
export type Ordnerlage = "offen" | "akzeptiert" | "abgelehnt";

/** Was der Abgleich vom Dateisystem braucht — bewusst kein TFile, damit die
 *  Abgleichlogik ohne Obsidian testbar bleibt. */
export interface GefundeneDatei {
	name: string;
	pfad: string;
	lage: Ordnerlage;
	/** Frontmatter-Werte, soweit vorhanden (aus dem Metadaten-Cache). */
	frontmatter?: Record<string, unknown>;
}
