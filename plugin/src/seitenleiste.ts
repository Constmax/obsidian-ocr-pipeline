// Linke Spalte: die Vorschau-Dateien mit Statusfilter, Textfilter und den
// drei getrennten Leerzustaenden. Reine UI — der Bestand (Manifest + Abgleich)
// lebt in `dateiaktionen.ts`, diese Klasse bekommt fertige Eintraege.

import { Notice, setIcon } from "obsidian";

import type { Bestandseintrag } from "./dateiaktionen.ts";
import type { Ordnerlage, Status } from "./typen.ts";

export type StatusFilter = Ordnerlage | "alle";

/** Kopierbarer Befehl aus docs/ocr-vorschau.md — dort ist er der Vertrag. */
function pdf2mdBefehl(vorschauOrdner: string): string {
	return `python .ocr-bench/pdf2md.py "raw/…/datei.pdf" --out ${vorschauOrdner}`;
}

interface FilterOption {
	wert: StatusFilter;
	label: string;
}

const FILTER: readonly FilterOption[] = [
	{ wert: "offen", label: "Offen" },
	{ wert: "akzeptiert", label: "Akzeptiert" },
	{ wert: "abgelehnt", label: "Abgelehnt" },
	{ wert: "alle", label: "Alle" },
];

/** "Offen" zeigt auch neu-erzeugte Dateien: sie liegen im Vorschau-Ordner und
 *  warten auf eine Entscheidung. */
function passtZumFilter(status: Status, filter: StatusFilter): boolean {
	if (filter === "alle") return true;
	if (filter === "offen") return status === "offen" || status === "neu-erzeugt";
	return status === filter;
}

export class Seitenleiste {
	beiAuswahl: ((name: string) => void) | null = null;
	beiAktualisieren: (() => void) | null = null;
	beiEinstellungen: (() => void) | null = null;

	private filter: StatusFilter = "offen";
	private textFilter = "";
	private bestand: Bestandseintrag[] = [];
	private ordnerFehlt = false;
	private ausgewaehlt: string | null = null;
	private kopf: HTMLElement;
	private filterZeile: HTMLElement;
	private suchfeld: HTMLInputElement;
	private listeEl: HTMLElement;
	private leereEl: HTMLElement;

	constructor(
		wurzel: HTMLElement,
		private vorschauOrdner: () => string,
	) {
		this.kopf = wurzel.createDiv({ cls: "ocr-liste-kopf" });
		this.kopf.createSpan({ cls: "ocr-liste-titel", text: "Vorschauen" });
		const aktualisieren = this.kopf.createEl("button", {
			cls: "ocr-ikonknopf",
			attr: { "aria-label": "Aktualisieren", title: "Aktualisieren" },
		});
		setIcon(aktualisieren.createSpan({ cls: "ocr-ikon" }), "refresh-cw");
		aktualisieren.addEventListener("click", () => this.beiAktualisieren?.());

		this.filterZeile = wurzel.createDiv({ cls: "ocr-filterzeile" });
		for (const option of FILTER) {
			const knopf = this.filterZeile.createEl("button", {
				cls: "ocr-chip",
				text: option.label,
			});
			knopf.dataset["filter"] = option.wert;
			knopf.addEventListener("click", () => {
				this.filter = option.wert;
				this.zeichnen();
			});
		}

		this.suchfeld = wurzel.createEl("input", {
			cls: "ocr-suchfeld",
			type: "search",
			placeholder: "Filtern…",
			attr: { "aria-label": "Nach Dateiname filtern" },
		});
		this.suchfeld.addEventListener("input", () => {
			this.textFilter = this.suchfeld.value.trim().toLowerCase();
			this.zeichnen();
		});

		this.listeEl = wurzel.createDiv({ cls: "ocr-liste" });
		this.leereEl = wurzel.createDiv({ cls: "ocr-leer" });
	}

	aktualisieren(bestand: Bestandseintrag[], ordnerFehlt: boolean): void {
		this.bestand = bestand;
		this.ordnerFehlt = ordnerFehlt;
		if (this.ausgewaehlt !== null && !bestand.some((b) => b.name === this.ausgewaehlt)) {
			this.ausgewaehlt = null;
		}
		this.zeichnen();
	}

	ausgewaehltName(): string | null {
		return this.ausgewaehlt;
	}

	setzeAuswahl(name: string | null): void {
		this.ausgewaehlt = name;
		for (const [n, zeile] of this.zeilenMap()) {
			zeile.toggleClass("ocr-selektiert", n === name);
		}
	}

	/** Gefilterte Liste, in Anzeigereihenfolge. */
	private gefiltert(): Bestandseintrag[] {
		const filter = this.filter;
		const text = this.textFilter;
		return this.bestand.filter(
			(b) =>
				passtZumFilter(b.eintrag.status, filter) &&
				(text.length === 0 || b.name.toLowerCase().includes(text)),
		);
	}

	/** Der Eintrag nach `name` in der gefilterten Liste — fuer das
	 *  Weiterspringen nach einer Entscheidung. */
	naechsterNach(name: string): string | null {
		const liste = this.gefiltert();
		const index = liste.findIndex((b) => b.name === name);
		return index >= 0 ? (liste[index + 1]?.name ?? null) : null;
	}

	/** Erster Eintrag der gefilterten Liste — fuer das Oeffnen ohne Auswahl. */
	ersterSichtbar(): string | null {
		return this.gefiltert()[0]?.name ?? null;
	}

	/** Naechster/vorheriger Eintrag relativ zur Auswahl. Waehlt und liefert. */
	verschiebeAuswahl(schritt: number): string | null {
		const liste = this.gefiltert();
		if (liste.length === 0) return null;
		let index = liste.findIndex((b) => b.name === this.ausgewaehlt);
		if (index < 0) index = schritt < 0 ? liste.length : -1;
		const ziel = liste[Math.min(Math.max(index + schritt, 0), liste.length - 1)];
		if (ziel === undefined) return null;
		this.setzeAuswahl(ziel.name);
		return ziel.name;
	}

	/** Anzahl der Eintraege unter dem aktuellen Filter — fuer den Kopf. */
	anzahl(): number {
		return this.gefiltert().length;
	}

	private zeichnen(): void {
		this.chipsAktualisieren();
		const gefiltert = this.gefiltert();
		const leer =
			this.ordnerFehlt || this.bestand.length === 0 || gefiltert.length === 0;
		this.listeEl.toggleClass("ocr-liste-versteckt", leer);
		this.listeEl.empty();
		this.leereEl.empty();
		this.leereEl.hide();

		if (this.ordnerFehlt) {
			this.leerZustand(
				`Der Vorschau-Ordner „${this.vorschauOrdner()}“ existiert nicht.`,
				"Den Ordner gibt es erst nach dem ersten pdf2md-Lauf.",
				"Einstellungen öffnen",
				() => this.beiEinstellungen?.(),
			);
			return;
		}
		if (this.bestand.length === 0) {
			const befehl = pdf2mdBefehl(this.vorschauOrdner());
			this.leerZustand(
				"Keine Vorschau-Dateien im Vorschau-Ordner.",
				"",
				"",
				null,
				befehl,
			);
			return;
		}
		if (gefiltert.length === 0) {
			if (this.textFilter.length > 0) {
				this.leerZustand(`Kein Treffer für „${this.suchfeld.value.trim()}“.`, "", "", null);
			} else {
				const texte: Record<StatusFilter, string> = {
					offen: "Nichts offen. Alles begutachtet.",
					akzeptiert: "Noch nichts angenommen.",
					abgelehnt: "Noch nichts abgelehnt.",
					alle: "Keine Vorschau-Dateien.",
				};
				this.leerZustand(texte[this.filter], "", "", null);
			}
			return;
		}

		for (const b of gefiltert) {
			this.listeEl.appendChild(this.zeileBauen(b));
		}
	}

	private zeileBauen(b: Bestandseintrag): HTMLElement {
		const zeile = this.listeEl.createDiv({ cls: "ocr-eintrag" });
		zeile.addClass(`ocr-status-${b.eintrag.status}`);
		zeile.toggleClass("ocr-selektiert", b.name === this.ausgewaehlt);
		zeile.dataset["name"] = b.name;
		zeile.setAttribute("title", b.datei.path);
		zeile.addEventListener("click", () => {
			this.setzeAuswahl(b.name);
			this.beiAuswahl?.(b.name);
		});

		const ober = zeile.createDiv({ cls: "ocr-eintrag-ober" });
		if ((b.eintrag["seiten-ocr"] ?? 0) > 0) {
			ober.createSpan({
				cls: "ocr-warnpunkt",
				attr: { "aria-label": "Enthält OCR-Seiten — Wortfehler möglich", title: "Enthält OCR-Seiten — Wortfehler möglich" },
			});
		}
		ober.createSpan({ cls: "ocr-eintrag-stem", text: b.datei.basename });
		if (b.eintrag.status === "neu-erzeugt") {
			const vorher = b.eintrag.vorher;
			const badge = ober.createSpan({ cls: "ocr-badge ocr-badge-neu", text: "Neu erzeugt" });
			badge.setAttribute(
				"aria-label",
				vorher !== null
					? `Vorher ${vorher.status} (${vorher["ocr-datum"] ?? "Datum unbekannt"})`
					: "Vorherige Entscheidung unbekannt",
			);
		}

		const unterzeile = this.unterzeile(b.eintrag);
		if (unterzeile.length > 0) {
			zeile.createDiv({ cls: "ocr-eintrag-unter", text: unterzeile });
		}
		return zeile;
	}

	private unterzeile(e: Bestandseintrag["eintrag"]): string {
		const teile: string[] = [];
		if (e.seiten !== null) teile.push(`${e.seiten} S.`);
		if (e["seiten-ocr"] !== null) teile.push(`${e["seiten-ocr"]} OCR`);
		if (e["seiten-diagramm"] !== null) teile.push(`${e["seiten-diagramm"]} Diagramm`);
		return teile.join(" · ");
	}

	private chipsAktualisieren(): void {
		const zaehlen = (filter: StatusFilter) =>
			this.bestand.filter((b) => passtZumFilter(b.eintrag.status, filter)).length;
		for (const option of FILTER) {
			const chip = this.filterZeile.querySelector<HTMLElement>(
				`[data-filter="${option.wert}"]`,
			);
			if (chip === null) continue;
			chip.toggleClass("ocr-chip-aktiv", this.filter === option.wert);
			chip.setText(`${option.label} (${zaehlen(option.wert)})`);
		}
	}

	private leerZustand(
		titel: string,
		untertitel: string,
		knopfText: string,
		knopfAktion: (() => void) | null,
		befehl?: string,
	): void {
		this.leereEl.show();
		this.leereEl.createDiv({ cls: "ocr-leer-titel", text: titel });
		if (untertitel.length > 0) {
			this.leereEl.createDiv({ cls: "ocr-leer-untertitel", text: untertitel });
		}
		if (befehl !== undefined) {
			const pre = this.leereEl.createEl("pre", { cls: "ocr-kopierbefehl" });
			pre.createEl("code", { text: befehl });
			pre.addEventListener("click", () => {
				navigator.clipboard
					.writeText(befehl)
					.then(() => new Notice("Befehl kopiert — Pfad der PDF einsetzen."))
					.catch(() => new Notice("Kopieren fehlgeschlagen."));
			});
		}
		if (knopfText.length > 0 && knopfAktion !== null) {
			this.leereEl.createEl("button", { cls: "ocr-knopf", text: knopfText })
				.addEventListener("click", () => knopfAktion());
		}
	}

	private zeilenMap(): Map<string, HTMLElement> {
		const m = new Map<string, HTMLElement>();
		for (const zeile of this.listeEl.querySelectorAll<HTMLElement>(".ocr-eintrag")) {
			const name = zeile.dataset["name"];
			if (name !== undefined) m.set(name, zeile);
		}
		return m;
	}
}
