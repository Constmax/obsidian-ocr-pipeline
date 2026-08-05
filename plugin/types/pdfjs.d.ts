// Minimale Typen fuer die pdf.js-Instanz, die Obsidian selbst mitbringt.
//
// `loadPdfJs()` aus der Obsidian-API ist als `Promise<any>` deklariert und
// liefert damit keine Typinformation. Statt `pdfjs-dist` als Typquelle
// hereinzuziehen — deren Typen beschreiben das Upstream-pdf.js, Obsidian liefert
// einen Fork — steht hier von Hand genau die Flaeche, die das Plugin benutzt.
//
// Der Umfang dieser Datei ist damit zugleich die Dokumentation der Abhaengigkeit:
// waechst sie, waechst das Risiko bei einem Obsidian-Update. Alle Aufrufe laufen
// durch `pdf-pane.ts`.

export {};

/** Seitengeometrie in CSS-Pixeln bei einem gegebenen Massstab. */
export interface PdfViewport {
	width: number;
	height: number;
}

/** Laufender Rastervorgang. `cancel()` wirft in `promise` eine
 *  RenderingCancelledException — die ist erwartet und wird geschluckt. */
export interface PdfRenderTask {
	promise: Promise<void>;
	cancel(): void;
}

export interface PdfSeite {
	getViewport(optionen: { scale: number }): PdfViewport;
	render(optionen: {
		canvasContext: CanvasRenderingContext2D;
		viewport: PdfViewport;
	}): PdfRenderTask;
	cleanup(): void;
}

export interface PdfDokument {
	numPages: number;
	getPage(nr: number): Promise<PdfSeite>;
	destroy(): Promise<void>;
}

export interface PdfLadevorgang {
	promise: Promise<PdfDokument>;
	destroy(): Promise<void>;
}

export interface PdfJsLib {
	getDocument(optionen: {
		url?: string;
		data?: Uint8Array;
		cMapUrl?: string;
		cMapPacked?: boolean;
		standardFontDataUrl?: string;
	}): PdfLadevorgang;
	GlobalWorkerOptions: { workerSrc: string };
}

declare global {
	interface Window {
		pdfjsLib?: PdfJsLib;
	}
}
