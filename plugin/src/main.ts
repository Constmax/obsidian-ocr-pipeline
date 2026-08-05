// Plugin-Einstieg: Registrierung der Ansicht, Befehle, Ribbon, Datei-Menue
// und die Vault-Listener, die den Abgleich anstossen.

import { Menu, Plugin, TAbstractFile, TFile } from "obsidian";

import { ANSICHT_TYP, OcrAbgleichAnsicht } from "./ansicht.ts";
import { Bestand } from "./dateiaktionen.ts";
import { Einstellungen, EinstellungenTab, STANDARD } from "./einstellungen.ts";

const ABGLEICH_ENTPRELLT_MS = 500;

export default class OcrVorschauPlugin extends Plugin {
	einstellungen: Einstellungen = { ...STANDARD };
	bestand!: Bestand;

	private abgleichTimer: number | null = null;

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
		this.registerEvent(this.app.vault.on("rename", anstossen));

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

		this.registerEvent(
			this.app.workspace.on("file-menu", (menu, datei) => {
				this.dateiMenuBefuellen(menu, datei);
			}),
		);

		this.addSettingTab(new EinstellungenTab(this.app, this));
	}

	onunload(): void {
		if (this.abgleichTimer !== null) window.clearTimeout(this.abgleichTimer);
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
