import { App, PluginSettingTab, Setting, TextComponent, normalizePath } from "obsidian";
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

		new Setting(containerEl)
			.setName("Scroll-Kopplung")
			.setDesc("PDF- und Markdown-Spalte synchron scrollen. Umschaltbar auch in der Ansicht.")
			.addToggle((t) =>
				t
					.setValue(this.plugin.einstellungen.syncAktiv)
					.onChange(async (wert) => {
						this.plugin.einstellungen.syncAktiv = wert;
						await this.plugin.einstellungenSpeichern();
						this.plugin.offeneAnsicht()?.einstellungenAnwenden();
					}),
			);

		new Setting(containerEl)
			.setName("PDF-Renderfaktor")
			.setDesc(
				"Obergrenze für die Rasterung — Speicherbremse, keine Qualitätswahl: " +
					"ein A4-Canvas bei Faktor 2 ist bereits ~4,5 MB RGBA.",
			)
			.addSlider((s) =>
				s
					.setLimits(1, 4, 0.25)
					.setValue(this.plugin.einstellungen.pdfZoomMax)
					.onChange(async (wert) => {
						this.plugin.einstellungen.pdfZoomMax = wert;
						await this.plugin.einstellungenSpeichern();
					}),
			);

		new Setting(containerEl)
			.setName("Markdown-Eager-Limit")
			.setDesc(
				"Ab so vielen Seiten rendert die Markdown-Spalte nicht mehr vollständig " +
					"im Voraus — Ventil für Ausreißer, im Normalfall nie erreicht.",
			)
			.addSlider((s) =>
				s
					.setLimits(0, 1000, 10)
					.setValue(this.plugin.einstellungen.mdEagerLimit)
					.onChange(async (wert) => {
						this.plugin.einstellungen.mdEagerLimit = wert;
						await this.plugin.einstellungenSpeichern();
					}),
			);

		new Setting(containerEl)
			.setName("Spaltenbreiten")
			.setDesc(
				"Seitenleiste · PDF · Markdown in Prozent. Verstellbar auch per Griff " +
					"an den Spaltenrändern.",
			)
			.addText((t) => this.breitenFeld(t, 0))
			.addText((t) => this.breitenFeld(t, 1))
			.addText((t) => this.breitenFeld(t, 2));
	}

	/** Prozentfeld einer Spaltenbreite: Ungueltiges faellt auf den Standard
	 *  zurueck, geklemmt wird auf 5–95 %. */
	private breitenFeld(t: TextComponent, index: 0 | 1 | 2): void {
		t
			.setPlaceholder(String(STANDARD.spaltenbreiten[index]))
			.setValue(String(this.plugin.einstellungen.spaltenbreiten[index]))
			.onChange((wert) => {
				const n = Number.parseInt(wert.trim(), 10);
				const breiten = [...this.plugin.einstellungen.spaltenbreiten] as [
					number,
					number,
					number,
				];
				breiten[index] = Number.isFinite(n)
					? Math.min(Math.max(n, 5), 95)
					: STANDARD.spaltenbreiten[index];
				this.plugin.einstellungen.spaltenbreiten = breiten;
				void this.plugin.einstellungenSpeichern();
				this.plugin.offeneAnsicht()?.einstellungenAnwenden();
			});
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
