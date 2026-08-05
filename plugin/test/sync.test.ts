import { test } from "node:test";
import assert from "node:assert/strict";

// ── Laufzeit-Shim ───────────────────────────────────────────────────────────
// `Kopplung` laeuft unter `window` (Lock-Timer, requestAnimationFrame).
// node kennt beides nicht: Die Lock-Timer sind echte Timer, der Frame-
// Scheduler wird hier manuell abgearbeitet (`rafAusfuehren`) — damit sind
// die Schreibvorgaenge deterministisch pruefbar.

let rafAuffraege: Array<() => void> = [];

Object.defineProperty(globalThis, "window", {
	configurable: true,
	value: {
		setTimeout: (fn: () => void, ms?: number) => setTimeout(fn, ms),
		clearTimeout: (id?: NodeJS.Timeout) => clearTimeout(id),
		requestAnimationFrame: (cb: () => void) => {
			rafAuffraege.push(cb);
			return rafAuffraege.length;
		},
		cancelAnimationFrame: () => undefined,
	},
});

function rafAusfuehren(): void {
	const arbeit = rafAuffraege;
	rafAuffraege = [];
	for (const cb of arbeit) cb();
}

function schlafen(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

import type { SyncQuelle } from "../src/sync.ts";
const { Kopplung } = await import("../src/sync.ts");
type Kopplung = InstanceType<typeof Kopplung>;

// ── Falsches DOM ────────────────────────────────────────────────────────────

/** Scroll-Container: speichert den Listener, `feuern` simuliert das
 *  scroll-Ereignis, das ein Schreibvorgang im echten DOM ausloest. */
class FakeScrollEl {
	scrollTop = 0;
	scrollHeight: number;
	clientHeight: number;
	private handler: (() => void) | null = null;

	constructor(scrollHeight: number, clientHeight: number) {
		this.scrollHeight = scrollHeight;
		this.clientHeight = clientHeight;
	}

	addEventListener(_typ: string, handler: () => void): void {
		this.handler = handler;
	}

	removeEventListener(_typ: string, _handler: () => void): void {
		this.handler = null;
	}

	getBoundingClientRect(): { top: number } {
		return { top: 0 };
	}

	feuern(): void {
		this.handler?.();
	}
}

/** Block-Element einer Spalte. Die rects sind scroll-invariant — die
 *  Vermessung wird im Test immer vor dem Scrollen durchgefuehrt, was der
 *  echten Scroll-Invarianz des DOM entspricht. */
class FakeBlock {
	private readonly top: number;
	private readonly hoehe: number;

	constructor(top: number, hoehe: number) {
		this.top = top;
		this.hoehe = hoehe;
	}

	getBoundingClientRect(): { top: number; height: number } {
		return { top: this.top, height: this.hoehe };
	}
}

interface Aufbau {
	kopplung: Kopplung;
	pdf: FakeScrollEl;
	md: FakeScrollEl;
}

/** Bloecke als [seitennummer, top, hoehe]. */
function aufbauen(
	pdfBloecke: Array<[number, number, number]>,
	mdBloecke: Array<[number, number, number]>,
): Aufbau {
	const pdf = new FakeScrollEl(2000, 600);
	const md = new FakeScrollEl(2000, 600);
	const spalte = (
		el: FakeScrollEl,
		bloecke: Array<[number, number, number]>,
	): SyncQuelle => ({
		scrollEl: el as unknown as HTMLElement,
		elemente: () =>
			new Map<number, HTMLElement>(
				bloecke.map(
					([nr, top, hoehe]) =>
						[nr, new FakeBlock(top, hoehe) as unknown as HTMLElement] as const,
				),
			),
	});
	const kopplung = new Kopplung({ pdf: spalte(pdf, pdfBloecke), md: spalte(md, mdBloecke) });
	kopplung.neuVermessen();
	return { kopplung, pdf, md };
}

const BLOECKE: Array<[number, number, number]> = [
	[1, 0, 100],
	[2, 100, 100],
	[3, 200, 100],
];

// ── Abbildung ───────────────────────────────────────────────────────────────

test("position: Bruchteil ueber die Blockhoehe, nicht ueber die Seitenzahl", () => {
	const { kopplung, pdf } = aufbauen(
		[
			[1, 0, 100],
			[2, 100, 200],
			[3, 300, 100],
		],
		BLOECKE,
	);
	pdf.scrollTop = 150;
	assert.equal(kopplung.position("pdf"), 2.25);
});

test("position klemmt ans Ende, nie ueber die letzte Seite", () => {
	const { kopplung, pdf } = aufbauen(BLOECKE, BLOECKE);
	pdf.scrollTop = 9999;
	assert.equal(kopplung.position("pdf"), 3.999);
});

test("ohne Vermessung oder ohne Elemente bleibt die Kopplung stumm", () => {
	const pdf = new FakeScrollEl(2000, 600);
	const md = new FakeScrollEl(2000, 600);
	const kopplung = new Kopplung({
		pdf: { scrollEl: pdf as unknown as HTMLElement, elemente: () => new Map() },
		md: { scrollEl: md as unknown as HTMLElement, elemente: () => new Map() },
	});
	assert.equal(kopplung.position("pdf"), null);
	pdf.scrollTop = 150;
	pdf.feuern();
	rafAusfuehren();
	assert.equal(md.scrollTop, 0);
});

// ── Treiber → Folger ────────────────────────────────────────────────────────

test("Scroll auf pdf treibt md auf denselben Bruchteil", () => {
	const { kopplung, pdf, md } = aufbauen(BLOECKE, BLOECKE);
	let gemeldet: number | null = null;
	kopplung.beiSeite = (s) => (gemeldet = s);
	pdf.scrollTop = 150;
	pdf.feuern();
	assert.equal(gemeldet, 2.5);
	rafAusfuehren();
	assert.equal(md.scrollTop, 150);
});

test("Besitzer-Token: der Echo-Scroll des Folgers spielt nichts zurueck", () => {
	const { pdf, md } = aufbauen(BLOECKE, BLOECKE);
	pdf.scrollTop = 250;
	pdf.feuern();
	rafAusfuehren();
	assert.equal(md.scrollTop, 250);
	// Das Schreiben selbst loest im echten DOM ein scroll-Ereignis aus.
	md.feuern();
	rafAusfuehren();
	assert.equal(md.scrollTop, 250, "md bleibt stehen");
	assert.equal(pdf.scrollTop, 250, "pdf wird nicht zurueckgeschrieben");
});

test("Epsilon: Zittern unter 2 px wird nicht weitergeschrieben", () => {
	const { pdf, md } = aufbauen(BLOECKE, BLOECKE);
	md.scrollTop = 151.9;
	pdf.scrollTop = 150;
	pdf.feuern();
	rafAusfuehren();
	assert.equal(md.scrollTop, 151.9);
});

test("Epsilon: Abstand von genau 2 px ist noch ein Schreibvorgang", () => {
	const { pdf, md } = aufbauen(BLOECKE, BLOECKE);
	md.scrollTop = 148;
	pdf.scrollTop = 150;
	pdf.feuern();
	rafAusfuehren();
	assert.equal(md.scrollTop, 150);
});

test("Schreiben klemmt an die Scrollgrenzen des Folgers", () => {
	const { pdf, md } = aufbauen(BLOECKE, BLOECKE);
	md.scrollHeight = 350;
	md.clientHeight = 100;
	pdf.scrollTop = 9999;
	pdf.feuern();
	rafAusfuehren();
	// Ziel waere 3,999·100+… — aber der Folger kann nur bis 250.
	assert.equal(md.scrollTop, 250);
});

test("Zwei Treiber-Ereignisse im selben Frame werden zu einem Schreibvorgang", () => {
	const { pdf, md } = aufbauen(BLOECKE, BLOECKE);
	pdf.scrollTop = 150;
	pdf.feuern();
	pdf.scrollTop = 250;
	pdf.feuern();
	assert.equal(rafAuffraege.length, 1, "genau ein Frame-Schreibvorgang");
	rafAusfuehren();
	assert.equal(md.scrollTop, 250, "der letzte Stand gewinnt");
});

test("Klemmen: fehlende Folger-Seite — Bruchteil verfaellt, exakter Anfang", () => {
	// pdf hat alle Seiten, md nur 1 und 3 (z.B. --nur-ocr).
	const { pdf, md } = aufbauen(BLOECKE, [
		[1, 0, 100],
		[3, 100, 100],
	]);
	pdf.scrollTop = 150;
	pdf.feuern();
	rafAusfuehren();
	// p = 2,5 — Seite 2 fehlt in md, geklemmt wird auf den Anfang von 1.
	assert.equal(md.scrollTop, 0);
});

// ── Lock ────────────────────────────────────────────────────────────────────

test("Lock: nachgetriggert — Ereignisse im Fenster verlaengern ihn", async () => {
	const { pdf, md } = aufbauen(BLOECKE, BLOECKE);
	pdf.scrollTop = 150;
	pdf.feuern();
	rafAusfuehren();
	await schlafen(80);
	// Ein weiteres Treiber-Ereignis im Lock-Fenster: der Lock beginnt neu.
	pdf.scrollTop = 250;
	pdf.feuern();
	rafAusfuehren();
	assert.equal(md.scrollTop, 250);
	await schlafen(80); // t=160 — Lock laeuft noch (Ende waere t=200)
	md.feuern();
	rafAusfuehren();
	assert.equal(pdf.scrollTop, 250, "md wird noch nicht gehoert");
	await schlafen(80); // t=240 — Lock abgelaufen
	md.scrollTop = 50;
	md.feuern();
	rafAusfuehren();
	assert.equal(pdf.scrollTop, 50, "md treibt jetzt pdf");
});

test("zuSeite setzt beide Spalten auf den Seitenanfang", () => {
	const { kopplung, pdf, md } = aufbauen(BLOECKE, BLOECKE);
	const gemeldet: number[] = [];
	kopplung.beiSeite = (s) => gemeldet.push(s);
	kopplung.zuSeite(2);
	assert.equal(pdf.scrollTop, 100);
	assert.equal(md.scrollTop, 100);
	assert.deepEqual(gemeldet, [2]);
});

test("zuSeite klemmt ueber das Ende hinaus auf die letzte Seite", () => {
	const { kopplung, pdf, md } = aufbauen(BLOECKE, BLOECKE);
	kopplung.zuSeite(99);
	assert.equal(pdf.scrollTop, 200);
	assert.equal(md.scrollTop, 200);
});

test("aktiv=false: Position wird gemeldet, geschrieben nicht", () => {
	const { kopplung, pdf, md } = aufbauen(BLOECKE, BLOECKE);
	kopplung.aktiv = false;
	const gemeldet: number[] = [];
	kopplung.beiSeite = (s) => gemeldet.push(s);
	pdf.scrollTop = 150;
	pdf.feuern();
	assert.deepEqual(gemeldet, [2.5]);
	rafAusfuehren();
	assert.equal(md.scrollTop, 0);
});

test("zerstoeren loest Listener und Timer", () => {
	const { kopplung, pdf, md } = aufbauen(BLOECKE, BLOECKE);
	kopplung.zerstoeren();
	pdf.scrollTop = 150;
	pdf.feuern();
	rafAusfuehren();
	assert.equal(md.scrollTop, 0);
	assert.equal(pdf.scrollTop, 150);
});
