// Die dreispaltige Ansicht: Vorschauen | Original-PDF | Markdown.
//
// Diese Datei ist die Verdrahtung — die Spalten selbst leben in
// `seitenleiste.ts`, `pdf-pane.ts` und `md-pane.ts`, die Kopplung in
// `sync.ts`, der Zustand in `dateiaktionen.ts`. Hier wird aus ihnen eine
// Bedienoberflaeche: Kopfzeilen, Ziehgriffe, Knöpfe, Tastatur.

import {
	App,
	ItemView,
	Menu,
	Modal,
	Notice,
	Setting,
	SuggestModal,
	TFile,
	ViewStateResult,
	WorkspaceLeaf,
	loadPdfJs,
	setIcon,
} from "obsidian";

import type OcrVorschauPlugin from "./main.ts";
import { Bestand, type Bestandseintrag } from "./dateiaktionen.ts";
import { MarkdownSpalte, type Darstellung } from "./md-pane.ts";
import { PdfSpalte } from "./pdf-pane.ts";
import { Seitenleiste } from "./seitenleiste.ts";
import { Kopplung } from "./sync.ts";
import type { Ordnerlage, Vorschau } from "./typen.ts";
import { vorschauParsen, vorschauZusammenbauen } from "./vorschau-parser.ts";

export const ANSICHT_TYP = "ocr-vorschau-abgleich";

const SPALTE_MIN = 10;
const GEPRUEFT_BIS_MS = 800;

export class OcrAbgleichAnsicht extends ItemView {
	aktiveName: string | null = null;

	private bestand!: Bestand;
	private seitenleiste!: Seitenleiste;
	private pdfSpalte!: PdfSpalte;
	private mdSpalte!: MarkdownSpalte;
	private kopplung!: Kopplung;
	private rahmen!: HTMLElement;
	private pdfKopfTitel!: HTMLElement;
	private pdfSeitenAnzeige!: HTMLElement;
	private pdfFehler!: HTMLElement;
	private zoomAnzeige!: HTMLElement;
	private umschalter!: HTMLElement;
	private pdfDatei: TFile | null = null;
	private pdfSeiten = 0;
	private oeffnenLauf = 0;
	private geprueftBisTimer: number | null = null;
	private mdSpeicherTimer: number | null = null;
	private mdSchreibKette: Promise<void> = Promise.resolve();
	private editierenKnopf!: HTMLButtonElement;

	constructor(
		leaf: WorkspaceLeaf,
		private plugin: OcrVorschauPlugin,
	) {
		super(leaf);
		this.navigation = true;
	}

	getViewType(): string {
		return ANSICHT_TYP;
	}

	getDisplayText(): string {
		return "OCR-Abgleich";
	}

	getIcon(): string {
		return "columns-3";
	}

	async onOpen(): Promise<void> {
		this.bestand = this.plugin.bestand;

		this.rahmen = this.contentEl.createDiv({ cls: "ocr-abgleich" });

		// ── Links: Vorschau-Liste ──────────────────────────────────────────────
		const listeSpalte = this.rahmen.createDiv({ cls: "ocr-spalte ocr-spalte-liste" });
		this.seitenleiste = new Seitenleiste(
			listeSpalte,
			() => this.plugin.einstellungen.vorschauOrdner,
		);
		this.seitenleiste.beiAuswahl = (name) => void this.oeffnen(name);
		// Der Knopf muss NEU ABGLEICHEN, nicht nur neu zeichnen: `aktualisieren`
		// liest bloss `bestand.eintraege`, und genau die sind veraltet, wenn der
		// Nutzer den Knopf drueckt.
		this.seitenleiste.beiAktualisieren = () => void this.neuAbgleichen();
		this.seitenleiste.beiEinstellungen = () => einstellungenOeffnen(this.plugin);

		// ── Mitte: Original-PDF ─────────────────────────────────────────────────
		const griff1 = this.rahmen.createDiv({
			cls: "ocr-griff",
			attr: { "aria-label": "Spaltenbreite anpassen", title: "Spaltenbreite anpassen" },
		});
		const pdfSpalte = this.rahmen.createDiv({ cls: "ocr-spalte ocr-spalte-pdf" });
		const pdfKopf = pdfSpalte.createDiv({ cls: "ocr-spaltenkopf" });
		const pdfZeile = pdfKopf.createDiv({ cls: "ocr-kopf-zeile" });
		this.pdfKopfTitel = pdfZeile.createSpan({ cls: "ocr-kopf-titel", text: "Original-PDF" });
		this.pdfSeitenAnzeige = pdfZeile.createSpan({ cls: "ocr-kopf-seiten" });
		const pdfWerkzeuge = pdfKopf.createDiv({ cls: "ocr-kopf-werkzeuge" });
		this.kleinknopf(pdfWerkzeuge, "−", "Verkleinern", () => this.zoomen(-0.25));
		this.zoomAnzeige = pdfWerkzeuge.createSpan({ cls: "ocr-kopf-zoom", text: "100 %" });
		this.kleinknopf(pdfWerkzeuge, "+", "Vergrößern", () => this.zoomen(0.25));
		const imViewer = this.kleinknopf(pdfWerkzeuge, "Im PDF-Viewer öffnen", "", () => {
			void this.imPdfViewerOeffnen();
		});
		imViewer.addClass("ocr-knopf-haupt");
		this.pdfFehler = pdfKopf.createDiv({ cls: "ocr-fehlbanner" });
		this.pdfFehler.hide();

		this.pdfSpalte = new PdfSpalte(this.app, pdfSpalte, () => this.plugin.einstellungen.pdfZoomMax);
		this.pdfSpalte.beiVermessungNoetig = () => this.kopplung.neuVermessen();
		this.pdfSpalte.beiGeladen = (name, seiten) => this.pdfGeladen(name, seiten);
		this.pdfSpalte.beiFehler = (fehler) => this.pdfFehlerAnzeigen(fehler);

		// ── Rechts: Markdown ────────────────────────────────────────────────────
		const griff2 = this.rahmen.createDiv({
			cls: "ocr-griff",
			attr: { "aria-label": "Spaltenbreite anpassen", title: "Spaltenbreite anpassen" },
		});
		const mdSpalte = this.rahmen.createDiv({ cls: "ocr-spalte ocr-spalte-md" });
		const mdKopf = mdSpalte.createDiv({ cls: "ocr-spaltenkopf" });
		const mdZeile = mdKopf.createDiv({ cls: "ocr-kopf-zeile" });
		mdZeile.createSpan({ cls: "ocr-kopf-titel", text: "Markdown" });
		this.umschalter = mdZeile.createDiv({ cls: "ocr-umschalter" });
		// Der Wert steht im dataset, nicht in der Beschriftung: ein Vergleich auf
		// den sichtbaren Text ("Gerendert") trifft den Aufzaehlungswert
		// ("gerendert") nie, und die Markierung bliebe fuer immer stehen.
		const gerendertKnopf = this.umschalter.createEl("button", {
			cls: "ocr-umschalter-option",
			text: "Gerendert",
		});
		gerendertKnopf.dataset["darstellung"] = "gerendert";
		const quelltextKnopf = this.umschalter.createEl("button", {
			cls: "ocr-umschalter-option",
			text: "Quelltext",
		});
		quelltextKnopf.dataset["darstellung"] = "quelltext";
		gerendertKnopf.addEventListener("click", () => this.darstellungSetzen("gerendert"));
		quelltextKnopf.addEventListener("click", () => this.darstellungSetzen("quelltext"));
		const mdWerkzeuge = mdZeile.createDiv({ cls: "ocr-kopf-werkzeuge" });
		const annehmen = this.kleinknopf(mdWerkzeuge, "Annehmen", "a — als korrekt übernehmen", () => {
			void this.entscheiden("akzeptiert");
		});
		annehmen.addClass("ocr-knopf-annehmen");
		const ablehnen = this.kleinknopf(mdWerkzeuge, "Ablehnen", "x — zur Überarbeitung", () => {
			void this.entscheiden("abgelehnt");
		});
		ablehnen.addClass("ocr-knopf-ablehnen");
		this.kleinknopf(mdWerkzeuge, "In Obsidian öffnen", "", () => void this.inObsidianOeffnen());
		this.editierenKnopf = mdWerkzeuge.createEl("button", {
			cls: "ocr-ikonknopf",
		});
		setIcon(this.editierenKnopf.createSpan({ cls: "ocr-ikon" }), "pencil");
		this.editierenKnopf.addEventListener("click", () => this.editierenUmschalten());
		this.editierenKnopfAktualisieren();
		const mehr = mdWerkzeuge.createEl("button", {
			cls: "ocr-ikonknopf",
			attr: { "aria-label": "Mehr", title: "Mehr" },
		});
		setIcon(mehr.createSpan({ cls: "ocr-ikon" }), "horizontal-three-dots");
		mehr.addEventListener("click", (e) => this.mehrMenu(e));

		this.mdSpalte = new MarkdownSpalte(
			this.app,
			mdSpalte,
			this,
			() => this.plugin.einstellungen.mdEagerLimit,
		);
		this.mdSpalte.beiVermessungNoetig = () => this.kopplung.neuVermessen();
		this.mdSpalte.beiAenderung = () => this.aenderungSpeichernAnstossen();
		this.mdSpalte.beiFokusVerlust = () => void this.aenderungSofortSpeichern();
		// Startmarkierung aus den Einstellungen, nicht fest auf „Gerendert":
		// wer „Quelltext" als Standard gewaehlt hat, sah sonst die falsche
		// Schaltflaeche hervorgehoben.
		this.umschalterMarkieren(this.plugin.einstellungen.markdownAnsicht);

		// ── Kopplung und Breiten ────────────────────────────────────────────────
		this.kopplung = new Kopplung({
			pdf: { scrollEl: this.pdfSpalte.scrollEl, elemente: () => this.pdfSpalte.elemente() },
			md: { scrollEl: this.mdSpalte.scrollEl, elemente: () => this.mdSpalte.elemente() },
		});
		this.kopplung.beiSeite = (p) => this.seiteAnzeigen(p);

		this.einstellungenAnwenden();
		this.griffVerdrahten(griff1, 0, 1);
		this.griffVerdrahten(griff2, 1, 2);

		this.tastenRegistrieren();

		this.aktualisieren();
	}

	async onClose(): Promise<void> {
		await this.aenderungSofortSpeichern();
		this.kopplung?.zerstoeren();
		this.pdfSpalte?.zerstoeren();
		this.mdSpalte?.leeren();
		if (this.geprueftBisTimer !== null) window.clearTimeout(this.geprueftBisTimer);
	}

	getState(): Record<string, unknown> {
		return { datei: this.aktiveName };
	}

	async setState(zustand: unknown, ergebnis: ViewStateResult): Promise<void> {
		ergebnis.history = false;
		const datei = (zustand as { datei?: unknown } | null)?.datei;
		if (typeof datei === "string") await this.oeffnen(datei);
	}

	/** Vom Plugin-Command gerufen: naechster Eintrag der gefilterten Liste. */
	weiter(): void {
		const name = this.aktiveName;
		if (name === null) {
			const erster = this.seitenleiste.verschiebeAuswahl(1);
			if (erster !== null) void this.oeffnen(erster);
			return;
		}
		const naechster = this.seitenleiste.naechsterNach(name);
		if (naechster !== null) void this.oeffnen(naechster);
	}

	/** Erster Eintrag der gefilterten Liste (ohne Auswahl zu verschieben). */
	ersterSichtbar(): string | null {
		return this.seitenleiste.ersterSichtbar();
	}

	/** Bestand frisch vom Dateisystem einlesen, dann neu zeichnen. */
	private async neuAbgleichen(): Promise<void> {
		await this.bestand.abgleichen();
		this.aktualisieren();
	}

	aktualisieren(): void {
		const ordnerFehlt =
			this.app.vault.getFolderByPath(this.plugin.einstellungen.vorschauOrdner) === null;
		this.seitenleiste.aktualisieren(this.bestand.eintraege, ordnerFehlt);
		// Die geoeffnete Datei ist verschwunden (verschoben, geloescht) —
		// dann zurueck zur Liste.
		if (
			this.aktiveName !== null &&
			!this.bestand.eintraege.some((b) => b.name === this.aktiveName)
		) {
			this.keineAuswahl();
		}
	}

	// ── Öffnen und PDF-Auflösung ──────────────────────────────────────────────

	async oeffnen(name: string): Promise<void> {
		await this.aenderungSofortSpeichern();
		const lauf = ++this.oeffnenLauf;
		const bestand = this.bestand.eintraege.find((b) => b.name === name);
		if (bestand === undefined) {
			new Notice(`„${name}“ ist nicht mehr im Vorschau-Bestand.`);
			this.aktualisieren();
			return;
		}
		this.aktiveName = name;
		this.seitenleiste.setzeAuswahl(name);
		this.app.workspace.requestSaveLayout();

		let vorschau: Vorschau | null = null;
		try {
			const text = await this.app.vault.read(bestand.datei);
			if (lauf !== this.oeffnenLauf) return;
			vorschau = vorschauParsen(text);
		} catch (fehler) {
			console.error("OCR-Vorschau: Datei nicht lesbar", fehler);
			new Notice(`„${name}“ konnte nicht gelesen werden.`);
		}
		if (lauf !== this.oeffnenLauf) return;

		if (vorschau !== null) {
			await this.mdSpalte.oeffnen(
				bestand.datei,
				vorschau,
				this.plugin.einstellungen.markdownAnsicht,
			);
		} else {
			this.mdSpalte.leeren("Die Datei konnte nicht gelesen werden.");
		}
		if (lauf !== this.oeffnenLauf) return;

		const pdfDatei = await this.originalFinden(bestand, vorschau);
		if (lauf !== this.oeffnenLauf) return;
		this.pdfDatei = pdfDatei;
		if (pdfDatei === null) {
			await this.pdfSpalte.oeffnen(null);
			this.pdfFehlerAnzeigen("Original-PDF nicht gefunden.");
		} else {
			this.pdfFehlerAnzeigen(null);
			await this.pdfSpalte.oeffnen(pdfDatei);
		}

		this.kopplung.neuVermessen();
		// Lesefortschritt wiederherstellen — ein 40-Seiten-Durchgang soll
		// fortsetzbar sein.
		const bis = bestand.eintrag["geprueft-bis"];
		if (bis !== null && bis > 1) this.kopplung.zuSeite(bis);
	}

	/**
	 * Original-PDF in dieser Reihenfolge suchen:
	 * 1. `quelle-pdf` aus dem Frontmatter (bzw. der `Quelle: [[…]]`-Zeile),
	 * 2. erster Wikilink aus dem Metadaten-Cache — ueberlebt ein YAML-kaputtes
	 *    `quelle-pdf`, das pdf2md.py, main() — Frontmatter — JSON-gequotet schreibt,
	 * 3. Vault-Suche nach `<stem>.pdf`,
	 * 4. die manuelle Zuordnung aus dem Manifest (`quelle-pdf-manuell`).
	 * Die manuelle Zuordnung steht NIE im Frontmatter: die .md ist erzeugte
	 * Ausgabe und v1 schreibt nicht hinein.
	 */
	private async originalFinden(
		bestand: Bestandseintrag,
		vorschau: Vorschau | null,
	): Promise<TFile | null> {
		const datei = bestand.datei;
		const kandidaten: Array<string | null> = [];
		if (vorschau !== null) kandidaten.push(vorschau.quellePdf);
		const link = this.app.metadataCache.getFileCache(datei)?.links?.[0]?.link;
		if (link !== undefined && link.length > 0) kandidaten.push(link);
		kandidaten.push(
			this.app.vault.getFiles().find(
				(f) => f.extension === "pdf" && f.basename === datei.basename,
			)?.path ?? null,
		);
		kandidaten.push(bestand.eintrag["quelle-pdf-manuell"]);

		for (const kandidat of kandidaten) {
			if (kandidat === null || kandidat.length === 0) continue;
			const ziel = this.app.metadataCache.getFirstLinkpathDest(kandidat, datei.path);
			if (ziel !== null) return ziel;
		}
		return null;
	}

	// ── Entscheiden und Navigieren ────────────────────────────────────────────

	private async entscheiden(lage: Ordnerlage): Promise<void> {
		await this.aenderungSofortSpeichern();
		const name = this.seitenleiste.ausgewaehltName();
		if (name === null) return;
		const ergebnis = await this.bestand.entscheiden(name, lage);
		if (ergebnis === "kollision") {
			this.kollisionMelden(name, lage);
			return;
		}
		if (ergebnis !== "ok") return;

		const label =
			lage === "akzeptiert" ? "Angenommen" : lage === "abgelehnt" ? "Abgelehnt" : "Zurückgesetzt";
		const notice = new Notice(`${label}: ${name.replace(/\.md$/, "")}`, 6000);
		const rueckgaengig = notice.messageEl.createEl("button", {
			cls: "ocr-knopf ocr-knopf-klein",
			text: "Rückgängig",
		});
		rueckgaengig.addEventListener("click", () => void this.rueckgaengig());

		this.aktualisieren();
		if (lage === "offen") return;
		// Automatisch zum naechsten passenden Eintrag — das ist es, was
		// „schnell" ueberhaupt herstellt.
		const naechster = this.seitenleiste.naechsterNach(name);
		if (naechster !== null) void this.oeffnen(naechster);
		else this.keineAuswahl();
	}

	/**
	 * Der Zielname ist belegt — praktisch immer die alte Fassung einer
	 * Neukonvertierung. Statt eines Fehlers den einen Weg anbieten, der hier
	 * weiterfuehrt. „Alte Fassung ersetzen" loescht nichts: die alte Datei
	 * wandert unter `<stem>-<ocr-datum>.md` nach _abgelehnt und behaelt dort
	 * einen eigenen Eintrag.
	 */
	private kollisionMelden(name: string, lage: Ordnerlage): void {
		const stem = name.replace(/\.md$/, "");
		const notice = new Notice(
			`„${stem}“ liegt im Zielordner bereits — vermutlich die vorige Fassung.`,
			10000,
		);
		const knopf = notice.messageEl.createEl("button", {
			cls: "ocr-knopf ocr-knopf-klein",
			text: "Alte Fassung ersetzen und fortfahren",
		});
		knopf.addEventListener("click", () => {
			notice.hide();
			void this.ersetzenUndEntscheiden(name, lage);
		});
	}

	private async ersetzenUndEntscheiden(name: string, lage: Ordnerlage): Promise<void> {
		if (!(await this.bestand.alteFassungErsetzen(name))) {
			new Notice(`OCR-Vorschau: „${name}“ konnte nicht ersetzt werden.`);
			this.aktualisieren();
			return;
		}
		await this.entscheiden(lage);
	}

	private async rueckgaengig(): Promise<void> {
		const name = await this.bestand.rueckgaengig();
		this.aktualisieren();
		if (name !== null) void this.oeffnen(name);
	}

	/** Esc — zurueck zur Liste. */
	private keineAuswahl(): void {
		this.aktiveName = null;
		this.seitenleiste.setzeAuswahl(null);
		this.mdSpalte.leeren("Keine Vorschau ausgewählt — links einen Eintrag wählen.");
		void this.pdfSpalte.oeffnen(null);
		this.pdfGeladen(null, 0);
		this.app.workspace.requestSaveLayout();
	}

	// ── Kopfzeilen ────────────────────────────────────────────────────────────

	private pdfGeladen(name: string | null, seiten: number): void {
		this.pdfSeiten = seiten;
		this.pdfKopfTitel.setText(name === null ? "Original-PDF" : name.replace(/\.pdf$/, ""));
		this.pdfSeitenAnzeige.setText(seiten > 0 ? `S. 1 / ${seiten}` : "");
	}

	private pdfFehlerAnzeigen(fehler: string | null): void {
		this.pdfFehler.empty();
		if (fehler === null) {
			this.pdfFehler.hide();
			return;
		}
		this.pdfFehler.show();
		this.pdfFehler.createSpan({ text: fehler });
		const zuordnen = this.pdfFehler.createEl("button", {
			cls: "ocr-knopf ocr-knopf-klein",
			text: "PDF zuordnen…",
		});
		zuordnen.addEventListener("click", () => this.pdfZuordnen());
	}

	private seiteAnzeigen(p: number): void {
		if (this.pdfDatei === null || this.pdfSeiten === 0) return;
		const nr = Math.min(Math.max(Math.floor(p), 1), this.pdfSeiten);
		this.pdfSeitenAnzeige.setText(`S. ${nr} / ${this.pdfSeiten}`);
		this.geprueftBisAktualisieren(nr);
	}

	private geprueftBisAktualisieren(nr: number): void {
		const name = this.aktiveName;
		if (name === null) return;
		const bestand = this.bestand.eintraege.find((b) => b.name === name);
		if (bestand === undefined) return;
		const aktuell = bestand.eintrag["geprueft-bis"] ?? 0;
		if (nr <= aktuell) return;
		if (this.geprueftBisTimer !== null) window.clearTimeout(this.geprueftBisTimer);
		this.geprueftBisTimer = window.setTimeout(() => {
			this.geprueftBisTimer = null;
			void this.bestand.eintragAendern(name, { "geprueft-bis": nr });
		}, GEPRUEFT_BIS_MS);
	}

	private aenderungSpeichernAnstossen(): void {
		if (this.mdSpeicherTimer !== null) window.clearTimeout(this.mdSpeicherTimer);
		this.mdSpeicherTimer = window.setTimeout(() => {
			this.mdSpeicherTimer = null;
			void this.aenderungSofortSpeichern();
		}, 500);
	}

	private async aenderungSofortSpeichern(): Promise<void> {
		if (this.mdSpeicherTimer !== null) {
			window.clearTimeout(this.mdSpeicherTimer);
			this.mdSpeicherTimer = null;
		}
		const vorschau = this.mdSpalte?.aktuelleVorschau();
		const datei = this.mdSpalte?.aktuelleDatei();
		if (vorschau === null || vorschau === undefined || datei === null || datei === undefined) return;
		const text = vorschauZusammenbauen(vorschau);
		this.mdSchreibKette = this.mdSchreibKette.then(async () => {
			try {
				await this.app.vault.modify(datei, text);
			} catch (fehler) {
				console.error("OCR-Vorschau: Konvertierten Text speichern fehlgeschlagen", fehler);
				new Notice("OCR-Vorschau: Änderungen konnten nicht in Datei gespeichert werden.");
			}
		});
		await this.mdSchreibKette;
	}

	private darstellungSetzen(darstellung: Darstellung): void {
		void this.aenderungSofortSpeichern();
		this.mdSpalte.darstellungSetzen(darstellung);
		this.umschalterMarkieren(darstellung);
	}

	private umschalterMarkieren(darstellung: Darstellung): void {
		const optionen =
			this.umschalter.querySelectorAll<HTMLElement>(".ocr-umschalter-option");
		for (const option of optionen) {
			option.toggleClass(
				"ocr-umschalter-aktiv",
				option.dataset["darstellung"] === darstellung,
			);
		}
	}

	private editierenUmschalten(): void {
		const neuerStatus = !this.mdSpalte.istEditierbar();
		this.mdSpalte.editierbarSetzen(neuerStatus);
		if (neuerStatus && this.mdSpalte.aktuelleDarstellung() === "gerendert") {
			this.darstellungSetzen("quelltext");
		}
		if (!neuerStatus) {
			void this.aenderungSofortSpeichern();
		}
		this.editierenKnopfAktualisieren();
	}

	private editierenKnopfAktualisieren(): void {
		if (this.editierenKnopf === undefined) return;
		const aktiv = this.mdSpalte.istEditierbar();
		this.editierenKnopf.toggleClass("ocr-ikonknopf-aktiv", aktiv);
		const label = aktiv ? "Bearbeitungsmodus deaktivieren (e)" : "Bearbeiten (e)";
		this.editierenKnopf.setAttribute("aria-label", label);
		this.editierenKnopf.setAttribute("title", label);
	}

	private zoomen(schritt: number): void {
		this.pdfSpalte.zoomen(schritt);
		this.zoomAnzeige.setText(`${Math.round(this.pdfSpalte.aktuelleZoom() * 100)} %`);
	}

	private async imPdfViewerOeffnen(): Promise<void> {
		if (this.pdfDatei === null) return;
		await this.app.workspace.openLinkText(this.pdfDatei.path, "");
	}

	private async inObsidianOeffnen(): Promise<void> {
		if (this.aktiveName === null) return;
		const bestand = this.bestand.eintraege.find((b) => b.name === this.aktiveName);
		if (bestand === undefined) return;
		await this.app.workspace.openLinkText(bestand.datei.path, "");
	}

	private mehrMenu(e: MouseEvent): void {
		const name = this.aktiveName;
		if (name === null) return;
		const bestand = this.bestand.eintraege.find((b) => b.name === name);
		if (bestand === undefined) return;
		const menu = new Menu();
		menu.addItem((i) =>
			i
				.setTitle("Notiz…")
				.setIcon("sticky-note")
				.onClick(() => this.notizDialog(bestand)),
		);
		if (bestand.eintrag.status === "neu-erzeugt") {
			menu.addItem((i) =>
				i
					.setTitle("Alte Fassung ersetzen")
					.setIcon("archive")
					.onClick(() => {
						void this.bestand.alteFassungErsetzen(name).then(() => {
							this.aktualisieren();
						});
					}),
			);
		}
		menu.addItem((i) =>
			i
				.setTitle("Status zurücksetzen")
				.setIcon("rotate-ccw")
				// Ueber denselben Weg wie die Hauptknoepfe, damit auch hier die
				// Namenskollision aufgefangen wird statt in einem Fehler zu enden.
				.onClick(() => void this.entscheiden("offen")),
		);
		menu.addItem((i) =>
			i
				.setTitle("Pfad kopieren")
				.setIcon("clipboard-copy")
				.onClick(() => {
					navigator.clipboard
						.writeText(bestand.datei.path)
						.then(() => new Notice("Pfad kopiert."))
						.catch(() => new Notice("Kopieren fehlgeschlagen."));
				}),
		);
		menu.showAtMouseEvent(e);
	}

	private pdfZuordnen(): void {
		const name = this.aktiveName;
		if (name === null) return;
		const modal = new PdfAuswahlModal(
			this.app,
			this.app.vault.getFiles().filter((f) => f.extension === "pdf"),
		);
		modal.setPlaceholder("Original-PDF suchen…");
		modal.onAuswahl = (datei) => {
			void this.bestand.eintragAendern(name, { "quelle-pdf-manuell": datei.path });
			void this.oeffnen(name);
		};
		modal.open();
	}

	private notizDialog(bestand: Bestandseintrag): void {
		const modal = new NotizModal(this.app, bestand.eintrag.notiz ?? "");
		modal.onGespeichert = (text) => {
			const bereinigt = text.trim();
			void this.bestand.eintragAendern(bestand.name, {
				notiz: bereinigt.length > 0 ? bereinigt : null,
			});
		};
		modal.open();
	}

	// ── Tastatur ──────────────────────────────────────────────────────────────

	private tastenRegistrieren(): void {
		const scope = this.scope;
		if (scope === null) return;
		// Obsidians Scope nimmt KEINE Ruecksicht auf fokussierte Eingabefelder.
		// Ohne diese Pruefung tippt „Fall 8" in das Filterfeld der Seitenleiste
		// nicht den Text, sondern loest „annehmen" aus und verschiebt eine Datei.
		// `true` zurueckgeben heisst: nicht behandelt, das Feld bekommt die Taste.
		const taste = (key: string, funktion: () => void) =>
			scope.register([], key, (ereignis) => {
				if (istEingabeziel(ereignis.target)) return true;
				funktion();
				return false;
			});

		taste("j", () => {
			const name = this.seitenleiste.verschiebeAuswahl(1);
			if (name !== null) void this.oeffnen(name);
		});
		taste("k", () => {
			const name = this.seitenleiste.verschiebeAuswahl(-1);
			if (name !== null) void this.oeffnen(name);
		});
		taste("a", () => void this.entscheiden("akzeptiert"));
		taste("x", () => void this.entscheiden("abgelehnt"));
		taste("t", () =>
			this.darstellungSetzen(
				this.mdSpalte.aktuelleDarstellung() === "gerendert" ? "quelltext" : "gerendert",
			),
		);
		taste("e", () => this.editierenUmschalten());
		taste(" ", () => this.seiteWeiter());
		taste("g", () => this.geheZuSeite());
		taste("s", () => this.syncUmschalten());
		taste("Escape", () => this.keineAuswahl());
	}

	private seiteWeiter(): void {
		const el = this.pdfSpalte.scrollEl;
		el.scrollBy({ top: Math.max(el.clientHeight - 60, 1) });
	}

	private syncUmschalten(): void {
		this.kopplung.aktiv = !this.kopplung.aktiv;
		this.plugin.einstellungen.syncAktiv = this.kopplung.aktiv;
		void this.plugin.einstellungenSpeichern();
		new Notice(`Scroll-Synchronisation ${this.kopplung.aktiv ? "an" : "aus"}.`);
	}

	private geheZuSeite(): void {
		const modal = new Modal(this.app);
		modal.titleEl.setText("Gehe zu Seite");
		// addText ruft den Callback synchron — das Feld ist nach dem Aufruf da.
		const eingabe: HTMLInputElement | null = ((): HTMLInputElement | null => {
			let feld: HTMLInputElement | null = null;
			new Setting(modal.contentEl).setName("Seitennummer").addText((t) => {
				t.inputEl.type = "number";
				t.inputEl.min = "1";
				t.inputEl.placeholder = this.pdfSeiten > 0 ? `1–${this.pdfSeiten}` : "1…";
				feld = t.inputEl;
				window.setTimeout(() => feld?.focus(), 0);
			});
			return feld;
		})();
		if (eingabe === null) {
			modal.close();
			return;
		}
		const zeile = modal.contentEl.createDiv({ cls: "modal-button-container" });
		const springen = zeile.createEl("button", { cls: "mod-cta", text: "Springen" });
		const ausfuehren = () => {
			const nr = Number.parseInt(eingabe.value, 10);
			if (Number.isFinite(nr) && nr > 0) this.kopplung.zuSeite(nr);
			modal.close();
		};
		springen.addEventListener("click", () => ausfuehren());
		eingabe.addEventListener("keydown", (e) => {
			if (e.key === "Enter") ausfuehren();
		});
		modal.open();
	}

	// ── Spaltenbreiten ────────────────────────────────────────────────────────

	/** Einstellungen uebernehmen, die eine OFFENE Ansicht anfassen — gerufen
	 *  beim Aufbau und vom Einstellungs-Tab, damit Aenderungen sofort gelten. */
	einstellungenAnwenden(): void {
		this.kopplung.aktiv = this.plugin.einstellungen.syncAktiv;
		this.spaltenbreitenAnwenden(this.plugin.einstellungen.spaltenbreiten);
	}

	private kleinknopf(
		eltern: HTMLElement,
		text: string,
		ariaLabel: string,
		aktion: () => void,
	): HTMLButtonElement {
		const knopf = eltern.createEl("button", {
			cls: "ocr-knopf ocr-knopf-klein",
			text,
			...(ariaLabel.length > 0
				? { attr: { "aria-label": ariaLabel, title: ariaLabel } }
				: {}),
		});
		knopf.addEventListener("click", () => aktion());
		return knopf;
	}

	private spaltenbreitenAnwenden(breiten: readonly [number, number, number]): void {
		const spalten = this.rahmen.querySelectorAll<HTMLElement>(".ocr-spalte");
		for (let i = 0; i < spalten.length; i++) {
			const spalte = spalten[i];
			const breite = breiten[i];
			if (spalte === undefined || breite === undefined) continue;
			spalte.style.setProperty("--ocr-spalte-basis", `${breite}%`);
		}
	}

	private griffVerdrahten(griff: HTMLElement, links: 0 | 1, rechts: 1 | 2): void {
		griff.addEventListener("mousedown", (start) => {
			start.preventDefault();
			const rahmenBreite = Math.max(this.rahmen.getBoundingClientRect().width, 1);
			const startX = start.clientX;
			const startBreiten = [...this.plugin.einstellungen.spaltenbreiten] as [
				number,
				number,
				number,
			];
			let letzte: [number, number, number] = startBreiten;
			// Die dritte Spalte ist der Rest — sie saugt auf, was Klemmen
			// uebriglaesst, damit die Summe immer 100 bleibt.
			const dritte: 0 | 2 = links === 0 ? 2 : 0;
			const breiten = (delta: number): [number, number, number] => {
				const b = [...startBreiten] as [number, number, number];
				const linksWert = b[links];
				const rechtsWert = b[rechts];
				const linksNeu = Math.min(
					Math.max(linksWert + delta, SPALTE_MIN),
					100 - 2 * SPALTE_MIN,
				);
				const rechtsNeu = Math.min(
					Math.max(rechtsWert - (linksNeu - linksWert), SPALTE_MIN),
					100 - linksNeu - SPALTE_MIN,
				);
				b[links] = linksNeu;
				b[rechts] = rechtsNeu;
				b[dritte] = 100 - linksNeu - rechtsNeu;
				return b;
			};
			const bewegen = (e: MouseEvent) => {
				const delta = ((e.clientX - startX) / rahmenBreite) * 100;
				letzte = breiten(delta);
				this.spaltenbreitenAnwenden(letzte);
				this.kopplung.neuVermessen();
			};
			const loslassen = () => {
				window.removeEventListener("mousemove", bewegen);
				window.removeEventListener("mouseup", loslassen);
				// breiten(0) normalisiert Ausgangswerte unter dem Minimum;
				// letzte traegt die zuletzt gezogene Position.
				this.plugin.einstellungen.spaltenbreiten = letzte;
				void this.plugin.einstellungenSpeichern();
			};
			window.addEventListener("mousemove", bewegen);
			window.addEventListener("mouseup", loslassen);
		});
	}
}

/** Steht der Fokus in einem Feld, in das man schreibt? */
function istEingabeziel(ziel: EventTarget | null): boolean {
	if (ziel instanceof HTMLInputElement) return true;
	if (ziel instanceof HTMLTextAreaElement) return true;
	if (ziel instanceof HTMLSelectElement) return true;
	return ziel instanceof HTMLElement && ziel.isContentEditable;
}

// ── Dialoge ──────────────────────────────────────────────────────────────────

/**
 * PDF aus dem Vault waehlen — fuer die manuelle Zuordnung hier und fuer den
 * Konvertierungs-Befehl in main.ts.
 */
export class PdfAuswahlModal extends SuggestModal<TFile> {
	onAuswahl: ((datei: TFile) => void) | null = null;

	constructor(
		app: App,
		private pdfs: TFile[],
	) {
		super(app);
		this.emptyStateText = "Keine PDFs im Vault.";
	}

	getSuggestions(query: string): TFile[] {
		const q = query.trim().toLowerCase();
		if (q.length === 0) return this.pdfs.slice(0, 50);
		return this.pdfs.filter((f) => f.path.toLowerCase().includes(q)).slice(0, 50);
	}

	renderSuggestion(datei: TFile, el: HTMLElement): void {
		el.createDiv({ text: datei.basename });
		el.createDiv({ cls: "suggestion-note", text: datei.path });
	}

	onChooseSuggestion(datei: TFile): void {
		this.onAuswahl?.(datei);
	}
}

/** Eine Notiz zum Eintrag — bleibt beim Abgleich erhalten. */
class NotizModal extends Modal {
	onGespeichert: ((text: string) => void) | null = null;

	constructor(
		app: App,
		private anfang: string,
	) {
		super(app);
	}

	onOpen(): void {
		this.titleEl.setText("Notiz zur Vorschau");
		const feld = this.contentEl.createEl("textarea", {
			cls: "ocr-notiz-feld",
			text: this.anfang,
		});
		const zeile = this.contentEl.createDiv({ cls: "modal-button-container" });
		const abbrechen = zeile.createEl("button", { text: "Abbrechen" });
		abbrechen.addEventListener("click", () => this.close());
		const speichern = zeile.createEl("button", { cls: "mod-cta", text: "Speichern" });
		speichern.addEventListener("click", () => {
			this.onGespeichert?.(feld.value);
			this.close();
		});
	}

	onClose(): void {
		this.contentEl.empty();
	}
}

/**
 * Seiten vor der Konvertierung auswaehlen. Zeigt die Gesamtseitenzahl des
 * PDFs und ein Texteingabefeld fuer die Seiten (z.B. "1,3-5").
 * Leer = alle Seiten.
 */
export class SeitenAuswahlModal extends Modal {
	onAuswahl: ((seiten: string) => void) | null = null;
	private eingabe: HTMLInputElement | null = null;

	constructor(
		app: App,
		private datei: TFile,
	) {
		super(app);
	}

	async onOpen(): Promise<void> {
		this.titleEl.setText("Seiten auswaehlen");
		this.contentEl.createDiv({
			cls: "setting-item-description",
			text: `PDF: ${this.datei.basename}`,
		});
		// Gesamtseitenzahl per pdf.js laden
		try {
			if (typeof window.pdfjsLib === "undefined") await loadPdfJs();
			const pdfjs = window.pdfjsLib as { getDocument: (opts: object) => { promise: Promise<{ numPages: number }> } };
			const doc = await pdfjs.getDocument({
				url: this.app.vault.getResourcePath(this.datei),
			}).promise;
			this.contentEl.createDiv({
				cls: "setting-item-description",
				text: `Insgesamt ${doc.numPages} Seiten`,
			});
		} catch {
			this.contentEl.createDiv({
				cls: "setting-item-description",
				text: "(Seitenzahl konnte nicht ermittelt werden)",
			});
		}
		new Setting(this.contentEl)
			.setName("Seiten")
			.setDesc("z.B. 1,3-5,8 — leer lassen = alle Seiten")
			.addText((t) => {
				t.inputEl.placeholder = "alle Seiten";
				t.inputEl.addClass("ocr-seiten-eingabe");
				this.eingabe = t.inputEl;
				window.setTimeout(() => t.inputEl.focus(), 0);
			});
		const zeile = this.contentEl.createDiv({ cls: "modal-button-container" });
		const abbrechen = zeile.createEl("button", { text: "Abbrechen" });
		abbrechen.addEventListener("click", () => this.close());
		const konvertieren = zeile.createEl("button", {
			cls: "mod-cta",
			text: "Konvertieren",
		});
		const ausfuehren = () => {
			const wert = this.eingabe?.value.trim() ?? "";
			this.onAuswahl?.(wert);
			this.close();
		};
		konvertieren.addEventListener("click", () => ausfuehren());
		this.eingabe?.addEventListener("keydown", (e) => {
			if (e.key === "Enter") ausfuehren();
		});
	}

	onClose(): void {
		this.contentEl.empty();
	}
}

/** Oeffnet die Plugin-Einstellungen. `app.setting` ist intern, aber seit
 *  Jahren stabil; der Fallback oeffnet den Einstellungsdialog ohne Tab. */
function einstellungenOeffnen(plugin: OcrVorschauPlugin): void {
	const setting = (
		plugin.app as unknown as {
			setting?: { openTabById?: (id: string) => void; open?: () => void };
		}
	).setting;
	if (setting?.openTabById !== undefined) setting.openTabById(plugin.manifest.id);
	else if (setting?.open !== undefined) setting.open();
}
