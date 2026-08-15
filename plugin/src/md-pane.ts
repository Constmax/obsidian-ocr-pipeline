import { App, Component, MarkdownRenderer, TFile } from "obsidian";

import type { Herkunft, Seitenblock, Vorschau } from "./typen.ts";

const BILDENDUNGEN = new Set(["png", "jpg", "jpeg", "webp", "gif", "avif", "svg"]);

/** Beschriftung der Herkunft — eine Stelle fuer Badge und Statuszeile. */
export const HERKUNFT_LABEL: Record<Herkunft, string> = {
	textlayer: "Textlayer",
	ocr: "OCR",
	diagramm: "Diagramm",
};

const HERKUNFT_ERKLAERUNG: Record<Herkunft, string> = {
	textlayer: "Verlustfrei aus dem Textlayer übernommen",
	ocr: "Durch das Modell gelesen — Wortfehler möglich",
	diagramm: "Als Seitenbild eingebettet, Text im Callout",
};

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
	private bearbeitet: number | null = null;

	/** Wird gerufen, wenn sich Hoehen geaendert haben koennen. */
	beiVermessungNoetig: (() => void) | null = null;

	/** Werkbank-Modus: den geaenderten Block in die Datei schreiben. Liefert
	 *  `false`, wenn nicht geschrieben wurde — dann bleibt das Feld offen und
	 *  der getippte Text ist nicht verloren. */
	beiSpeichern: ((nr: number, text: string) => Promise<boolean>) | null = null;

	/** Feld geoeffnet oder geschlossen — die Ansicht zieht ihre Statuszeile
	 *  nach. Noetig, weil Speichern und Verwerfen auch ueber Tasten IM Feld
	 *  laufen, an denen die Ansicht nicht beteiligt ist. */
	beiBearbeitungswechsel: (() => void) | null = null;

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
		this.bearbeitet = null;
		if (leertext !== undefined) {
			this.container.createDiv({ cls: "ocr-leer", text: leertext });
		}
	}

	private async zeichnen(): Promise<void> {
		const vorschau = this.vorschau;
		const datei = this.datei;
		if (vorschau === null || datei === null) return;

		const lauf = ++this.lauf;
		// Ein offenes Feld ueberlebt kein Neuzeichnen (Dateiwechsel, Umschalter):
		// sein DOM-Knoten wird gleich weggeraeumt.
		this.bearbeitet = null;
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

			await this.koerperFuellen(koerper, block, eager, kind, datei);
			if (lauf !== this.lauf) return; // Datei wurde inzwischen gewechselt
		}

		this.beiVermessungNoetig?.();
	}

	/** Der Inhalt eines Seitenblocks — beim Zeichnen und nach dem Bearbeiten
	 *  derselbe Weg, damit ein gespeicherter Block genau so aussieht wie ein
	 *  frisch geoeffneter. */
	private async koerperFuellen(
		koerper: HTMLElement,
		block: Seitenblock,
		eager: boolean,
		kind: Component,
		datei: TFile,
	): Promise<void> {
		koerper.empty();
		if (this.darstellung === "quelltext") {
			koerper.createEl("pre", { cls: "ocr-md-quelltext" }).createEl("code", {
				text: block.markdown,
			});
			return;
		}
		if (!eager) {
			koerper.createDiv({ cls: "ocr-md-platzhalter", text: "…" });
			return;
		}
		await MarkdownRenderer.render(this.app, block.markdown, koerper, datei.path, kind);
		this.einbettungenNachbessern(koerper, datei);
	}

	// ── Werkbank-Modus: eine Seite an Ort und Stelle korrigieren ──────────────
	//
	// Bearbeitet wird IMMER der Quelltext des Blocks, auch in der gerenderten
	// Darstellung: die Vorschau ist Markdown, und ein WYSIWYG-Feld muesste
	// zurueckuebersetzen — dabei geht genau das verloren (Fussnoten,
	// `![[…]]`-Einbettungen), was hier haeufig vorkommt.

	/** Der Block zu einer Seitennummer — fuer Kopf- und Statuszeile. */
	blockZu(nr: number): Seitenblock | null {
		return this.vorschau?.bloecke.find((b) => b.nr === nr) ?? null;
	}

	/** Seitennummer des offenen Bearbeitungsfeldes, sonst null. */
	bearbeiteteSeite(): number | null {
		return this.bearbeitet;
	}

	/** Oeffnet das Bearbeitungsfeld auf Seite `nr`. `false`, wenn es die Seite
	 *  nicht gibt oder bereits ein Feld offen ist. */
	bearbeitenStarten(nr: number): boolean {
		if (this.bearbeitet !== null) return false;
		const el = this.bloecke.get(nr);
		const block = this.blockZu(nr);
		if (el === undefined || block === null) return false;
		const koerper = el.querySelector<HTMLElement>(".ocr-md-koerper");
		if (koerper === null) return false;

		koerper.empty();
		el.addClass("ocr-md-seite-bearbeitet");
		const feld = koerper.createEl("textarea", { cls: "ocr-md-editfeld" });
		feld.value = block.markdown;
		feld.spellcheck = false;
		feld.addEventListener("input", () => this.feldHoeheAnpassen(feld));
		feld.addEventListener("keydown", (e) => {
			// Esc darf hier NICHT bis zur Ansicht durchlaufen: dort raeumt es die
			// Auswahl ab und die halb getippte Korrektur waere weg.
			if (e.key === "Escape") {
				e.preventDefault();
				e.stopPropagation();
				this.bearbeitenAbbrechen();
				return;
			}
			if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
				e.preventDefault();
				e.stopPropagation();
				void this.bearbeitenSpeichern();
			}
		});

		const zeile = koerper.createDiv({ cls: "ocr-md-editzeile" });
		const speichern = zeile.createEl("button", {
			cls: "ocr-knopf ocr-knopf-klein ocr-knopf-haupt",
			text: "Speichern (⌘↩)",
		});
		speichern.addEventListener("click", () => void this.bearbeitenSpeichern());
		const abbrechen = zeile.createEl("button", {
			cls: "ocr-knopf ocr-knopf-klein",
			text: "Abbrechen (Esc)",
		});
		abbrechen.addEventListener("click", () => this.bearbeitenAbbrechen());

		this.bearbeitet = nr;
		this.feldHoeheAnpassen(feld);
		feld.focus();
		this.beiVermessungNoetig?.();
		this.beiBearbeitungswechsel?.();
		return true;
	}

	/** Verwirft die Aenderung und stellt die Darstellung wieder her. */
	bearbeitenAbbrechen(): void {
		const nr = this.bearbeitet;
		if (nr === null) return;
		this.bearbeitet = null;
		void this.blockNeuZeichnen(nr);
	}

	/** Schreibt ueber `beiSpeichern` zurueck. `false` heisst: nichts geschrieben,
	 *  das Feld bleibt offen. */
	async bearbeitenSpeichern(): Promise<boolean> {
		const nr = this.bearbeitet;
		if (nr === null) return false;
		const block = this.blockZu(nr);
		const el = this.bloecke.get(nr);
		const feld = el?.querySelector<HTMLTextAreaElement>(".ocr-md-editfeld");
		if (block === null || feld === null || feld === undefined) return false;

		const text = feld.value.trim();
		if (text === block.markdown) {
			// Nichts geaendert — kein Schreibvorgang, kein „handbearbeitet".
			this.bearbeitet = null;
			await this.blockNeuZeichnen(nr);
			return true;
		}
		const ok = (await this.beiSpeichern?.(nr, text)) ?? false;
		if (!ok) return false;
		block.markdown = text;
		this.bearbeitet = null;
		await this.blockNeuZeichnen(nr);
		return true;
	}

	private async blockNeuZeichnen(nr: number): Promise<void> {
		const el = this.bloecke.get(nr);
		const block = this.blockZu(nr);
		const datei = this.datei;
		const kind = this.renderKind;
		this.beiBearbeitungswechsel?.();
		if (el === undefined || block === null || datei === null || kind === null) return;
		const koerper = el.querySelector<HTMLElement>(".ocr-md-koerper");
		if (koerper === null) return;
		el.removeClass("ocr-md-seite-bearbeitet");
		const eager = (this.vorschau?.bloecke.length ?? 0) <= this.eagerLimit();
		await this.koerperFuellen(koerper, block, eager, kind, datei);
		this.beiVermessungNoetig?.();
	}

	/** Das Feld waechst mit dem Text: eine feste Hoehe hiesse, in einem
	 *  Guckloch zu korrigieren, waehrend daneben die ganze Seite steht.
	 *
	 *  Ueber eine Custom Property statt `style.height`: dieselbe Bauart wie die
	 *  Spaltenbreiten (`--ocr-spalte-basis`), und die Hoehenformel bleibt im
	 *  Stylesheet. Erst `auto`, dann messen — das Lesen von `scrollHeight`
	 *  erzwingt den Umbruch dazwischen. */
	private feldHoeheAnpassen(feld: HTMLTextAreaElement): void {
		feld.setCssProps({ "--ocr-feld-hoehe": "auto" });
		feld.setCssProps({ "--ocr-feld-hoehe": `${feld.scrollHeight + 2}px` });
	}

	private kopfBauen(el: HTMLElement, block: Seitenblock): void {
		const kopf = el.createDiv({ cls: "ocr-md-seitenkopf" });
		kopf.createSpan({ cls: "ocr-md-seitenzahl", text: `S. ${block.nr}` });
		if (block.herkunft !== undefined) {
			const badge = kopf.createSpan({
				cls: `ocr-badge ocr-badge-${block.herkunft}`,
				text: HERKUNFT_LABEL[block.herkunft],
			});
			badge.setAttribute("aria-label", HERKUNFT_ERKLAERUNG[block.herkunft]);
		}
		if (block.layout !== undefined) {
			kopf.createSpan({ cls: "ocr-md-layout", text: block.layout });
		}
	}

	/**
	 * `MarkdownRenderer.render` loest interne Einbettungen nicht auf — es setzt
	 * nur einen `.internal-embed`-Platzhalter. In pdf2md-Ausgaben betrifft das
	 * ausschliesslich die Diagrammbilder (`![[…png]]`, pdf2md.py,
	 * diagramm_bild()).
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
