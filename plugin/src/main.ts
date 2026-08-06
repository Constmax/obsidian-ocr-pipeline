// Plugin-Einstieg: Registrierung der Ansicht, Befehle, Ribbon, Datei-Menue
// und die Vault-Listener, die den Abgleich anstossen.

import { homedir } from "os";
import { join } from "path";
import { existsSync } from "fs";
import {
	FileSystemAdapter,
	Menu,
	Notice,
	Plugin,
	TAbstractFile,
	TFile,
	normalizePath,
} from "obsidian";

import { ANSICHT_TYP, OcrAbgleichAnsicht, PdfAuswahlModal } from "./ansicht.ts";
import { Bestand } from "./dateiaktionen.ts";
import { Einstellungen, EinstellungenTab, STANDARD } from "./einstellungen.ts";
import { pdfKonvertieren } from "./konvertierung.ts";

const ABGLEICH_ENTPRELLT_MS = 500;

/** Lokale pdf2md-Installation — install.sh legt den Symlink nach ~/bin an. */
const PDF2MD_PFAD = join(homedir(), "bin", "pdf2md");

export default class OcrVorschauPlugin extends Plugin {
	einstellungen: Einstellungen = { ...STANDARD };
	bestand!: Bestand;

	private abgleichTimer: number | null = null;
	private konvertiertGerade = false;

	async onload(): Promise<void> {
		await this.einstellungenLaden();
		this.bestand = new Bestand(this.app, () => this.einstellungen);
		await this.bestand.laden();

		this.registerView(ANSICHT_TYP, (leaf) => new OcrAbgleichAnsicht(leaf, this));

		// Datei verschoben/neu/geloescht → Abgleich. Entprellt: ein Schwung an
		// Aenderungen kostet einen Durchlauf, nicht einen pro Ereignis.
		const anstossen = () => this.abgleichAnstossen();
		this.registerEvent(this.app.vault.on("create", anstossen));
		this.registerEvent(this.app.vault.on("delete", anstossen));
		this.registerEvent(
			this.app.vault.on("rename", (datei, alterPfad) => {
				// Vor dem Abgleich: eine Datei, die die drei Ordner verlaesst, nimmt
				// ihren Eintrag mit. Sonst kann Regel 4 „ins Wiki uebernommen" nicht
				// von „geloescht" unterscheiden.
				this.bestand.pfadNachziehen(datei.path, alterPfad);
				anstossen();
			}),
		);
		// `modify` ist nicht verzichtbar: ein erneuter pdf2md-Lauf schreibt nach
		// <out>/<stem>.md und ueberschreibt eine dort liegende Datei IN PLACE.
		// Das ist weder create noch rename — ohne diesen Listener bliebe das neue
		// `ocr-datum` ungesehen und Regel 6 erkennt die Neukonvertierung nicht.
		//
		// Gefiltert auf .md in den drei Ordnern. Das haelt den Abgleich von jedem
		// beliebigen Notiz-Tastendruck im Vault fern und schliesst zugleich den
		// Schreib-Kreisel strukturell aus: review-status.json ist .json, unser
		// eigener Schreibvorgang kann diesen Listener also nie ausloesen.
		this.registerEvent(
			this.app.vault.on("modify", (datei) => {
				if (!(datei instanceof TFile)) return;
				if (datei.extension !== "md") return;
				if (!this.istVorschauDatei(datei)) return;
				anstossen();
			}),
		);

		this.addRibbonIcon("columns-3", "OCR-Abgleich öffnen", () => {
			void this.ansichtOeffnen();
		});

		this.addCommand({
			id: "abgleich-oeffnen",
			name: "Abgleich-Ansicht öffnen",
			callback: () => void this.ansichtOeffnen(),
		});
		this.addCommand({
			id: "abgleich-weiter",
			name: "Zum nächsten Vorschau-Eintrag springen",
			callback: () => this.offeneAnsicht()?.weiter(),
		});
		this.addCommand({
			id: "pdf-konvertieren-und-abgleich",
			name: "PDF konvertieren und im OCR-Abgleich öffnen",
			callback: () => void this.pdfAuswaehlenUndKonvertieren(),
		});

		this.registerEvent(
			this.app.vault.on("modify", (datei) => {
				if (!(datei instanceof TFile)) return;
				if (datei.extension !== "md") return;
				if (!this.istVorschauDatei(datei)) return;
				anstossen();
			}),
		);
		// `modify` meldet den Schreibvorgang, aber das neue `ocr-datum` steht
		// erst nach Obsidians Nachparsen im metadataCache — und genau DAS liest
		// `dateienSammeln`. Fuer Regel 6 dem Cache-Meldepunkt nachhoeren, mit
		// demselben Filter: ein verzoegertes Parsen darf die Neukonvertierung
		// nicht verpassen. Der eigene Manifest-Schreibvorgang ist .json und
		// kommt hier nie an.
		this.registerEvent(
			this.app.metadataCache.on("changed", (datei) => {
				if (!(datei instanceof TFile)) return;
				if (datei.extension !== "md") return;
				if (!this.istVorschauDatei(datei)) return;
				anstossen();
			}),
		);

		this.addSettingTab(new EinstellungenTab(this.app, this));
	}

	onunload(): void {
		if (this.abgleichTimer !== null) window.clearTimeout(this.abgleichTimer);
		// Der Manifest-Schreibvorgang ist um 500 ms entprellt. Wird Obsidian
		// innerhalb dieser Spanne nach einer Entscheidung geschlossen, stirbt der
		// Timer mit dem Plugin und Notiz, `geprueft-bis` und der Zeitpunkt der
		// Entscheidung waeren weg. `onunload` ist synchron, also nur anstossen —
		// das genuegt, weil der Schreibvorgang damit sofort statt spaeter laeuft.
		void this.bestand?.sofortSpeichern();
	}

	async einstellungenLaden(): Promise<void> {
		const gespeichert = (await this.loadData()) as Partial<Einstellungen> | null;
		this.einstellungen = { ...STANDARD, ...(gespeichert ?? {}) };
	}

	async einstellungenSpeichern(): Promise<void> {
		await this.saveData(this.einstellungen);
	}

	/** Abgleich nach Fremdaenderung oder Einstellungsaenderung. */
	abgleichAnstossen(): void {
		if (this.abgleichTimer !== null) window.clearTimeout(this.abgleichTimer);
		this.abgleichTimer = window.setTimeout(() => {
			this.abgleichTimer = null;
			void this.bestand.abgleichen();
			this.offeneAnsicht()?.aktualisieren();
		}, ABGLEICH_ENTPRELLT_MS);
	}

	offeneAnsicht(): OcrAbgleichAnsicht | null {
		const leaf = this.app.workspace.getLeavesOfType(ANSICHT_TYP)[0];
		if (leaf === undefined) return null;
		return leaf.view instanceof OcrAbgleichAnsicht ? leaf.view : null;
	}

	/** Oeffnet die Ansicht, optional mit einem Eintrag. */
	async ansichtOeffnen(name?: string): Promise<void> {
		const { workspace } = this.app;
		let leaf = workspace.getLeavesOfType(ANSICHT_TYP)[0];
		if (leaf === undefined) {
			leaf = workspace.getLeaf("tab");
			await leaf.setViewState({ type: ANSICHT_TYP, active: true });
		}
		await workspace.revealLeaf(leaf);
		const ansicht =
			leaf.view instanceof OcrAbgleichAnsicht ? leaf.view : null;
		if (ansicht === null) return;
		if (name !== undefined) await ansicht.oeffnen(name);
		else if (ansicht.aktiveName === null) {
			const erster = ansicht.ersterSichtbar();
			if (erster !== null) await ansicht.oeffnen(erster);
		}
	}

	/**
	 * Befehl „PDF konvertieren und im OCR-Abgleich öffnen": eine PDF aus dem
	 * Vault waehlen, per pdf2md konvertieren, dann den Abgleich mit dem
	 * Ergebnis oeffnen. Rückmeldung bewusst per Notice — Fortschrittsmodal
	 * und Abbruch sind Roadmap-Themen.
	 */
	async pdfAuswaehlenUndKonvertieren(): Promise<void> {
		if (this.konvertiertGerade) {
			new Notice("OCR-Vorschau: Es läuft bereits eine Konvertierung.");
			return;
		}
		const modal = new PdfAuswahlModal(
			this.app,
			this.app.vault.getFiles().filter((f) => f.extension === "pdf"),
		);
		modal.setPlaceholder("PDF für die Konvertierung suchen…");
		modal.onAuswahl = (datei) => void this.konvertieren(datei);
		modal.open();
	}

	private async konvertieren(datei: TFile): Promise<void> {
		// Erneute Pruefung hier (nicht nur in pdfAuswaehlenUndKonvertieren):
		// zwei Befehlsaufrufe koennen je ein Modal oeffnen, bevor der erste
		// eine Auswahl trifft. Check und Setzen der Flagge muessen darum an
		// derselben Stelle direkt vor dem eigentlichen Start liegen.
		if (this.konvertiertGerade) {
			new Notice("OCR-Vorschau: Es läuft bereits eine Konvertierung.");
			return;
		}
		this.konvertiertGerade = true;
		const name = datei.basename;
		let laufendeNotice: Notice | null = null;
		try {
			const adapter = this.app.vault.adapter;
			if (!(adapter instanceof FileSystemAdapter)) {
				new Notice("OCR-Vorschau: Konvertierung braucht Dateisystemzugriff (Desktop).");
				return;
			}
			const basis = adapter.getBasePath();
			if (!existsSync(PDF2MD_PFAD)) {
				new Notice(
					`OCR-Vorschau: pdf2md nicht gefunden unter ${PDF2MD_PFAD}. Bitte install.sh ausführen.`,
				);
				return;
			}
			// Vault-relative Pfade, Kindprozess mit cwd=Vault-Wurzel: pdf2md.py
			// schreibt den PDF-Pfad unveraendert in `quelle-pdf` und den
			// `Quelle: [[…]]`-Link der erzeugten Notiz. Ein absoluter,
			// maschinenspezifischer Pfad waere dort ein toter Link.
			const pdfRel = datei.path;
			const outRel = normalizePath(this.einstellungen.vorschauOrdner);
			laufendeNotice = new Notice(`OCR-Vorschau: Konvertiere „${name}“ …`, 0);
			const ergebnis = await pdfKonvertieren(pdfRel, outRel, PDF2MD_PFAD, basis);
			laufendeNotice.hide();
			laufendeNotice = null;
			if (ergebnis.code !== 0) {
				const stderrLetzte = ergebnis.stderrLetzte;
				const stdoutLetzte = ergebnis.stdoutLetzte;
				const detail =
					(stderrLetzte.length > 0 ? stderrLetzte[stderrLetzte.length - 1] : undefined) ??
					(stdoutLetzte.length > 0 ? stdoutLetzte[stdoutLetzte.length - 1] : undefined) ??
					"";
				const codeText = ergebnis.code === null ? "Startfehler" : `Code ${ergebnis.code}`;
				new Notice(
					`OCR-Vorschau: Konvertierung fehlgeschlagen (${codeText})` +
						(detail.length > 0 ? ` — ${detail}` : "") +
						".",
				);
				return;
			}
			// Der Vault-Listener wuerde den neuen Eintrag frueher oder spaeter
			// sehen; fuer den direkten Uebergang in die Ansicht ist der
			// Bestand jetzt besser frisch.
			await this.bestand.abgleichen();
			const eintrag = `${name}.md`;
			if (this.bestand.eintraege.some((b) => b.name === eintrag)) {
				await this.ansichtOeffnen(eintrag);
				new Notice(`OCR-Vorschau: „${name}“ fertig — Abgleich geöffnet.`);
			} else {
				new Notice(
					`OCR-Vorschau: „${name}“ fertig, aber nicht im Vorschau-Ordner gelandet (Ziel: ${this.einstellungen.vorschauOrdner}).`,
				);
			}
		} finally {
			laufendeNotice?.hide();
			this.konvertiertGerade = false;
		}
	}

	/** Datei-Menue: auf Vorschau-`.md` und auf PDFs mit passendem Stem. */
	private dateiMenuBefuellen(menu: Menu, datei: TAbstractFile): void {
		if (!(datei instanceof TFile)) return;
		if (datei.extension === "md" && this.istVorschauDatei(datei)) {
			menu.addItem((i) =>
				i
					.setTitle("Im OCR-Abgleich öffnen")
					.setIcon("columns-3")
					.onClick(() => void this.ansichtOeffnen(datei.name)),
			);
		} else if (datei.extension === "pdf") {
			const stem = `${datei.basename}.md`;
			if (this.bestand.eintraege.some((b) => b.name === stem)) {
				menu.addItem((i) =>
					i
						.setTitle("Im OCR-Abgleich öffnen")
						.setIcon("columns-3")
						.onClick(() => void this.ansichtOeffnen(stem)),
				);
			}
		}
	}

	private istVorschauDatei(datei: TFile): boolean {
		const e = this.einstellungen;
		const eltern = datei.parent?.path ?? "";
		return (
			eltern === e.vorschauOrdner ||
			eltern === e.akzeptiertOrdner ||
			eltern === e.abgelehntOrdner
		);
	}
}
