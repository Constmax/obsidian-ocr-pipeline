import { App, PluginSettingTab, Setting, normalizePath } from "obsidian";
import type OcrVorschauPlugin from "./main.ts";

export interface Einstellungen {
	/** Ordner, in den pdf2md.py schreibt (`--out`). */
	vorschauOrdner: string;
	akzeptiertOrdner: string;
	abgelehntOrdner: string;
	statusDatei: string;
	markdownAnsicht: "gerendert" | "quelltext";
	/** Spaltenbreiten in Prozent: Seitenleiste, PDF, Markdown. */
	spaltenbreiten: [number, number, number];
	/** Obergrenze fuer den Renderfaktor. Speicherbremse, keine Qualitaetswahl:
	 *  ein A4-Canvas bei Faktor 2 ist bereits ~4,5 MB RGBA. */
	pdfZoomMax: number;
	syncAktiv: boolean;
	/** Ab so vielen Seiten rendert die Markdown-Spalte nicht mehr vollstaendig
	 *  im Voraus. Ventil fuer Ausreisser, im Normalfall nie erreicht. */
	mdEagerLimit: number;
}

export const STANDARD: Einstellungen = {
	vorschauOrdner: "_ocr-vorschau",
	akzeptiertOrdner: "_ocr-vorschau/_akzeptiert",
	abgelehntOrdner: "_ocr-vorschau/_abgelehnt",
	statusDatei: "_ocr-vorschau/review-status.json",
	markdownAnsicht: "gerendert",
	spaltenbreiten: [20, 40, 40],
	pdfZoomMax: 2,
	syncAktiv: true,
	mdEagerLimit: 200,
};

export class EinstellungenTab extends PluginSettingTab {
	constructor(
		app: App,
		private plugin: OcrVorschauPlugin,
	) {
		super(app, plugin);
	}

	display(): void {
		const { containerEl } = this;
		containerEl.empty();

		containerEl.createEl("p", {
			cls: "ocr-einstellungen-hinweis",
			text:
				"Die drei Ordner sind der Zustand: wo eine Datei liegt, bestimmt " +
				"ihren Status. review-status.json ist nur ein Cache mit Anmerkungen " +
				"und darf jederzeit gelöscht werden.",
		});

		this.ordnerFeld(
			"Vorschau-Ordner",
			"Der Ordner, in den pdf2md.py schreibt (--out). Hier liegen die noch " +
				"offenen Vorschauen.",
			"vorschauOrdner",
		);
		this.ordnerFeld(
			"Ordner für Angenommenes",
			"Ziel von „Annehmen“.",
			"akzeptiertOrdner",
		);
		this.ordnerFeld(
			"Ordner für Abgelehntes",
			"Ziel von „Ablehnen“. Es wird nichts gelöscht.",
			"abgelehntOrdner",
		);
		this.ordnerFeld(
			"Status-Datei",
			"Pfad der review-status.json.",
			"statusDatei",
			true,
		);

		new Setting(containerEl)
			.setName("Markdown-Spalte")
			.setDesc("Wie die rechte Spalte beim Öffnen dargestellt wird.")
			.addDropdown((d) =>
				d
					.addOption("gerendert", "Gerendert")
					.addOption("quelltext", "Quelltext")
					.setValue(this.plugin.einstellungen.markdownAnsicht)
					.onChange(async (wert) => {
						this.plugin.einstellungen.markdownAnsicht =
							wert === "quelltext" ? "quelltext" : "gerendert";
						await this.plugin.einstellungenSpeichern();
					}),
			);
	}

	private ordnerFeld(
		name: string,
		beschreibung: string,
		schluessel: "vorschauOrdner" | "akzeptiertOrdner" | "abgelehntOrdner" | "statusDatei",
		istDatei = false,
	): void {
		const setting = new Setting(this.containerEl)
			.setName(name)
			.setDesc(beschreibung)
			.addText((t) =>
				t
					.setPlaceholder(STANDARD[schluessel])
					.setValue(this.plugin.einstellungen[schluessel])
					.onChange(async (wert) => {
						const bereinigt = normalizePath(wert.trim() || STANDARD[schluessel]);
						this.plugin.einstellungen[schluessel] = bereinigt;
						await this.plugin.einstellungenSpeichern();
						hinweisSetzen(bereinigt);
						this.plugin.abgleichAnstossen();
					}),
			);

		const hinweis = setting.descEl.createDiv({ cls: "ocr-pfad-hinweis" });
		const hinweisSetzen = (pfad: string) => {
			if (istDatei) {
				hinweis.setText("");
				return;
			}
			const vorhanden = this.app.vault.getFolderByPath(pfad) !== null;
			hinweis.setText(
				vorhanden
					? ""
					: "Ordner existiert nicht — wird beim ersten Verschieben angelegt.",
			);
		};
		hinweisSetzen(this.plugin.einstellungen[schluessel]);
	}
}
