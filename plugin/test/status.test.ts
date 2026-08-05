import { test } from "node:test";
import assert from "node:assert/strict";

import {
	abgleichen,
	altesDatumAus,
	entscheidungEintragen,
	leeresManifest,
	manifestLesen,
	manifestSchreiben,
	zielordner,
} from "../src/status.ts";
import type { GefundeneDatei, StatusManifest } from "../src/typen.ts";

const JETZT = "2026-08-05T12:00:00+02:00";

const ORDNER = {
	vorschauOrdner: "_ocr-vorschau",
	akzeptiertOrdner: "_ocr-vorschau/_akzeptiert",
	abgelehntOrdner: "_ocr-vorschau/_abgelehnt",
};

function datei(
	name: string,
	lage: GefundeneDatei["lage"],
	frontmatter: Record<string, unknown> = {},
): GefundeneDatei {
	const ordner = zielordner(lage, ORDNER);
	return { name, pfad: `${ordner}/${name}`, lage, frontmatter };
}

function mitEintrag(
	name: string,
	teil: Partial<StatusManifest["eintraege"][string]>,
): StatusManifest {
	const m = leeresManifest(JETZT);
	m.eintraege[name] = {
		status: "offen",
		pfad: `_ocr-vorschau/${name}`,
		"quelle-pdf": null,
		"quelle-pdf-manuell": null,
		seiten: null,
		"seiten-ocr": null,
		"seiten-diagramm": null,
		"ocr-datum": null,
		entschieden: null,
		"geprueft-bis": null,
		notiz: null,
		vorher: null,
		...teil,
	};
	return m;
}

// ── Regel 1: die startsWith-Falle ───────────────────────────────────────────

test("Regel 1: eine Datei in _akzeptiert zaehlt nicht als offen", () => {
	const e = abgleichen(
		[datei("Fall 8.md", "akzeptiert")],
		leeresManifest(JETZT),
		JETZT,
	);
	assert.equal(e.manifest.eintraege["Fall 8.md"]?.status, "akzeptiert");
	// Der Pfad liegt innerhalb von _ocr-vorschau — ein Praefixtest im Aufrufer
	// wuerde die Datei doppelt sehen. Hier ist dokumentiert, was gelten muss.
	assert.ok(
		e.manifest.eintraege["Fall 8.md"]?.pfad.startsWith("_ocr-vorschau/"),
		"Unterordner liegt bewusst innerhalb des Vorschau-Ordners",
	);
});

// ── Regel 3: Datei ohne Eintrag ─────────────────────────────────────────────

test("Regel 3: neue Datei bekommt einen Eintrag aus dem Frontmatter", () => {
	const e = abgleichen(
		[
			datei("Fall 8.md", "offen", {
				"quelle-pdf": "raw/VwR/Fall 8.pdf",
				seiten: "14",
				"seiten-ocr": "9",
				"seiten-diagramm": "2",
				"ocr-datum": "2026-07-30",
			}),
		],
		leeresManifest(JETZT),
		JETZT,
	);
	const eintrag = e.manifest.eintraege["Fall 8.md"];
	assert.equal(eintrag?.status, "offen");
	assert.equal(eintrag?.["quelle-pdf"], "raw/VwR/Fall 8.pdf");
	assert.equal(eintrag?.seiten, 14);
	assert.equal(eintrag?.["seiten-ocr"], 9);
	assert.equal(eintrag?.["ocr-datum"], "2026-07-30");
	assert.equal(eintrag?.entschieden, null, "kein Zeitpunkt wird erfunden");
});

test("Regel 3: Datei liegt bereits in _abgelehnt, ohne dass wir sie bewegt haetten", () => {
	const e = abgleichen(
		[datei("Fall 9.md", "abgelehnt")],
		leeresManifest(JETZT),
		JETZT,
	);
	assert.equal(e.manifest.eintraege["Fall 9.md"]?.status, "abgelehnt");
	assert.equal(e.manifest.eintraege["Fall 9.md"]?.entschieden, null);
});

// ── Regel 2: das Dateisystem gewinnt ────────────────────────────────────────

test("Regel 2: von Hand nach _ocr-vorschau zurueckgeschoben → wieder offen", () => {
	const vorher = mitEintrag("Fall 8.md", {
		status: "akzeptiert",
		pfad: "_ocr-vorschau/_akzeptiert/Fall 8.md",
		entschieden: "2026-08-01T10:00:00+02:00",
		notiz: "S. 7 kaputt",
		"geprueft-bis": 12,
		"ocr-datum": "2026-07-30",
	});
	const e = abgleichen(
		[datei("Fall 8.md", "offen", { "ocr-datum": "2026-07-30" })],
		vorher,
		JETZT,
	);
	const eintrag = e.manifest.eintraege["Fall 8.md"];
	assert.equal(eintrag?.status, "offen");
	assert.equal(eintrag?.entschieden, null, "Entscheidung zurueckgenommen");
	assert.equal(eintrag?.notiz, "S. 7 kaputt", "Anmerkung bleibt");
	assert.equal(eintrag?.["geprueft-bis"], 12, "Lesefortschritt bleibt");
	assert.deepEqual(e.korrigiert, ["Fall 8.md"]);
	assert.deepEqual(e.neuErzeugt, []);
});

test("Regel 2: von Hand nach _akzeptiert geschoben → Status folgt dem Ordner", () => {
	const vorher = mitEintrag("Fall 8.md", { status: "offen" });
	const e = abgleichen([datei("Fall 8.md", "akzeptiert")], vorher, JETZT);
	assert.equal(e.manifest.eintraege["Fall 8.md"]?.status, "akzeptiert");
	assert.deepEqual(e.korrigiert, ["Fall 8.md"]);
});

// ── Regel 5/6: Neukonvertierung ─────────────────────────────────────────────

test("Regel 6: zwei Fassungen gleichzeitig → neu-erzeugt, alte Entscheidung bleibt erhalten", () => {
	const vorher = mitEintrag("Fall 8.md", {
		status: "akzeptiert",
		pfad: "_ocr-vorschau/_akzeptiert/Fall 8.md",
		entschieden: "2026-08-01T10:00:00+02:00",
		"ocr-datum": "2026-07-30",
	});
	const e = abgleichen(
		[
			datei("Fall 8.md", "offen", { "ocr-datum": "2026-08-05" }),
			datei("Fall 8.md", "akzeptiert", { "ocr-datum": "2026-07-30" }),
		],
		vorher,
		JETZT,
	);
	const eintrag = e.manifest.eintraege["Fall 8.md"];
	assert.equal(eintrag?.status, "neu-erzeugt");
	assert.equal(eintrag?.["ocr-datum"], "2026-08-05", "die frische Fassung zaehlt");
	assert.equal(eintrag?.vorher?.status, "akzeptiert");
	assert.equal(eintrag?.vorher?.["ocr-datum"], "2026-07-30");
	assert.equal(eintrag?.vorher?.entschieden, "2026-08-01T10:00:00+02:00");
	assert.deepEqual(e.neuErzeugt, ["Fall 8.md"]);
});

test("Regel 6: nur eine Fassung, aber anderes ocr-datum → auch neu-erzeugt", () => {
	const vorher = mitEintrag("Fall 8.md", {
		status: "abgelehnt",
		"ocr-datum": "2026-07-30",
		entschieden: "2026-08-01T10:00:00+02:00",
	});
	const e = abgleichen(
		[datei("Fall 8.md", "offen", { "ocr-datum": "2026-08-05" })],
		vorher,
		JETZT,
	);
	assert.equal(e.manifest.eintraege["Fall 8.md"]?.status, "neu-erzeugt");
	assert.equal(e.manifest.eintraege["Fall 8.md"]?.vorher?.status, "abgelehnt");
});

test("Regel 6 grenzt sich vom Handverschub ab: gleiches ocr-datum ist keine Neukonvertierung", () => {
	const vorher = mitEintrag("Fall 8.md", {
		status: "akzeptiert",
		"ocr-datum": "2026-07-30",
		entschieden: "2026-08-01T10:00:00+02:00",
	});
	const e = abgleichen(
		[datei("Fall 8.md", "offen", { "ocr-datum": "2026-07-30" })],
		vorher,
		JETZT,
	);
	assert.equal(e.manifest.eintraege["Fall 8.md"]?.status, "offen");
	assert.deepEqual(e.neuErzeugt, []);
	assert.deepEqual(e.korrigiert, ["Fall 8.md"]);
});

test("neu-erzeugt im offenen Ordner wird nicht jedes Mal erneut korrigiert", () => {
	const vorher = mitEintrag("Fall 8.md", {
		status: "neu-erzeugt",
		"ocr-datum": "2026-08-05",
		vorher: {
			status: "akzeptiert",
			entschieden: "2026-08-01T10:00:00+02:00",
			"ocr-datum": "2026-07-30",
		},
	});
	const e = abgleichen(
		[datei("Fall 8.md", "offen", { "ocr-datum": "2026-08-05" })],
		vorher,
		JETZT,
	);
	assert.equal(e.manifest.eintraege["Fall 8.md"]?.status, "neu-erzeugt");
	assert.deepEqual(e.korrigiert, []);
});

// ── Regel 4: Eintrag ohne Datei ─────────────────────────────────────────────

test("Regel 4: Datei liegt woanders im Vault → uebernommen, Eintrag bleibt", () => {
	const vorher = mitEintrag("Fall 8.md", {
		status: "akzeptiert",
		pfad: "wiki/fall-8.md",
	});
	const e = abgleichen([], vorher, JETZT, (p) => p === "wiki/fall-8.md");
	assert.equal(e.manifest.eintraege["Fall 8.md"]?.status, "uebernommen");
	assert.deepEqual(e.entfernt, []);
});

test("Regel 4: Datei nirgends mehr → Cache-Zeile faellt weg (keine Datei wird geloescht)", () => {
	const vorher = mitEintrag("Fall 8.md", { status: "akzeptiert" });
	const e = abgleichen([], vorher, JETZT, () => false);
	assert.equal(e.manifest.eintraege["Fall 8.md"], undefined);
	assert.deepEqual(e.entfernt, ["Fall 8.md"]);
});

// ── Manifest ist nur Cache ──────────────────────────────────────────────────

test("geloeschtes Manifest baut sich vollstaendig aus der Ordnerlage neu auf", () => {
	const dateien = [
		datei("A.md", "offen", { seiten: "3" }),
		datei("B.md", "akzeptiert", { seiten: "5" }),
		datei("C.md", "abgelehnt", { seiten: "7" }),
	];
	const e = abgleichen(dateien, leeresManifest(JETZT), JETZT);
	assert.equal(e.manifest.eintraege["A.md"]?.status, "offen");
	assert.equal(e.manifest.eintraege["B.md"]?.status, "akzeptiert");
	assert.equal(e.manifest.eintraege["C.md"]?.status, "abgelehnt");
});

// ── Serialisierung ──────────────────────────────────────────────────────────

test("Manifest ueberlebt Schreiben und Lesen", () => {
	const vorher = mitEintrag("Fall 8.md", {
		status: "akzeptiert",
		seiten: 14,
		"seiten-ocr": 9,
		notiz: "S. 7 Lesereihenfolge kaputt",
		"geprueft-bis": 12,
		entschieden: JETZT,
		vorher: { status: "abgelehnt", entschieden: null, "ocr-datum": "2026-07-01" },
	});
	const wieder = manifestLesen(manifestSchreiben(vorher), JETZT);
	assert.deepEqual(wieder.eintraege, vorher.eintraege);
});

test("Manifest mit unbekanntem Status faellt auf offen zurueck statt zu werfen", () => {
	const wieder = manifestLesen(
		JSON.stringify({
			version: 1,
			eintraege: { "X.md": { status: "quatsch", pfad: "_ocr-vorschau/X.md" } },
		}),
		JETZT,
	);
	assert.equal(wieder.eintraege["X.md"]?.status, "offen");
});

test("Eintraege werden sortiert geschrieben, damit ein Diff lesbar bleibt", () => {
	const m = leeresManifest(JETZT);
	for (const name of ["Z.md", "A.md", "M.md"]) {
		m.eintraege[name] = mitEintrag(name, {}).eintraege[name]!;
	}
	const text = manifestSchreiben(m);
	assert.ok(text.indexOf('"A.md"') < text.indexOf('"M.md"'));
	assert.ok(text.indexOf('"M.md"') < text.indexOf('"Z.md"'));
});

// ── Entscheidung eintragen ──────────────────────────────────────────────────

test("entscheidungEintragen setzt Status, Pfad und Zeitpunkt", () => {
	const vorher = mitEintrag("Fall 8.md", { status: "offen" });
	const nachher = entscheidungEintragen(
		vorher,
		"Fall 8.md",
		"akzeptiert",
		"_ocr-vorschau/_akzeptiert/Fall 8.md",
		JETZT,
	);
	const e = nachher.eintraege["Fall 8.md"];
	assert.equal(e?.status, "akzeptiert");
	assert.equal(e?.pfad, "_ocr-vorschau/_akzeptiert/Fall 8.md");
	assert.equal(e?.entschieden, JETZT);
});

test("Zuruecksetzen auf offen loescht den Entscheidungszeitpunkt", () => {
	const vorher = mitEintrag("Fall 8.md", {
		status: "akzeptiert",
		entschieden: JETZT,
	});
	const nachher = entscheidungEintragen(
		vorher,
		"Fall 8.md",
		"offen",
		"_ocr-vorschau/Fall 8.md",
		JETZT,
	);
	assert.equal(nachher.eintraege["Fall 8.md"]?.entschieden, null);
});

test("Eine neue Entscheidung loescht die Erinnerung an die alte", () => {
	// `vorher` beantwortet die Frage „was galt vor der Neukonvertierung?". Ist
	// neu entschieden, ist sie beantwortet — bliebe sie stehen, zeigte die
	// Seitenleiste ein „Vorher …" zu einem Zustand, den es nicht mehr gibt.
	const vorher = mitEintrag("Fall 8.md", {
		status: "neu-erzeugt",
		vorher: {
			status: "akzeptiert",
			entschieden: "2026-08-01T10:00:00+02:00",
			"ocr-datum": "2026-07-30",
		},
	});
	const nachher = entscheidungEintragen(
		vorher,
		"Fall 8.md",
		"akzeptiert",
		"_ocr-vorschau/_akzeptiert/Fall 8.md",
		JETZT,
	);
	assert.equal(nachher.eintraege["Fall 8.md"]?.vorher, null);
});

test("zielordner bildet alle drei Lagen ab", () => {
	assert.equal(zielordner("offen", ORDNER), "_ocr-vorschau");
	assert.equal(zielordner("akzeptiert", ORDNER), "_ocr-vorschau/_akzeptiert");
	assert.equal(zielordner("abgelehnt", ORDNER), "_ocr-vorschau/_abgelehnt");
});

test("altesDatumAus: Frontmatter der alten Datei schlaegt die Erinnerung", () => {
	const eintrag = mitEintrag("Fall 8.md", {
		"ocr-datum": "2026-07-30",
		vorher: {
			status: "akzeptiert",
			entschieden: JETZT,
			"ocr-datum": "2026-06-01",
		},
	}).eintraege["Fall 8.md"];
	assert.equal(altesDatumAus(eintrag, { "ocr-datum": "2026-06-15" }), "2026-06-15");
});

test("altesDatumAus: der Eintrag einer Neukonvertierung traegt das NEUE Datum — genutzt wird die Erinnerung", () => {
	const eintrag = mitEintrag("Fall 8.md", {
		"ocr-datum": "2026-07-30",
		vorher: {
			status: "akzeptiert",
			entschieden: JETZT,
			"ocr-datum": "2026-06-01",
		},
	}).eintraege["Fall 8.md"];
	assert.equal(altesDatumAus(eintrag, {}), "2026-06-01");
});

test("altesDatumAus: ohne Anhaltspunkt bleibt 'alt'", () => {
	assert.equal(altesDatumAus(undefined, {}), "alt");
});
