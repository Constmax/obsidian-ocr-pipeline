// Zerlegt eine von pdf2md.py erzeugte .md in Seitenbloecke.
//
// Warum ueberhaupt zerlegen: `%%…%%` ist Obsidians Kommentarsyntax und in der
// Leseansicht unsichtbar (pdf2md.py:1612-1613 hat sie genau deshalb gewaehlt).
// Im gerenderten Markdown gibt es am Marker also KEINEN DOM-Knoten, an dem sich
// die Scroll-Kopplung verankern liesse. Loesung: am Marker trennen und jeden
// Block in einen eigenen Container rendern — der Container ist der Anker.
//
// Nebenwirkung, positiv: pdf2md.py haengt Fussnotendefinitionen pro Seite an.
// Blockweises Rendern behebt damit die Kollision gleichlautender
// Fussnotennummern ueber Seiten hinweg, die ein Ein-Block-Render erzeugt.
//
// Reines Modul: kein Import aus `obsidian`, damit es ohne laufende App testbar
// ist.

import type { Herkunft, Seitenblock, Vorschau } from "./typen.ts";

/** `%% S. 7 %%` und `%% S. 7 | ocr | zweispaltig, senkrecht @48% %%`.
 *  Der Zusatz ist optional, damit Dateien aus Laeufen vor der
 *  Marker-Erweiterung unveraendert weiter funktionieren. */
const MARKER = /^%%\s*S\.\s*(\d+)\s*(?:\|(.*?))?\s*%%\s*$/;

/** Zeile, die einen Codeblock oeffnet oder schliesst (``` oder ~~~). */
const ZAUN = /^\s{0,3}(`{3,}|~{3,})/;

/** `Quelle: [[raw/ZR/skript.pdf]]` — pdf2md.py:1705. */
const QUELLE_LINK = /^Quelle:\s*\[\[([^\]|]+)(?:\|[^\]]*)?\]\]\s*$/;

const HERKUENFTE: readonly string[] = ["textlayer", "ocr", "diagramm"];

/** Flaches `key: value` aus dem YAML-Frontmatter.
 *
 *  Bewusst kein YAML-Parser: pdf2md.py:1696-1701 schreibt ausschliesslich
 *  flache Skalare. Im Plugin ist ohnehin `metadataCache.getFileCache()` die
 *  bessere Quelle; diese Funktion dient dem Testbarkeit-Fall und als Rueckfall,
 *  wenn der Cache eine Datei noch nicht erfasst hat. */
function frontmatterLesen(zeilen: string[]): {
	daten: Record<string, string>;
	rest: number;
} {
	if (zeilen[0]?.trim() !== "---") return { daten: {}, rest: 0 };
	const daten: Record<string, string> = {};
	for (let i = 1; i < zeilen.length; i++) {
		const zeile = zeilen[i];
		if (zeile === undefined) break;
		if (zeile.trim() === "---") return { daten, rest: i + 1 };
		const doppelpunkt = zeile.indexOf(":");
		if (doppelpunkt <= 0) continue;
		const schluessel = zeile.slice(0, doppelpunkt).trim();
		let wert = zeile.slice(doppelpunkt + 1).trim();
		// pdf2md.py:1696 schreibt `quelle-pdf` unquotiert. Enthaelt der Pfad
		// Anfuehrungszeichen, sind sie hier zu entfernen; ein Pfad mit ':'
		// bricht das Frontmatter ohnehin — dafuer gibt es den Quelle-Link.
		if (
			(wert.startsWith('"') && wert.endsWith('"') && wert.length > 1) ||
			(wert.startsWith("'") && wert.endsWith("'") && wert.length > 1)
		) {
			wert = wert.slice(1, -1);
		}
		daten[schluessel] = wert;
	}
	// Kein schliessendes '---' — kaputtes Frontmatter, nichts uebernehmen.
	return { daten: {}, rest: 0 };
}

function zusatzZerlegen(zusatz: string | undefined): {
	herkunft?: Herkunft;
	layout?: string;
} {
	if (zusatz === undefined) return {};
	const teile = zusatz
		.split("|")
		.map((t) => t.trim())
		.filter((t) => t.length > 0);
	const erstes = teile[0];
	if (erstes === undefined) return {};
	if (!HERKUENFTE.includes(erstes)) {
		// Unbekannter Zusatz: als Layout durchreichen, aber keine Herkunft raten.
		return { layout: teile.join(" | ") };
	}
	const layout = teile.slice(1).join(" | ");
	return {
		herkunft: erstes as Herkunft,
		...(layout.length > 0 ? { layout } : {}),
	};
}

/** Zerlegt den Quelltext einer Vorschau-Datei.
 *
 *  Marker innerhalb eines Codeblocks werden nicht als Seitengrenze gewertet —
 *  OCR-Ausgaben enthalten durchaus eingezaeunte Bloecke. */
export function vorschauParsen(quelltext: string): Vorschau {
	const zeilen = quelltext.split("\n");
	const { daten: frontmatter, rest } = frontmatterLesen(zeilen);

	const bloecke: Seitenblock[] = [];
	const vorspannZeilen: string[] = [];
	let quelleAusLink: string | null = null;

	let aktuell: Seitenblock | null = null;
	let sammlung: string[] = [];
	let inZaun = false;
	let zaunZeichen = "";

	const abschliessen = () => {
		if (aktuell === null) return;
		aktuell.markdown = sammlung.join("\n").trim();
		bloecke.push(aktuell);
		aktuell = null;
		sammlung = [];
	};

	for (let i = rest; i < zeilen.length; i++) {
		const zeile = zeilen[i];
		if (zeile === undefined) continue;

		const zaun = ZAUN.exec(zeile);
		if (zaun !== null && zaun[1] !== undefined) {
			if (!inZaun) {
				inZaun = true;
				zaunZeichen = zaun[1][0] ?? "`";
			} else if (zaun[1][0] === zaunZeichen) {
				inZaun = false;
			}
		}

		const treffer = inZaun ? null : MARKER.exec(zeile);
		if (treffer !== null && treffer[1] !== undefined) {
			abschliessen();
			aktuell = {
				nr: Number.parseInt(treffer[1], 10),
				...zusatzZerlegen(treffer[2]),
				markdown: "",
			};
			continue;
		}

		if (aktuell === null) {
			const link = QUELLE_LINK.exec(zeile);
			if (link !== null && link[1] !== undefined && quelleAusLink === null) {
				quelleAusLink = link[1].trim();
				continue; // Die Quelle-Zeile ist Metadatum, kein Vorspann.
			}
			vorspannZeilen.push(zeile);
		} else {
			sammlung.push(zeile);
		}
	}
	abschliessen();

	const quelleAusFrontmatter = frontmatter["quelle-pdf"];
	return {
		frontmatter,
		quellePdf:
			quelleAusFrontmatter !== undefined && quelleAusFrontmatter.length > 0
				? quelleAusFrontmatter
				: quelleAusLink,
		vorspann: vorspannZeilen.join("\n").trim(),
		bloecke,
	};
}

/** Zahl aus dem Frontmatter, `null` wenn nicht vorhanden oder keine Zahl. */
export function zahlAusFrontmatter(
	frontmatter: Record<string, unknown>,
	schluessel: string,
): number | null {
	const wert = frontmatter[schluessel];
	if (typeof wert === "number") return Number.isFinite(wert) ? wert : null;
	if (typeof wert !== "string") return null;
	const zahl = Number.parseInt(wert.trim(), 10);
	return Number.isFinite(zahl) ? zahl : null;
}

/** Text aus dem Frontmatter, `null` wenn leer oder nicht vorhanden. */
export function textAusFrontmatter(
	frontmatter: Record<string, unknown>,
	schluessel: string,
): string | null {
	const wert = frontmatter[schluessel];
	if (typeof wert !== "string") return null;
	const getrimmt = wert.trim();
	return getrimmt.length > 0 ? getrimmt : null;
}
