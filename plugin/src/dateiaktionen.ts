import { App, Notice, TFile, normalizePath } from "obsidian";

import type { Einstellungen } from "./einstellungen.ts";
import {
	abgleichen,
	altesDatumAus,
	entscheidungEintragen,
	leeresManifest,
	manifestLesen,
	manifestSchreiben,
	zielordner,
} from "./status.ts";
import type {
	GefundeneDatei,
	Ordnerlage,
	StatusEintrag,
	StatusManifest,
} from "./typen.ts";

export interface Bestandseintrag {
	name: string;
	datei: TFile;
	eintrag: StatusEintrag;
}

interface LetzteAktion {
	name: string;
	vonPfad: string;
	nachPfad: string;
	vorherigerEintrag: StatusEintrag;
}

/**
 * Haelt das Manifest, gleicht es mit den drei Ordnern ab und fuehrt die
 * Entscheidungen aus.
 *
 * Verschoben wird ausschliesslich ueber `fileManager.renameFile` — nicht ueber
 * `vault.rename`. Der Unterschied ist nicht kosmetisch: `renameFile`
 * aktualisiert die Links im Vault, `vault.rename` bewegt nur Bytes. Konkret
 * haengt daran der `![[…png]]`-Einbettungslink der Diagrammseiten
 * (pdf2md.py:1619), dessen Bild in `<vorschau>/assets/` liegen bleibt.
 */
export class Bestand {
	manifest: StatusManifest = leeresManifest(new Date().toISOString());
	eintraege: Bestandseintrag[] = [];

	private schreibKette: Promise<void> = Promise.resolve();
	private schreibTimer: number | null = null;
	private letzte: LetzteAktion | null = null;
	/** Pfad der Manifest-Datei waehrend eines eigenen Schreibvorgangs — damit
	 *  der modify-Listener den eigenen Schreibvorgang nicht als Fremdaenderung
	 *  deutet und einen Abgleich-Schreib-Kreisel startet. */
	private eigenerSchreibvorgang = false;

	constructor(
		private app: App,
		private einstellungen: () => Einstellungen,
	) {}

	private get e(): Einstellungen {
		return this.einstellungen();
	}

	istManifestPfad(pfad: string): boolean {
		return (
			this.eigenerSchreibvorgang && normalizePath(pfad) === normalizePath(this.e.statusDatei)
		);
	}

	/** Sammelt die .md aus den drei Ordnern.
	 *
	 *  EXAKTER Elternpfad-Vergleich, kein `startsWith`: `_akzeptiert` liegt
	 *  innerhalb von `_ocr-vorschau`, ein Praefixtest listete angenommene
	 *  Dateien als offen. */
	private dateienSammeln(): GefundeneDatei[] {
		const e = this.e;
		const lagen: Array<[Ordnerlage, string]> = [
			["offen", normalizePath(e.vorschauOrdner)],
			["akzeptiert", normalizePath(e.akzeptiertOrdner)],
			["abgelehnt", normalizePath(e.abgelehntOrdner)],
		];
		const gefunden: GefundeneDatei[] = [];
		for (const datei of this.app.vault.getMarkdownFiles()) {
			const eltern = datei.parent?.path ?? "";
			for (const [lage, ordner] of lagen) {
				if (eltern !== ordner) continue;
				const cache = this.app.metadataCache.getFileCache(datei);
				gefunden.push({
					name: datei.name,
					pfad: datei.path,
					lage,
					frontmatter: cache?.frontmatter ?? {},
				});
				break;
			}
		}
		return gefunden;
	}

	async laden(): Promise<void> {
		const jetzt = new Date().toISOString();
		const pfad = normalizePath(this.e.statusDatei);
		let vorher = leeresManifest(jetzt);
		if (await this.app.vault.adapter.exists(pfad)) {
			try {
				vorher = manifestLesen(await this.app.vault.adapter.read(pfad), jetzt);
			} catch (fehler) {
				// Kaputtes JSON darf den Durchgang nicht blockieren: alles ausser
				// `notiz` und `geprueft-bis` baut sich aus der Ordnerlage neu auf.
				console.error("OCR-Vorschau: review-status.json ist unlesbar", fehler);
				const kaputt = `${pfad}.kaputt`;
				try {
					if (await this.app.vault.adapter.exists(kaputt)) {
						await this.app.vault.adapter.remove(kaputt);
					}
					await this.app.vault.adapter.rename(pfad, kaputt);
					new Notice(
						`OCR-Vorschau: review-status.json war unlesbar und liegt jetzt als ${kaputt}. Status aus der Ordnerlage neu aufgebaut.`,
						8000,
					);
				} catch (umbenennFehler) {
					console.error("OCR-Vorschau: Umbenennen fehlgeschlagen", umbenennFehler);
				}
			}
		}
		await this.abgleichen(vorher);
	}

	async abgleichen(vorher: StatusManifest = this.manifest): Promise<void> {
		const jetzt = new Date().toISOString();
		const dateien = this.dateienSammeln();
		const ergebnis = abgleichen(dateien, vorher, jetzt, (p) =>
			this.app.vault.getFileByPath(normalizePath(p)) !== null,
		);
		this.manifest = ergebnis.manifest;

		// Zurueck auf TFile abbilden. Bei zwei Fassungen desselben Namens ist die
		// offene die massgebliche — pdf2md.py schreibt immer dorthin.
		const nachName = new Map<string, TFile>();
		for (const gefunden of dateien) {
			const datei = this.app.vault.getFileByPath(gefunden.pfad);
			if (datei === null) continue;
			if (gefunden.lage === "offen" || !nachName.has(gefunden.name)) {
				nachName.set(gefunden.name, datei);
			}
		}
		this.eintraege = [];
		for (const [name, eintrag] of Object.entries(this.manifest.eintraege)) {
			const datei = nachName.get(name);
			if (datei === undefined) continue; // uebernommen — nicht mehr listen
			this.eintraege.push({ name, datei, eintrag });
		}
		this.eintraege.sort((a, b) => a.name.localeCompare(b.name, "de"));

		if (ergebnis.korrigiert.length > 0) {
			console.warn(
				"OCR-Vorschau: Status aus Ordnerlage übernommen für",
				ergebnis.korrigiert.join(", "),
			);
		}
		this.speichernAnstossen();
	}

	/** Entprellt und serialisiert. Zwei schnelle Entscheidungen duerfen sich
	 *  nicht verschraenken und dabei eine verlieren. */
	speichernAnstossen(): void {
		if (this.schreibTimer !== null) window.clearTimeout(this.schreibTimer);
		this.schreibTimer = window.setTimeout(() => {
			this.schreibTimer = null;
			this.schreibKette = this.schreibKette.then(() => this.schreiben());
		}, 500);
	}

	async sofortSpeichern(): Promise<void> {
		if (this.schreibTimer !== null) {
			window.clearTimeout(this.schreibTimer);
			this.schreibTimer = null;
		}
		this.schreibKette = this.schreibKette.then(() => this.schreiben());
		await this.schreibKette;
	}

	private async schreiben(): Promise<void> {
		const pfad = normalizePath(this.e.statusDatei);
		const ordner = pfad.slice(0, pfad.lastIndexOf("/"));
		try {
			this.eigenerSchreibvorgang = true;
			if (ordner.length > 0 && !(await this.app.vault.adapter.exists(ordner))) {
				await this.app.vault.adapter.mkdir(ordner);
			}
			await this.app.vault.adapter.write(pfad, manifestSchreiben(this.manifest));
		} catch (fehler) {
			console.error("OCR-Vorschau: review-status.json nicht schreibbar", fehler);
			new Notice("OCR-Vorschau: Status konnte nicht gespeichert werden.");
		} finally {
			// Erst im naechsten Tick freigeben — das modify-Ereignis des eigenen
			// Schreibvorgangs kommt asynchron.
			window.setTimeout(() => {
				this.eigenerSchreibvorgang = false;
			}, 0);
		}
	}

	private async ordnerSicherstellen(pfad: string): Promise<void> {
		if (this.app.vault.getFolderByPath(pfad) !== null) return;
		try {
			await this.app.vault.createFolder(pfad);
		} catch (fehler) {
			// „Folder already exists" — zwei Aufrufe im selben Tick. Unschaedlich.
			if (this.app.vault.getFolderByPath(pfad) === null) throw fehler;
		}
	}

	/** Verschiebt die Datei und traegt die Entscheidung ein. */
	async entscheiden(name: string, lage: Ordnerlage): Promise<boolean> {
		const bestand = this.eintraege.find((b) => b.name === name);
		if (bestand === undefined) return false;
		const ziel = normalizePath(zielordner(lage, this.e));
		const neuerPfad = normalizePath(`${ziel}/${bestand.datei.name}`);
		if (neuerPfad === bestand.datei.path) return false;

		const vorherigerEintrag = { ...bestand.eintrag };
		const vonPfad = bestand.datei.path;
		try {
			await this.ordnerSicherstellen(ziel);
			await this.app.fileManager.renameFile(bestand.datei, neuerPfad);
		} catch (fehler) {
			console.error("OCR-Vorschau: Verschieben fehlgeschlagen", fehler);
			new Notice(`OCR-Vorschau: „${name}“ konnte nicht verschoben werden.`);
			return false;
		}

		this.manifest = entscheidungEintragen(
			this.manifest,
			name,
			lage,
			neuerPfad,
			new Date().toISOString(),
		);
		this.letzte = { name, vonPfad, nachPfad: neuerPfad, vorherigerEintrag };
		await this.abgleichen();
		return true;
	}

	kannRueckgaengig(): boolean {
		return this.letzte !== null;
	}

	/** Macht die letzte Entscheidung rueckgaengig. Liefert den Namen der
	 *  wiederhergestellten Datei, oder null, wenn nichts zurueckzunehmen war. */
	async rueckgaengig(): Promise<string | null> {
		const letzte = this.letzte;
		if (letzte === null) return null;
		const datei = this.app.vault.getFileByPath(letzte.nachPfad);
		if (datei === null) {
			this.letzte = null;
			return null;
		}
		try {
			const ordner = letzte.vonPfad.slice(0, letzte.vonPfad.lastIndexOf("/"));
			await this.ordnerSicherstellen(ordner);
			await this.app.fileManager.renameFile(datei, letzte.vonPfad);
		} catch (fehler) {
			console.error("OCR-Vorschau: Rückgängig fehlgeschlagen", fehler);
			return null;
		}
		this.manifest = {
			...this.manifest,
			eintraege: {
				...this.manifest.eintraege,
				[letzte.name]: letzte.vorherigerEintrag,
			},
		};
		this.letzte = null;
		await this.abgleichen();
		return letzte.name;
	}

	/** Anmerkungsfelder ändern, ohne die Datei anzufassen. */
	async eintragAendern(
		name: string,
		aenderung: Partial<Pick<StatusEintrag, "notiz" | "geprueft-bis" | "quelle-pdf-manuell">>,
	): Promise<void> {
		const alt = this.manifest.eintraege[name];
		if (alt === undefined) return;
		this.manifest = {
			...this.manifest,
			eintraege: { ...this.manifest.eintraege, [name]: { ...alt, ...aenderung } },
		};
		const bestand = this.eintraege.find((b) => b.name === name);
		if (bestand !== undefined) {
			bestand.eintrag = this.manifest.eintraege[name] as StatusEintrag;
		}
		this.speichernAnstossen();
	}

	/**
	 * „Alte Fassung ersetzen" (Regel 6): benennt die alte Fassung einer
	 * Neukonvertierung nach `_abgelehnt/<stem>-<altes-ocr-datum>.md` um, damit
	 * der Basename fuer die frische Fassung frei wird. Nichts geht verloren:
	 * die alte Fassung bleibt als Datei erhalten und bekommt ueber den
	 * Abgleich einen eigenen Manifest-Eintrag.
	 */
	async alteFassungErsetzen(name: string): Promise<boolean> {
		const e = this.e;
		const akzeptiert = normalizePath(e.akzeptiertOrdner);
		const abgelehnt = normalizePath(e.abgelehntOrdner);
		const datei = this.app.vault
			.getMarkdownFiles()
			.find(
				(f) =>
					f.name === name &&
					(f.parent?.path === akzeptiert || f.parent?.path === abgelehnt),
			);
		if (datei === undefined) return false;
		// Das Datum der ALTEN Fassung: das Frontmatter der Datei selbst,
		// sonst die Erinnerung vor der Neukonvertierung. Der Eintrag traegt
		// inzwischen das neue Datum — damit wuerde das Archiv falsch heissen.
		const cache = this.app.metadataCache.getFileCache(datei);
		const altesDatum = altesDatumAus(
			this.manifest.eintraege[name],
			cache?.frontmatter ?? {},
		);
		// Schon belegt (z.B. doppelte Ersetzung mit gleichem Datum)? Dann
		// numerieren statt scheitern — die alte Fassung soll den Abgleich
		// nie blockieren.
		let ziel = normalizePath(`${abgelehnt}/${datei.basename}-${altesDatum}.md`);
		let versuch = 2;
		while (this.app.vault.getFileByPath(ziel) !== null) {
			ziel = normalizePath(
				`${abgelehnt}/${datei.basename}-${altesDatum}-${versuch}.md`,
			);
			versuch++;
		}
		try {
			await this.ordnerSicherstellen(abgelehnt);
			await this.app.fileManager.renameFile(datei, ziel);
		} catch (fehler) {
			console.error("OCR-Vorschau: Alte Fassung nicht umbenennbar", fehler);
			new Notice(`OCR-Vorschau: „${name}“ konnte nicht ersetzt werden.`);
			return false;
		}
		// Die frische Fassung ist jetzt die einzige — zurueck zu einem offenen
		// Eintrag; die Historie bleibt in `vorher` stehen.
		this.manifest = {
			...this.manifest,
			eintraege: {
				...this.manifest.eintraege,
				[name]: {
					...(this.manifest.eintraege[name] as StatusEintrag),
					status: "offen",
					entschieden: null,
				},
			},
		};
		await this.abgleichen();
		return true;
	}
}
