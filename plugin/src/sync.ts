// Scroll-Kopplung zwischen PDF- und Markdown-Spalte.
//
// Die Abbildung ist BRUCHTEILSWEISE, nicht auf ganze Seiten gerundet: eine
// PDF-Seite und ihr Markdown-Block haben im Normalfall sehr verschiedene Hoehen,
// und ein Sprung auf Seitenanfaenge fuehlt sich dann falsch an.
//
//   Treiber:  Block am Viewport-Oberrand suchen
//             p = nr + (scrollTop - blockTop) / blockHoehe        // reelle Zahl
//   Folger:   ziel = block(floor(p)).top + (p - floor(p)) * hoehe
//
// Die Rueckkopplung — das programmatische Setzen von `scrollTop` loest im
// Folger selbst ein `scroll`-Ereignis aus — wird dreifach abgesichert:
//
//   1. Besitzer-Token mit nachgetriggertem Timeout. Der Lock loest 120 ms nach
//      dem LETZTEN Ereignis des Treibers, nicht 120 ms nach dem ersten.
//   2. Kein `scrollIntoView({behavior:'smooth'})`. Sanftes Scrollen erzeugt
//      einen langen Ereignisschweif, der den Lock ueberlebt und die Schleife
//      zuverlaessig zurueckbringt. Direkt `scrollTop` schreiben, gebuendelt auf
//      einen Schreibvorgang pro Frame.
//   3. Epsilon von 2 px gegen Restzittern aus Subpixel-Rundung.

export type Spalte = "pdf" | "md";

export interface SyncQuelle {
	scrollEl: HTMLElement;
	/** Seitennummer → verankerndes Element. Luecken sind normal (z.B. bei
	 *  `--nur-ocr`), es wird auf den naechstgelegenen Schluessel geklemmt. */
	elemente(): Map<number, HTMLElement>;
}

interface Vermessung {
	nummern: number[];
	top: Map<number, number>;
	hoehe: Map<number, number>;
}

const LOCK_MS = 120;
const EPSILON = 2;

function vermessen(quelle: SyncQuelle): Vermessung {
	const elemente = quelle.elemente();
	const basis = quelle.scrollEl.getBoundingClientRect().top;
	const scrollTop = quelle.scrollEl.scrollTop;
	const top = new Map<number, number>();
	const hoehe = new Map<number, number>();
	for (const [nr, el] of elemente) {
		const rect = el.getBoundingClientRect();
		top.set(nr, rect.top - basis + scrollTop);
		hoehe.set(nr, Math.max(rect.height, 1));
	}
	return {
		nummern: [...elemente.keys()].sort((a, b) => a - b),
		top,
		hoehe,
	};
}

/** Groesster Schluessel <= nr, sonst der kleinste vorhandene. */
function naechsteNummer(nummern: number[], nr: number): number | null {
	if (nummern.length === 0) return null;
	let treffer: number | null = null;
	for (const kandidat of nummern) {
		if (kandidat <= nr) treffer = kandidat;
		else break;
	}
	return treffer ?? nummern[0] ?? null;
}

export class Kopplung {
	aktiv = true;
	/** Wird bei jeder Positionsaenderung mit der aktuellen Bruchteilsseite
	 *  gerufen — die Spaltenkoepfe zeigen daraus „S. n / m". */
	beiSeite: ((seite: number) => void) | null = null;

	private messungen = new Map<Spalte, Vermessung>();
	private treiber: Spalte | null = null;
	private lockTimer: number | null = null;
	private rafId: number | null = null;
	private ausstehend: Array<[Spalte, number]> = [];
	private abmelden: Array<() => void> = [];
	private quellen: Record<Spalte, SyncQuelle>;

	constructor(quellen: Record<Spalte, SyncQuelle>) {
		this.quellen = quellen;
		for (const spalte of ["pdf", "md"] as const) {
			const quelle = quellen[spalte];
			const handler = () => this.beiScroll(spalte);
			quelle.scrollEl.addEventListener("scroll", handler, { passive: true });
			this.abmelden.push(() =>
				quelle.scrollEl.removeEventListener("scroll", handler),
			);
		}
	}

	zerstoeren(): void {
		for (const ab of this.abmelden) ab();
		this.abmelden = [];
		if (this.lockTimer !== null) window.clearTimeout(this.lockTimer);
		if (this.rafId !== null) window.cancelAnimationFrame(this.rafId);
	}

	/** Nach jedem Ereignis rufen, das Hoehen aendert: Block fertig gerendert,
	 *  ResizeObserver, Canvas eingewechselt, Gerendert/Quelltext umgeschaltet. */
	neuVermessen(): void {
		for (const spalte of ["pdf", "md"] as const) {
			this.messungen.set(spalte, vermessen(this.quellen[spalte]));
		}
	}

	/** Aktuelle Bruchteilsseite einer Spalte. */
	position(spalte: Spalte): number | null {
		const messung = this.messungen.get(spalte);
		if (messung === undefined || messung.nummern.length === 0) return null;
		const scrollTop = this.quellen[spalte].scrollEl.scrollTop;
		let treffer = messung.nummern[0] as number;
		for (const nr of messung.nummern) {
			const top = messung.top.get(nr);
			if (top === undefined) continue;
			if (top <= scrollTop + 1) treffer = nr;
			else break;
		}
		const top = messung.top.get(treffer) ?? 0;
		const hoehe = messung.hoehe.get(treffer) ?? 1;
		const anteil = Math.min(Math.max((scrollTop - top) / hoehe, 0), 0.999);
		return treffer + anteil;
	}

	/** Beide Spalten auf eine Seite setzen — fuer „Gehe zu Seite" und die
	 *  Seitenleiste. Setzt den Treiber fuer einen Frame, damit die daraus
	 *  entstehenden Scroll-Ereignisse nichts zurueckspielen. */
	zuSeite(nr: number): void {
		this.treiber = "pdf";
		this.lockNachtriggern();
		for (const spalte of ["pdf", "md"] as const) {
			const messung = this.messungen.get(spalte);
			if (messung === undefined) continue;
			const ziel = naechsteNummer(messung.nummern, nr);
			if (ziel === null) continue;
			this.schreiben(spalte, messung.top.get(ziel) ?? 0);
		}
		this.beiSeite?.(nr);
	}

	private lockNachtriggern(): void {
		if (this.lockTimer !== null) window.clearTimeout(this.lockTimer);
		this.lockTimer = window.setTimeout(() => {
			this.treiber = null;
			this.lockTimer = null;
		}, LOCK_MS);
	}

	private beiScroll(spalte: Spalte): void {
		// Sicherung 1: solange eine andere Spalte treibt, ist dieses Ereignis
		// die Folge unseres eigenen Schreibvorgangs.
		if (this.treiber !== null && this.treiber !== spalte) return;
		this.treiber = spalte;
		this.lockNachtriggern();

		const p = this.position(spalte);
		if (p === null) return;
		this.beiSeite?.(p);
		if (!this.aktiv) return;

		const folger: Spalte = spalte === "pdf" ? "md" : "pdf";
		const messung = this.messungen.get(folger);
		if (messung === undefined || messung.nummern.length === 0) return;
		const ganz = Math.floor(p);
		const ziel = naechsteNummer(messung.nummern, ganz);
		if (ziel === null) return;
		const top = messung.top.get(ziel) ?? 0;
		const hoehe = messung.hoehe.get(ziel) ?? 1;
		// Der Bruchteil gilt nur, wenn wirklich dieselbe Seite getroffen wurde;
		// beim Klemmen auf einen Nachbarn waere er sinnlos.
		const anteil = ziel === ganz ? p - ganz : 0;
		this.planen(folger, top + anteil * hoehe);
	}

	/** Sicherung 2: mehrere Scroll-Ereignisse werden zu einem Schreibvorgang je
	 *  Frame gebuendelt, und geschrieben wird direkt — nie `smooth`. */
	private planen(spalte: Spalte, ziel: number): void {
		this.ausstehend = this.ausstehend.filter(([s]) => s !== spalte);
		this.ausstehend.push([spalte, ziel]);
		if (this.rafId !== null) return;
		this.rafId = window.requestAnimationFrame(() => {
			this.rafId = null;
			const arbeit = this.ausstehend;
			this.ausstehend = [];
			for (const [s, z] of arbeit) this.schreiben(s, z);
		});
	}

	private schreiben(spalte: Spalte, ziel: number): void {
		const el = this.quellen[spalte].scrollEl;
		const begrenzt = Math.min(
			Math.max(ziel, 0),
			Math.max(el.scrollHeight - el.clientHeight, 0),
		);
		// Sicherung 3: Restzittern aus Subpixel-Rundung nicht weitertragen.
		if (Math.abs(begrenzt - el.scrollTop) < EPSILON) return;
		el.scrollTop = begrenzt;
	}
}
