// Original-PDF seitenweise, lazy gerendert.
//
// Dies ist der Risikoschritt des Plugins — nicht wegen der Mechanik, sondern
// wegen der Abhaengigkeit von Obsidians pdf.js-Fork. Drei Absicherungen:
//
// 1. Alle pdf.js-Aufrufe liegen in dieser Datei, das eigentliche Rendern
//    gebuendelt in `seiteZeichnen(page, canvas, skala)`. Aendert Obsidian die
//    Signatur, ist das ein Einzeiler-Fix statt einer Vault-Jagd.
// 2. Jeder Fehler degradiert statt zu blockieren: Banner im Kopf mit „Im
//    PDF-Viewer oeffnen" als Weg in Obsidians eigenen Betrachter.
// 3. `loadPdfJs()` ist die dokumentierte Obsidian-API — der Worker ist von
//    Obsidian bereits verdrahtet (`GlobalWorkerOptions.workerSrc` ist gesetzt).
//    Es gibt keinen Blob-Worker-Bau und keinen CSP-Kampf, und das Bundle
//    bleibt bei ~60–80 kB statt ~2,5 MB.
//
// Platzhalter-Strategie: nach `getDocument` werden ALLE Viewports bei
// Massstab 1 geholt — das liest nur das Seiten-Dictionary, rastert nichts.
// Jeder Seiten-Container bekommt sein Seitenverhaeltnis als CSS-Custom-
// Property; Hoehen und Breiten folgen daraus via `aspect-ratio`, ohne
// JavaScript-Nachkorrektur. Die Scrollbar hat damit ab Frame eins die richtige
// Geometrie — Springen beim Nachladen ist verhindert statt kompensiert.

import { App, TFile } from "obsidian";
import { loadPdfJs } from "obsidian";

import type {
	PdfDokument,
	PdfJsLib,
	PdfRenderTask,
	PdfSeite,
	PdfViewport,
} from "../types/pdfjs.d.ts";

/** Renderparallelitaet: mehr bedeutet beim Zurueckscrollen weniger Warten,
 *  kostet aber Speicher und Laeden am Worker. */
const PARALLEL = 2;
/** Deckel fuer gepufferte Canvases. Ein A4-Canvas bei Faktor 2 ist ~4,5 MB
 *  RGBA; beim Raeumen wird der Puffer per width/height = 0 freigegeben, sonst
 *  bleibt er liegen. */
const MAX_CANVAS = 12;
const RESIZE_ENTPRELLT_MS = 150;
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 2;

interface Seitenzustand {
	nr: number;
	el: HTMLElement;
	canvas: HTMLCanvasElement;
	seite: PdfSeite | null;
	viewport: PdfViewport | null;
	task: PdfRenderTask | null;
	/** Rasterung (oder Fehleranzeige) abgeschlossen. */
	gerendert: boolean;
	/** Zuletzt im rootMargin-Fenster gesehen. */
	sichtbar: boolean;
	/** LRU-Stempel: `performance.now()` bei Sichtbarkeit und nach dem Zeichnen. */
	zuletztGenutzt: number;
}

interface Zeichenauftrag {
	nr: number;
	erzwingen: boolean;
}

export class PdfSpalte {
	readonly scrollEl: HTMLElement;

	/** Hoehen haben sich geaendert (Canvas eingewechselt, Breite gewachsen,
	 *  Zoom veraendert) — die Kopplung soll neu vermessen. */
	beiVermessungNoetig: (() => void) | null = null;
	/** Fehler beim Laden — das Banner baut die Ansicht im PDF-Kopf. */
	beiFehler: ((fehler: string | null) => void) | null = null;
	/** PDF geladen: Name und Seitenzahl fuer den Spaltenkopf. */
	beiGeladen: ((name: string | null, seiten: number) => void) | null = null;

	private container: HTMLElement;
	private stapel: HTMLElement;
	private leerZustand: HTMLElement;
	private seiten = new Map<number, Seitenzustand>();
	private doc: PdfDokument | null = null;
	private pdfDatei: TFile | null = null;
	private lauf = 0;
	private observer: IntersectionObserver | null = null;
	private resizeObserver: ResizeObserver | null = null;
	private resizeTimer: number | null = null;
	private warteschlange: Zeichenauftrag[] = [];
	private angefragt = new Set<number>();
	private aktiveZeichnungen = 0;
	private zoom = 1;

	constructor(
		private app: App,
		wurzel: HTMLElement,
		/** Obergrenze des Renderfaktors aus den Einstellungen. */
		private zoomMax: () => number,
	) {
		this.scrollEl = wurzel.createDiv({ cls: "ocr-pdf-scroll" });
		this.container = this.scrollEl.createDiv({ cls: "ocr-pdf-inhalt" });
		this.stapel = this.container.createDiv({ cls: "ocr-pdf-stapel" });
		this.leerZustand = this.container.createDiv({
			cls: "ocr-leer ocr-pdf-leer",
			text: "Kein Original-PDF geladen.",
		});
		// Breitenaenderung aendert die Pixel-Skala und (ueber aspect-ratio) alle
		// Hoehen — beides will neu vermessen sein. Entprellt, s. Plan Abschnitt 3.
		this.resizeObserver = new ResizeObserver(() => this.entprelltVermessen());
		this.resizeObserver.observe(this.scrollEl);
	}

	/** Seitencontainer fuer die Scroll-Kopplung — existieren ab Frame eins,
	 *  laengst bevor die erste Seite gerastert ist. */
	elemente(): Map<number, HTMLElement> {
		const m = new Map<number, HTMLElement>();
		for (const [nr, z] of this.seiten) m.set(nr, z.el);
		return m;
	}

	aktuelleZoom(): number {
		return this.zoom;
	}

	zoomen(schritt: number): void {
		const vorher = this.zoom;
		this.zoom = Math.min(Math.max(this.zoom + schritt, ZOOM_MIN), ZOOM_MAX);
		if (this.zoom === vorher) return;
		this.stapel.style.setProperty("--ocr-pdf-zoom", String(this.zoom));
		// Zoom aendert alle Layout-Hoehen — die Kopplung muss neu vermessen,
		// sonst springt die Markdown-Spalte auf tote Positionen.
		this.beiVermessungNoetig?.();
		// Und die Rasterung muss mit: ein vorhandener Canvas wird durch `zoom`
		// nur hochskaliert. Ohne erzwungenes Neuzeichnen ist Vergroessern reine
		// Unschaerfe statt mehr Detail.
		for (const z of this.seiten.values()) {
			if (z.sichtbar) this.zeichnenAnstossen(z.nr, true);
		}
	}

	/** Laedt ein neues Dokument oder raeumt die Spalte (null). */
	async oeffnen(datei: TFile | null): Promise<void> {
		const lauf = ++this.lauf;
		// `destroy` ist asynchron. Das alte Dokument SOFORT abhaengen, sonst
		// greifen zwei schnell aufeinanderfolgende Aufrufe (zweimal `j`) beide auf
		// dasselbe Dokument zu und zerstoeren es doppelt.
		const alt = this.doc;
		this.doc = null;
		if (alt !== null) await alt.destroy().catch(() => undefined);
		// Ab hier gilt der Laufcheck: ohne ihn raeumt der ueberholte Aufruf gleich
		// den Stapel des neueren weg und baut seine eigenen Seiten hinein.
		if (lauf !== this.lauf) return;
		this.observer?.disconnect();
		this.observer = null;
		this.pdfDatei = null;
		this.seiten.clear();
		this.stapel.empty();
		this.warteschlange = [];
		this.angefragt.clear();
		this.aktiveZeichnungen = 0;
		this.leerZustand.show();
		this.beiFehler?.(null);

		if (datei === null) {
			this.beiGeladen?.(null, 0);
			return;
		}
		this.pdfDatei = datei;
		this.leerZustand.hide();
		try {
			await this.dokumentLaden(datei, lauf);
			if (lauf !== this.lauf) return; // inzwischen gewechselt
			this.beobachten();
		} catch (fehler) {
			if (lauf !== this.lauf) return;
			console.error("OCR-Vorschau: PDF nicht ladbar", fehler);
			this.beiFehler?.(`Das PDF „${datei.name}“ konnte nicht geladen werden.`);
		}
	}

	zerstoeren(): void {
		this.lauf++;
		this.resizeObserver?.disconnect();
		this.resizeObserver = null;
		this.observer?.disconnect();
		this.observer = null;
		if (this.resizeTimer !== null) window.clearTimeout(this.resizeTimer);
		void this.doc?.destroy().catch(() => undefined);
		this.doc = null;
	}

	private async dokumentLaden(datei: TFile, lauf: number): Promise<void> {
		if (typeof window.pdfjsLib === "undefined") await loadPdfJs();
		const pdfjs = window.pdfjsLib as PdfJsLib;
		const ladevorgang = pdfjs.getDocument({
			url: this.app.vault.getResourcePath(datei),
			// cMaps sind nicht optional: PDFs mit eingebetteten CID/Type0-Fonts —
			// genau das Material hier — rendern sonst leer. Rezept aus Excalidraw.
			// Fester Pfad in Obsidians App-Bundle: faellt er weg, bleiben CID-Fonts
			// leer — sichtbar als weisse Seite trotz erfolgreichem Rendern.
			cMapUrl: "/lib/pdfjs/cmaps/",
			cMapPacked: true,
			standardFontDataUrl: "/lib/pdfjs/standard_fonts/",
		});
		const doc = await ladevorgang.promise;
		// Erst nach dem Laufcheck uebernehmen — ein ueberholter Ladevorgang darf
		// das inzwischen gueltige Dokument nicht verdraengen.
		if (lauf !== this.lauf) {
			void doc.destroy().catch(() => undefined);
			return;
		}
		this.doc = doc;

		for (let nr = 1; nr <= doc.numPages; nr++) {
			const seite = await doc.getPage(nr);
			// Jede Seite ist ein eigener await: ohne Check im Schleifenkoerper
			// schoebe ein ueberholter Lauf seine Seiten in den fremden Stapel.
			if (lauf !== this.lauf) return;
			const viewport = seite.getViewport({ scale: 1 });
			const el = this.stapel.createDiv({ cls: "ocr-pdf-seite" });
			el.dataset["seite"] = String(nr);
			// Seitenverhaeltnis als Custom Property: Hoehe und Breite des
			// Platzhalters folgen daraus via aspect-ratio — kein inline-style.
			el.style.setProperty(
				"--ocr-seitenverhaeltnis",
				`${viewport.width} / ${viewport.height}`,
			);
			const canvas = el.createEl("canvas", { cls: "ocr-pdf-canvas" });
			this.seiten.set(nr, {
				nr,
				el,
				canvas,
				seite,
				viewport,
				task: null,
				gerendert: false,
				sichtbar: false,
				zuletztGenutzt: 0,
			});
		}
		this.beiGeladen?.(datei.name, doc.numPages);
	}

	private beobachten(): void {
		const observer = new IntersectionObserver(
			(eintraege) => {
				for (const eintrag of eintraege) {
					const nr = Number((eintrag.target as HTMLElement).dataset["seite"]);
					const z = this.seiten.get(nr);
					if (z === undefined) continue;
					z.sichtbar = eintrag.isIntersecting;
					if (eintrag.isIntersecting) {
						z.zuletztGenutzt = performance.now();
						this.zeichnenAnstossen(nr);
					}
				}
			},
			{ root: this.scrollEl, rootMargin: "200% 0px" },
		);
		for (const z of this.seiten.values()) observer.observe(z.el);
		this.observer = observer;
	}

	/** Entprellt, weil ein Resize-Ereignis pro Frame ankommt. */
	private entprelltVermessen(): void {
		if (this.resizeTimer !== null) window.clearTimeout(this.resizeTimer);
		this.resizeTimer = window.setTimeout(() => {
			this.resizeTimer = null;
			this.beiVermessungNoetig?.();
			// Breite geaendert → Pixel-Skala geaendert → sichtbare Seiten
			// (auch bereits gerenderte) neu zeichnen.
			for (const z of this.seiten.values()) {
				if (z.sichtbar) this.zeichnenAnstossen(z.nr, true);
			}
		}, RESIZE_ENTPRELLT_MS);
	}

	/** `erzwingen` zeichnet auch bereits gerenderte Seiten neu (Zoom/Resize). */
	private zeichnenAnstossen(nr: number, erzwingen = false): void {
		const z = this.seiten.get(nr);
		if (z === undefined || this.angefragt.has(nr)) return;
		if (z.gerendert && !erzwingen) return;
		this.angefragt.add(nr);
		this.warteschlange.push({ nr, erzwingen });
		this.weitermachen();
	}

	private weitermachen(): void {
		while (this.aktiveZeichnungen < PARALLEL) {
			const auftrag = this.warteschlange.shift();
			if (auftrag === undefined) break;
			this.angefragt.delete(auftrag.nr);
			const z = this.seiten.get(auftrag.nr);
			if (z === undefined) continue;
			if (z.gerendert && !auftrag.erzwingen) continue;
			this.aktiveZeichnungen++;
			// `.catch` ist Pflicht, nicht Vorsicht: `getPage` und der synchrone
			// Wurf von `seiteZeichnen` (kein 2D-Kontext) liegen ausserhalb des
			// try-Blocks in `zeichnen`. Mit blossem `.finally` entkaeme das als
			// unbehandelte Rejection.
			void this.zeichnen(z)
				.catch((fehler: unknown) => this.seitenFehler(z, fehler))
				.finally(() => {
					this.aktiveZeichnungen--;
					this.weitermachen();
				});
		}
	}

	private async zeichnen(z: Seitenzustand): Promise<void> {
		const doc = this.doc;
		if (doc === null) return;
		if (z.seite === null) z.seite = await doc.getPage(z.nr);
		const seite = z.seite;
		if (seite === null) return;
		if (z.viewport === null) z.viewport = seite.getViewport({ scale: 1 });
		// Eine erzwungene Wiederholung (Zoom, Resize) trifft auch Seiten, die
		// beim letzten Mal gescheitert sind. Deren Fehleranzeige zuerst weg:
		// sonst stapeln sich die Banner, oder eines bleibt ueber einer Seite
		// stehen, die inzwischen laengst wieder zeichnet.
		this.fehlerZuruecksetzen(z);
		const skala = this.skalaFuer(z);
		// Vor dem Neuzeichnen canceln: ohne das meldet pdf.js „Cannot use the
		// same canvas during multiple render operations".
		z.task?.cancel();
		const task = this.seiteZeichnen(seite, z.canvas, skala);
		z.task = task;
		try {
			await task.promise;
		} catch (fehler) {
			if (z.task === task) z.task = null;
			// cancel() wirft die RenderingCancelledException — erwartet, die
			// Nachfolge-Zeichnung uebernimmt.
			if (istAbbruch(fehler)) return;
			this.seitenFehler(z, fehler);
			return;
		}
		if (z.task === task) z.task = null;
		z.gerendert = true;
		z.zuletztGenutzt = performance.now();
		this.raeumen();
		// Canvas eingewechselt — die Kopplung soll neu vermessen (Plan: die
		// vier Ereignisse, nach denen neu vermessen wird).
		this.beiVermessungNoetig?.();
	}

	/** Der eigentliche pdf.js-Renderaufruf. Einzige Stelle, an der die
	 *  Render-Signatur des Forks steht. */
	private seiteZeichnen(
		seite: PdfSeite,
		canvas: HTMLCanvasElement,
		skala: number,
	): PdfRenderTask {
		const viewport = seite.getViewport({ scale: skala });
		canvas.width = Math.floor(viewport.width);
		canvas.height = Math.floor(viewport.height);
		const kontext = canvas.getContext("2d");
		if (kontext === null) {
			throw new Error("Canvas-Kontext nicht verfügbar");
		}
		return seite.render({ canvasContext: kontext, viewport });
	}

	/** Pixel-Skala: Seitenbreite in CSS-Pixeln gefuellt, scharf fuer den
	 *  Bildschirm, gedeckelt durch die Speicherbremse aus den Einstellungen.
	 *
	 *  `zoom` liegt auf `.ocr-pdf-stapel`, also auf dem ELTERNelement. Die
	 *  Seiten-Container messen sich darin in lokalen, unskalierten Koordinaten —
	 *  ihre tatsaechliche Groesse auf dem Schirm ist `clientWidth * zoom`. Genau
	 *  dieser Faktor muss in die Rasterung, sonst zeigt „200 %" dieselbe
	 *  Pixelzahl, nur groesser gezogen. */
	private skalaFuer(z: Seitenzustand): number {
		const viewport = z.viewport;
		if (viewport === null) return 1;
		const breite = Math.max(z.el.clientWidth * this.zoom, 1);
		return Math.min(
			(breite / viewport.width) * window.devicePixelRatio,
			this.zoomMax(),
		);
	}

	/** Fehlerzustand einer Seite loeschen — vor jedem Zeichenversuch. */
	private fehlerZuruecksetzen(z: Seitenzustand): void {
		z.el.removeClass("ocr-pdf-seite-fehler");
		for (const banner of z.el.querySelectorAll(".ocr-pdf-seitenfehler")) {
			banner.remove();
		}
	}

	private seitenFehler(z: Seitenzustand, fehler: unknown): void {
		console.error("OCR-Vorschau: Seite nicht darstellbar", z.nr, fehler);
		this.fehlerZuruecksetzen(z);
		// Als gerendert markieren, damit der Beobachter nicht in eine
		// Endlosschleife laeuft; der Weg bleibt der PDF-Viewer von Obsidian.
		z.gerendert = true;
		z.el.addClass("ocr-pdf-seite-fehler");
		z.el.createDiv({
			cls: "ocr-pdf-seitenfehler",
			text: `Seite ${z.nr}: nicht darstellbar. Im PDF-Viewer öffnen?`,
		});
	}

	/** LRU-Raeumung: mehr als MAX_CANVAS gepufferte Seiten kosten ~4,5 MB
	 *  pro Stueck. Der Canvas-Puffer wird freigegeben, das Element bleibt —
	 *  sonst muesste der DOM-Baum (und damit die Kopplung) umgebaut werden. */
	private raeumen(): void {
		const gepuffert = [...this.seiten.values()].filter(
			(z) => z.gerendert && z.task === null,
		);
		if (gepuffert.length <= MAX_CANVAS) return;
		// Sichtbare Seiten sind nie Raeumkandidaten. Der IntersectionObserver
		// meldet sich erst bei der naechsten Sichtbarkeitsaenderung wieder — eine
		// geraeumte sichtbare Seite bliebe also leer stehen, bis der Nutzer
		// wegscrollt und zurueckkommt.
		const kandidaten = gepuffert.filter((z) => !z.sichtbar);
		kandidaten.sort((a, b) => a.zuletztGenutzt - b.zuletztGenutzt);
		for (const z of kandidaten.slice(0, gepuffert.length - MAX_CANVAS)) {
			z.gerendert = false;
			z.canvas.width = 0;
			z.canvas.height = 0;
			z.seite?.cleanup();
		}
	}
}

/** Die Abbruch-Exception von pdf.js: `cancel()` ruft sie absichtlich hervor. */
function istAbbruch(fehler: unknown): boolean {
	if (fehler instanceof Error && fehler.name === "RenderingCancelledException") {
		return true;
	}
	return String(fehler).includes("RenderingCancelledException");
}
