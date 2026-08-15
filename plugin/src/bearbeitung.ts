// Einen einzelnen Seitenblock im Quelltext einer Vorschau-Datei ersetzen —
// die Schreibseite zum Lesen in `vorschau-parser.ts`.
//
// Warum nicht die ganze Datei aus den geparsten Bloecken neu bauen: dabei
// gingen Frontmatter-Formatierung, die `Quelle: [[…]]`-Zeile und jede
// Leerzeilen-Eigenart verloren, die der Parser nicht modelliert. Hier wird
// ausschliesslich der Bereich zwischen zwei Markern ausgetauscht; alles andere
// bleibt Byte fuer Byte stehen.
//
// Die Marker-Grammatik kommt aus dem Parser, nicht aus einer zweiten Kopie:
// zwei Regexe fuer dieselbe Zeile laufen frueher oder spaeter auseinander.
//
// Reines Modul: kein Import aus `obsidian`.

import { MARKER, ZAUN } from "./vorschau-parser.ts";

/**
 * Ersetzt den Inhalt des Blocks mit der Seitennummer `nr` durch `neuerText`.
 * Die Markerzeile selbst bleibt unangetastet — Herkunft und Layout sind
 * Befunde des OCR-Laufs, keine Nutzereingabe.
 *
 * Liefert `null`, wenn der Marker nicht vorkommt. Das ist kein Randfall,
 * sondern der Schutz davor, in eine Datei zu schreiben, die sich seit dem
 * Oeffnen der Ansicht geaendert hat: der Aufrufer bricht dann ab, statt einen
 * anderen Block zu ueberschreiben.
 */
export function blockErsetzen(
	quelltext: string,
	nr: number,
	neuerText: string,
): string | null {
	const zeilen = quelltext.split("\n");
	let inZaun = false;
	let zaunZeichen = "";
	let start = -1;
	let ende = zeilen.length;

	for (let i = 0; i < zeilen.length; i++) {
		const zeile = zeilen[i];
		if (zeile === undefined) continue;

		// Marker in einem Codeblock sind Text, keine Seitengrenze — dieselbe
		// Regel wie beim Zerlegen (docs/vorschau-format.md).
		const zaun = ZAUN.exec(zeile);
		if (zaun !== null && zaun[1] !== undefined) {
			if (!inZaun) {
				inZaun = true;
				zaunZeichen = zaun[1][0] ?? "`";
			} else if (zaun[1][0] === zaunZeichen) {
				inZaun = false;
			}
		}
		if (inZaun) continue;

		const treffer = MARKER.exec(zeile);
		if (treffer === null || treffer[1] === undefined) continue;
		if (start < 0) {
			if (Number.parseInt(treffer[1], 10) === nr) start = i;
			continue;
		}
		ende = i;
		break;
	}

	if (start < 0) return null;

	const inhalt = neuerText.trim();
	const neu = [
		...zeilen.slice(0, start + 1),
		"",
		...(inhalt.length > 0 ? inhalt.split("\n") : []),
		"",
		...zeilen.slice(ende),
	];
	return neu.join("\n");
}
