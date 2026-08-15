// Plugin-Einstieg: Registrierung der Ansicht, Befehle, Ribbon, Datei-Menue
// und die Vault-Listener, die den Abgleich anstossen.

import { homedir } from "os";
import { join } from "path";
import { existsSync } from "fs";
import type { ChildProcess } from "child_process";
import {
	FileSystemAdapter,
	Menu,
	Notice,
	Plugin,
	TAbstractFile,
	TFile,
	normalizePath,
} from "obsidian";

import { ANSICHT_TYP, OcrAbgleichAnsicht, PdfAuswahlModal, SeitenAuswahlModal } from "./ansicht.ts";
import { Bestand } from "./dateiaktionen.ts";
import { Einstellungen, EinstellungenTab, STANDARD } from "./einstellungen.ts";
import { kindAbbrechen, pdfKonvertieren } from "./konvertierung.ts";

const ABGLEICH_ENTPRELLT_MS = 500;

/** Nach so langer Laufzeit ohne Prozessende wird die Konvertierung gekillt.
 *  Grosszuegig bemessen: der ERSTE Lauf laedt das MLX-Modell (~2 GB) und
 *  schreibt erst danach Fortschritt. */
const KONVERTIERUNG_TIMEOUT_MS = 30 * 60 * 1000;

/** Wartezeit auf Obsidians Datei-Index nach dem Prozessende: der Watcher
 *  laeuft dem Dateisystem um Bruchteile einer Sekunde hinterher. */
const INDEX_WARTE_SCHRITTE = 10;
const INDEX_WARTE_MS = 200;

/** Lokale pdf2md-Installation. setup.sh (Repo-Wurzel) und install.sh legen
 *  den Symlink nach ~/bin an; install.sh respektiert dabei BIN_DIR. Gesucht
 *  wird deshalb in der Reihenfolge ~/bin, /usr/local/bin, dann PATH. */
function pdf2mdAufloesen(): string {
	const kandidaten = [join(homedir(), "bin", "pdf2md"), "/usr/local/bin/pdf2md"];
	for (const teil of (process.env.PATH ?? "").split(":")) {
		if (teil.length > 0) kandidaten.push(join(teil, "pdf2md"));
	}
	for (const kandidat of kandidaten) {
		if (existsSync(kandidat)) return kandidat;
	}
	// Nichts gefunden: der Fallback laeuft in den Spawn-ENOENT-Pfad, der die
	// freundliche Meldung mit setup.sh-Hinweis zeigt.
	return kandidaten[0]!;
}

export default class OcrVorschauPlugin extends Plugin {
	einstellungen: Einstellungen = { ...STANDARD };
	bestand!: Bestand;

	private abgleichTimer: number | null = null;
	private konvertiertGerade = false;
	/** Laufender pdf2md-Kindprozess — wird beim Plugin-Unload gekillt, damit
	 *  ein haengender Lauf Obsidian nicht auf ewig blockiert. */
	private laufendesKind: ChildProcess | null = null;

	async onload(): Promise<void> {
		await this.einstellungenLaden();
		this.bestand = new Bestand(this.app, () => this.einstellungen);
		await this.bestand.laden();

		this.registerView(ANSICHT_TYP, (leaf) => new OcrAbgleichAnsicht(leaf, this));

		// Datei verschoben/neu/geloescht → Abgleich. Entprellt: ein Schwung an
		// Aenderungen kostet einen Durchlauf, nicht einen pro Ereignis.
		// Gefiltert auf .md in den drei Ordnern: pdf2md legt waehrend einer
		// Konvertierung Kachelbilder (_seiteNNN.png) im Repo-Ordner ab — ohne
		// Filter stuesse jedes PNG den Abgleich an.
		const relevant = (datei: TAbstractFile): boolean =>
			datei instanceof TFile && datei.extension === "md" && this.istVorschauDatei(datei);
		const anstossen = () => this.abgleichAnstossen();
		this.registerEvent(
			this.app.vault.on("create", (datei) => {
				if (relevant(datei)) anstossen();
			}),
		);
		this.registerEvent(
			this.app.vault.on("delete", (datei) => {
				if (relevant(datei)) anstossen();
			}),
		);
		this.registerEvent(
			this.app.vault.on("rename", (datei, alterPfad) => {
				if (!(datei instanceof TFile) || datei.extension !== "md") return;
				// Vor dem Abgleich: eine Datei, die die drei Ordner verlaesst,
				// nimmt ihren Eintrag mit. Sonst kann Regel 4 „ins Wiki
				// uebernommen" nicht von „geloescht" unterscheiden. Deshalb muss
				// auch das VERLASSEN der drei Ordner den Abgleich anstossen —
				// der Filter prueft beide Pfade.
				if (!this.istVorschauPfad(alterPfad) && !this.istVorschauDatei(datei)) return;
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

		this.addSettingTab(new EinstellungenTab(this.app, this));
	}

	onunload(): void {
		if (this.abgleichTimer !== null) window.clearTimeout(this.abgleichTimer);
		// Geordneter Abbruch (SIGTERM, nach Frist SIGKILL): ein haengender
		// Lauf darf Obsidian nicht auf ewig blockieren, und pdf2md bekommt die
		// Chance, seine Teildatei zu schreiben.
		if (this.laufendesKind !== null) kindAbbrechen(this.laufendesKind);
		this.laufendesKind = null;
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
	 * Ergebnis oeffnen. Rückmeldung per Notice mit Abbruch-Button — der
	 * Fortschrittsmodal mit Fortschrittsbalken bleibt Roadmap.
	 */
	async pdfAuswaehlenUndKonvertieren(): Promise<void> {
		if (!this.konvertierungFrei()) return;
		const modal = new PdfAuswahlModal(
			this.app,
			this.app.vault.getFiles().filter((f) => f.extension === "pdf"),
		);
		modal.setPlaceholder("PDF für die Konvertierung suchen…");
		modal.onAuswahl = (datei) => {
			const seitenModal = new SeitenAuswahlModal(this.app, datei);
			seitenModal.onAuswahl = (seiten) =>
				void this.konvertieren(datei, seiten);
			seitenModal.open();
		};
		modal.open();
	}

	/** Gemeinsame Sperre beider Konvertierungs-Einstiege (Modal-Auswahl und
	 *  Direktaufruf): zwei Befehlsaufrufe koennen je ein Modal oeffnen, bevor
	 *  der erste eine Auswahl trifft. */
	private konvertierungFrei(): boolean {
		if (this.konvertiertGerade) {
			new Notice("OCR-Vorschau: Es läuft bereits eine Konvertierung.");
			return false;
		}
		return true;
	}

	private async konvertieren(datei: TFile, seiten?: string): Promise<void> {
		if (!this.konvertierungFrei()) return;
		this.konvertiertGerade = true;
		const name = datei.basename;
		let laufendeNotice: Notice | null = null;
		try {
			// Gleiche Stems in verschiedenen Ordnern wuerden dieselbe
			// <out>/<stem>.md ueberschreiben — lieber mit klarer Meldung
			// abbrechen als still die Vorschau der anderen Datei zerstoeren.
			const gleichnamige = this.app.vault
				.getFiles()
				.filter(
					(f) => f.extension === "pdf" && f.basename === name && f.path !== datei.path,
				);
			if (gleichnamige.length > 0) {
				new Notice(
					`OCR-Vorschau: „${name}“ existiert auch als ${gleichnamige
						.map((f) => f.path)
						.join(", ")} — die Ausgabe würde sich überschreiben. Bitte eine der Dateien umbenennen.`,
				);
				return;
			}
			const adapter = this.app.vault.adapter;
			if (!(adapter instanceof FileSystemAdapter)) {
				new Notice("OCR-Vorschau: Konvertierung braucht Dateisystemzugriff (Desktop).");
				return;
			}
			const basis = adapter.getBasePath();
			// Vault-relative Pfade, Kindprozess mit cwd=Vault-Wurzel: pdf2md.py
			// schreibt den PDF-Pfad unveraendert in `quelle-pdf` und den
			// `Quelle: [[…]]`-Link der erzeugten Notiz. Ein absoluter,
			// maschinenspezifischer Pfad waere dort ein toter Link.
			const pdfRel = datei.path;
			const outRel = normalizePath(this.einstellungen.vorschauOrdner);
			laufendeNotice = new Notice(`OCR-Vorschau: Konvertiere „${name}“ …`, 0);
			// Abbruch-Button in der Notice: geordneter Abbruch (SIGTERM, nach
			// Frist SIGKILL). Die Stufe, die gegriffen hat, steht im
			// Ergebnis — Code 6 (pdf2md hat die Teildatei geschrieben),
			// Code 7 (Abbruch vor der ersten Seite, keine Datei) oder
			// Signal SIGTERM/SIGKILL.
			let abgebrochen = false;
			const abbruchKnopf = laufendeNotice.containerEl.createEl("button", {
				text: "Abbrechen",
				cls: "ocr-notice-abbrechen",
			});
			abbruchKnopf.addEventListener("click", () => {
				if (abgebrochen) return;
				abgebrochen = true;
				abbruchKnopf.detach();
				laufendeNotice?.setMessage(`OCR-Vorschau: „${name}“ wird abgebrochen …`);
				if (this.laufendesKind !== null) kindAbbrechen(this.laufendesKind);
			});
			const ergebnis = await pdfKonvertieren(
				pdfRel,
				outRel,
				pdf2mdAufloesen(),
				basis,
				undefined,
				{
					timeoutMs: KONVERTIERUNG_TIMEOUT_MS,
					...(seiten && seiten.length > 0 ? { seiten } : {}),
					onKind: (kind) => {
						this.laufendesKind = kind;
					},
					onFortschritt: (e) => {
						if (e.typ !== "seite" || abgebrochen) return;
						if (laufendeNotice) {
							const txt = e.entgleist
								? `— Seite ${e.nr} von ${e.von} (entgleist)`
								: `— Seite ${e.nr} von ${e.von}`;
							laufendeNotice.setMessage(
								`OCR-Vorschau: Konvertiere "${name}"${txt} …`
							);
						}
					},
				},
			);
			this.laufendesKind = null;
			laufendeNotice.hide();
			laufendeNotice = null;
			if (ergebnis.code !== 0) {
				const stderrLetzte = ergebnis.stderrLetzte;
				// Fortschrittszeilen („→ S.3: …") sind keine Fehlerursache — ohne
				// stderr-Rest darf keine davon als Detail durchrutschen.
				const stdoutLetzte = ergebnis.stdoutLetzte.filter((z) => !z.startsWith("→"));
				const detail =
					(stderrLetzte.length > 0
						? stderrLetzte[stderrLetzte.length - 1]
						: undefined) ??
					(stdoutLetzte.length > 0 ? stdoutLetzte[stdoutLetzte.length - 1] : undefined) ??
					"";
			let codeText: string;
			if (ergebnis.code === 6) {
				// pdf2md hat auf SIGTERM geordnet beendet: Teildatei liegt im
				// Vorschau-Ordner, im Frontmatter als unvollstaendig markiert.
				codeText = "abgebrochen — Teildatei erstellt (unvollständig)";
			} else if (ergebnis.code === 7) {
				// Abbruch vor der ersten Seite (z. B. haengender Modell-Download
				// beim Timeout): pdf2md hat nichts geschrieben — keine Teildatei.
				codeText = "abgebrochen — vor der ersten Seite (keine Teildatei)";
			} else if (ergebnis.signal === "SIGKILL") {
				// Stufe 2: der Prozess hat die Frist nach SIGTERM nicht
				// geschafft und wurde hart beendet — keine Teildatei.
				codeText = "abgebrochen — nach Frist hart beendet (SIGKILL)";
			} else if (ergebnis.timeout) {
				codeText = `nach ${KONVERTIERUNG_TIMEOUT_MS / 60000} min abgebrochen`;
			} else if (ergebnis.code === null && ergebnis.signal !== null) {
				codeText = `abgebrochen (Signal ${ergebnis.signal})`;
			} else if (ergebnis.code === null) {
				codeText = "Startfehler";
			} else {
				codeText = `Code ${ergebnis.code}`;
			}
				let zusatz = detail.length > 0 ? ` — ${detail}` : "";
				if (ergebnis.code === null && /ENOENT/.test(detail)) {
					// Der ausfuehrbare Kandidat existiert nicht (oder ist nicht
					// startbar) — der haeufigste Fehler auf frischen Maschinen:
					// gezielter Hinweis statt roher ENOENT-Zeile.
					zusatz = " — pdf2md nicht gefunden. Bitte setup.sh im Repo ausführen.";
				}
				new Notice(`OCR-Vorschau: Konvertierung fehlgeschlagen (${codeText})${zusatz}.`);
				return;
			}
			// Der Vault-Listener wuerde den neuen Eintrag frueher oder spaeter
			// sehen; fuer den direkten Uebergang in die Ansicht ist der Bestand
			// jetzt besser frisch. Obsidians Datei-Index laeuft dem Prozessende
			// aber um Bruchteile hinterher — deshalb nachlegen, bis der Eintrag
			// da ist.
			const eintrag = `${name}.md`;
			const istDa = () => this.bestand.eintraege.some((b) => b.name === eintrag);
			await this.bestand.abgleichen();
			for (let schritt = 0; !istDa() && schritt < INDEX_WARTE_SCHRITTE; schritt++) {
				await new Promise((fertig) => setTimeout(fertig, INDEX_WARTE_MS));
				await this.bestand.abgleichen();
			}
			if (istDa()) {
				await this.ansichtOeffnen(eintrag);
				new Notice(`OCR-Vorschau: „${name}“ fertig — Abgleich geöffnet.`);
			} else {
				new Notice(
					`OCR-Vorschau: „${name}“ fertig, aber nicht im Vorschau-Ordner gelandet (Ziel: ${this.einstellungen.vorschauOrdner}).`,
				);
			}
		} catch (fehler) {
			// Auch nach erfolgreicher Konvertierung kann die Ansicht scheitern
			// (Workspace/Leaf) — ohne catch bliebe das eine unbehandelte
			// Rejection ohne jede Benutzermeldung.
			new Notice(`OCR-Vorschau: Konvertierung fehlgeschlagen — ${String(fehler)}.`);
		} finally {
			laufendeNotice?.hide();
			this.konvertiertGerade = false;
			this.laufendesKind = null;
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

	private istVorschauPfad(pfad: string): boolean {
		const e = this.einstellungen;
		const eltern = pfad.slice(0, pfad.lastIndexOf("/"));
		return (
			eltern === e.vorschauOrdner ||
			eltern === e.akzeptiertOrdner ||
			eltern === e.abgelehntOrdner
		);
	}

	private istVorschauDatei(datei: TFile): boolean {
		return this.istVorschauPfad(datei.path);
	}
}
