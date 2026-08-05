import { App, Component, MarkdownRenderer, TFile } from "obsidian";

import type { Seitenblock, Vorschau } from "./typen.ts";

const BILDENDUNGEN = new Set(["png", "jpg", "jpeg", "webp", "gif", "avif", "svg"]);

export type Darstellung = "gerendert" | "quelltext";

/**
 * Rechte Spalte: das erzeugte Markdown, seitenweise.
 *
 * Blockweise und nicht am Stueck, weil `%%…%%` Obsidians Kommentarsyntax ist und
 * in der Leseansicht unsichtbar bleibt — am Marker gibt es also keinen
 * DOM-Knoten, an dem die Scroll-Kopplung ankern koennte. Der Seiten-Container
 * ist der Anker, und er ist in beiden Darstellungen derselbe. Die Sync-Schicht
 * kennt den Umschalter dadurch gar nicht.
 *
 * Bewusst NICHT lazy: die Hoehen sind hier inhaltsgetrieben und vorab unbekannt.
 * Lazy hiesse raten und nachkorrigieren — also genau das Sprungproblem, das die
 * PDF-Spalte mit vorgemessenen Platzhaltern vermeidet, hier wieder einfuehren.
 * `MarkdownRenderer.render` auf einem ~2-kB-Block liegt im Millisekundenbereich.
 */
export class MarkdownSpalte {
	readonly scrollEl: HTMLElement;

	private container: HTMLElement;
	private bloecke = new Map<number, HTMLElement>();
	private renderKind: Component | null = null;
	private darstellung: Darstellung = "gerendert";
	private vorschau: Vorschau | null = null;
	private datei: TFile | null = null;
	private lauf = 0;

	/** Wird gerufen, wenn sich Hoehen geaendert haben koennen. */
	beiVermessungNoetig: (() => void) | null = null;

	constructor(
		private app: App,
		wurzel: HTMLElement,
		private eltern: Component,
		/** Getter statt Wert: eine Aenderung im Einstellungs-Tab wirkt sofort,
		 *  ohne die Ansicht neu zu oeffnen. */
		private readonly eagerLimit: () => number,
	) {
		this.scrollEl = wurzel.createDiv({ cls: "ocr-md-scroll" });
		// `markdown-rendered` ist die Klasse, an der Obsidians Typographie-
		// CSS haengt — ohne sie waeren Ueberschriften und Listen nackt.
		this.container = this.scrollEl.createDiv({
			cls: "ocr-md-inhalt markdown-rendered",
		});
	}

	elemente(): Map<number, HTMLElement> {
		return this.bloecke;
	}

	darstellungSetzen(wert: Darstellung): void {
		if (this.darstellung === wert) return;
		this.darstellung = wert;
		void this.zeichnen();
	}

	aktuelleDarstellung(): Darstellung {
		return this.darstellung;
	}

	async oeffnen(
		datei: TFile,
		vorschau: Vorschau,
		darstellung: Darstellung,
	): Promise<void> {
		this.datei = datei;
		this.vorschau = vorschau;
		this.darstellung = darstellung;
		await this.zeichnen();
	}

	leeren(leertext?: string): void {
		this.lauf++;
		this.renderKind?.unload();
		this.renderKind = null;
		this.bloecke.clear();
		this.container.empty();
		this.vorschau = null;
		this.datei = null;
		if (leertext !== undefined) {
			this.container.createDiv({ cls: "ocr-leer", text: leertext });
		}
	}

	private async zeichnen(): Promise<void> {
		const vorschau = this.vorschau;
		const datei = this.datei;
		if (vorschau === null || datei === null) return;

		const lauf = ++this.lauf;
		// Pro geoeffneter Datei eine eigene Kind-Komponente: ohne das leckt jeder
		// Dateiwechsel die Render-Kinder von MarkdownRenderer.
		this.renderKind?.unload();
		const kind = new Component();
		this.eltern.addChild(kind);
		this.renderKind = kind;

		this.bloecke.clear();
		this.container.empty();

		if (vorschau.bloecke.length === 0) {
			this.container.createDiv({
				cls: "ocr-leer",
				text:
					"Keine Seitenmarker gefunden. Stammt diese Datei aus pdf2md.py? " +
					"Erwartet wird pro Seite eine Zeile „%% S. n %%“.",
			});
			return;
		}

		const eager = vorschau.bloecke.length <= this.eagerLimit();
		// Der Nicht-Eager-Fall rendert bewusst NICHT nach — es gibt keinen
		// Nachlader. Das muss dastehen: eine Spalte voller „…" sieht sonst aus
		// wie ein haengendes Laden, nicht wie eine Entscheidung.
		if (!eager && this.darstellung === "gerendert") {
			this.container.createDiv({
				cls: "ocr-leer ocr-md-hinweis",
				text:
					`${vorschau.bloecke.length} Seiten — über der Grenze von ${this.eagerLimit()}. ` +
					"Die Seiten bleiben ungerendert; „Quelltext“ zeigt den Text vollständig.",
			});
		}

		for (const block of vorschau.bloecke) {
			const el = this.container.createDiv({ cls: "ocr-md-seite" });
			el.dataset["seite"] = String(block.nr);
			this.kopfBauen(el, block);
			const koerper = el.createDiv({ cls: "ocr-md-koerper" });
			this.bloecke.set(block.nr, el);

			if (this.darstellung === "quelltext") {
				koerper.createEl("pre", { cls: "ocr-md-quelltext" }).createEl("code", {
					text: block.markdown,
				});
				continue;
			}
			if (!eager) {
				koerper.createDiv({ cls: "ocr-md-platzhalter", text: "…" });
				continue;
			}
			await MarkdownRenderer.render(
				this.app,
				block.markdown,
				koerper,
				datei.path,
				kind,
			);
			if (lauf !== this.lauf) return; // Datei wurde inzwischen gewechselt
			this.einbettungenNachbessern(koerper, datei);
		}

		this.beiVermessungNoetig?.();
	}

	private kopfBauen(el: HTMLElement, block: Seitenblock): void {
		const kopf = el.createDiv({ cls: "ocr-md-seitenkopf" });
		kopf.createSpan({ cls: "ocr-md-seitenzahl", text: `S. ${block.nr}` });
		if (block.herkunft !== undefined) {
			const badge = kopf.createSpan({
				cls: `ocr-badge ocr-badge-${block.herkunft}`,
				text:
					block.herkunft === "textlayer"
						? "Textlayer"
						: block.herkunft === "ocr"
							? "OCR"
							: "Diagramm",
			});
			badge.setAttribute(
				"aria-label",
				block.herkunft === "textlayer"
					? "Verlustfrei aus dem Textlayer übernommen"
					: block.herkunft === "ocr"
						? "Durch das Modell gelesen — Wortfehler möglich"
						: "Als Seitenbild eingebettet, Text im Callout",
			);
		}
		if (block.layout !== undefined) {
			kopf.createSpan({ cls: "ocr-md-layout", text: block.layout });
		}
	}

	/**
	 * `MarkdownRenderer.render` loest interne Einbettungen nicht auf — es setzt
	 * nur einen `.internal-embed`-Platzhalter. In pdf2md-Ausgaben betrifft das
	 * ausschliesslich die Diagrammbilder (`![[…png]]`, pdf2md.py:1619).
	 *
	 * Sollte Obsidian Bild-Einbettungen doch selbst aufloesen, findet die
	 * Schleife nichts und ist folgenlos — dann kann sie ersatzlos entfallen.
	 */
	private einbettungenNachbessern(wurzel: HTMLElement, quelle: TFile): void {
		const platzhalter = wurzel.querySelectorAll<HTMLElement>(".internal-embed");
		for (let i = 0; i < platzhalter.length; i++) {
			const span = platzhalter[i];
			if (span === undefined) continue;
			if (span.hasClass("is-loaded") || span.querySelector("img") !== null) continue;
			const src = span.getAttribute("src");
			if (src === null || src.length === 0) continue;
			const ziel = this.app.metadataCache.getFirstLinkpathDest(src, quelle.path);
			if (ziel === null) {
				span.addClass("ocr-embed-fehlt");
				span.setText(`Bild nicht gefunden: ${src}`);
				continue;
			}
			if (!BILDENDUNGEN.has(ziel.extension.toLowerCase())) continue;
			span.empty();
			span.addClass("is-loaded");
			const img = span.createEl("img", { cls: "ocr-embed-bild" });
			img.src = this.app.vault.getResourcePath(ziel);
			img.alt = ziel.name;
			// Erst wenn das Bild da ist, stimmt die Hoehe — dann neu vermessen.
			img.addEventListener("load", () => this.beiVermessungNoetig?.(), {
				once: true,
			});
		}
	}
}
