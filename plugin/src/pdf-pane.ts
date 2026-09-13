// Original PDF rendered page-by-page lazily.
//
// All pdf.js calls are isolated to this file, actual rendering in `drawPage(page, canvas, scale)`.
// Placeholder strategy: after `getDocument`, ALL viewports at scale 1 are queried.
// Custom properties set CSS aspect-ratio on containers before rasterization,
// preventing jumpiness on lazy loading.

import { App, TFile } from "obsidian";
import { loadPdfJs } from "obsidian";

import type {
	PdfDokument,
	PdfJsLib,
	PdfRenderTask,
	PdfSeite,
	PdfViewport,
} from "../types/pdfjs.d.ts";

const PARALLEL = 2;
const MAX_CANVAS = 12;
const RESIZE_DEBOUNCE_MS = 150;
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 2;

interface PageState {
	nr: number;
	el: HTMLElement;
	canvas: HTMLCanvasElement;
	page: PdfSeite | null;
	viewport: PdfViewport | null;
	task: PdfRenderTask | null;
	rendered: boolean;
	visible: boolean;
	lastUsed: number;
}

interface DrawTask {
	nr: number;
	force: boolean;
}

export class PdfColumn {
	readonly scrollEl: HTMLElement;

	onMeasurementNeeded: (() => void) | null = null;
	onError: ((error: string | null) => void) | null = null;
	onLoaded: ((name: string | null, pages: number) => void) | null = null;

	private container: HTMLElement;
	private stack: HTMLElement;
	private emptyState: HTMLElement;
	private pages = new Map<number, PageState>();
	private doc: PdfDokument | null = null;
	private pdfFile: TFile | null = null;
	private run = 0;
	private observer: IntersectionObserver | null = null;
	private resizeObserver: ResizeObserver | null = null;
	private resizeTimer: number | null = null;
	private queue: DrawTask[] = [];
	private requested = new Set<number>();
	private activeDraws = 0;
	private zoomLevel = 1;

	constructor(
		private app: App,
		root: HTMLElement,
		private zoomMax: () => number,
	) {
		this.scrollEl = root.createDiv({ cls: "ocr-pdf-scroll" });
		this.container = this.scrollEl.createDiv({ cls: "ocr-pdf-inhalt" });
		this.stack = this.container.createDiv({ cls: "ocr-pdf-stapel" });
		this.emptyState = this.container.createDiv({
			cls: "ocr-leer ocr-pdf-leer",
			text: "No original PDF loaded.",
		});
		this.resizeObserver = new ResizeObserver(() => this.debouncedMeasure());
		this.resizeObserver.observe(this.scrollEl);
	}

	elements(): Map<number, HTMLElement> {
		const m = new Map<number, HTMLElement>();
		for (const [nr, z] of this.pages) m.set(nr, z.el);
		return m;
	}

	currentZoom(): number {
		return this.zoomLevel;
	}

	zoom(step: number): void {
		const prev = this.zoomLevel;
		this.zoomLevel = Math.min(Math.max(this.zoomLevel + step, ZOOM_MIN), ZOOM_MAX);
		if (this.zoomLevel === prev) return;
		this.stack.style.setProperty("--ocr-pdf-zoom", String(this.zoomLevel));
		this.onMeasurementNeeded?.();
		for (const z of this.pages.values()) {
			if (z.visible) this.triggerRender(z.nr, true);
		}
	}

	async open(file: TFile | null): Promise<void> {
		const run = ++this.run;
		const oldDoc = this.doc;
		this.doc = null;
		if (oldDoc !== null) await oldDoc.destroy().catch(() => undefined);
		if (run !== this.run) return;
		this.observer?.disconnect();
		this.observer = null;
		this.pdfFile = null;
		this.pages.clear();
		this.stack.empty();
		this.queue = [];
		this.requested.clear();
		this.activeDraws = 0;
		this.emptyState.show();
		this.onError?.(null);

		if (file === null) {
			this.onLoaded?.(null, 0);
			return;
		}
		this.pdfFile = file;
		this.emptyState.hide();
		try {
			await this.loadDocument(file, run);
			if (run !== this.run) return;
			this.observe();
		} catch (err) {
			if (run !== this.run) return;
			console.error("OCR Preview: PDF failed to load", err);
			this.onError?.(`The PDF "${file.name}" could not be loaded.`);
		}
	}

	destroy(): void {
		this.run++;
		this.resizeObserver?.disconnect();
		this.resizeObserver = null;
		this.observer?.disconnect();
		this.observer = null;
		if (this.resizeTimer !== null) window.clearTimeout(this.resizeTimer);
		void this.doc?.destroy().catch(() => undefined);
		this.doc = null;
	}

	private async loadDocument(file: TFile, run: number): Promise<void> {
		const loaded = (typeof window.pdfjsLib === "undefined" ? await loadPdfJs() : window.pdfjsLib) as PdfJsLib;
		const pdfjs = window.pdfjsLib ?? loaded;
		if (typeof window.pdfjsLib === "undefined" && pdfjs) {
			(window as unknown as { pdfjsLib: PdfJsLib }).pdfjsLib = pdfjs;
		}
		const loading = pdfjs.getDocument({
			url: this.app.vault.getResourcePath(file),
			cMapUrl: "/lib/pdfjs/cmaps/",
			cMapPacked: true,
			standardFontDataUrl: "/lib/pdfjs/standard_fonts/",
		});
		const doc = await loading.promise;
		if (run !== this.run) {
			void doc.destroy().catch(() => undefined);
			return;
		}
		this.doc = doc;

		for (let nr = 1; nr <= doc.numPages; nr++) {
			const page = await doc.getPage(nr);
			if (run !== this.run) return;
			const viewport = page.getViewport({ scale: 1 });
			const el = this.stack.createDiv({ cls: "ocr-pdf-seite" });
			el.dataset["seite"] = String(nr);
			el.style.setProperty(
				"--ocr-seitenverhaeltnis",
				`${viewport.width} / ${viewport.height}`,
			);
			const canvas = el.createEl("canvas", { cls: "ocr-pdf-canvas" });
			this.pages.set(nr, {
				nr,
				el,
				canvas,
				page,
				viewport,
				task: null,
				rendered: false,
				visible: false,
				lastUsed: 0,
			});
		}
		this.onLoaded?.(file.name, doc.numPages);
	}

	private observe(): void {
		const observer = new IntersectionObserver(
			(entries) => {
				for (const entry of entries) {
					const nr = Number((entry.target as HTMLElement).dataset["seite"]);
					const z = this.pages.get(nr);
					if (z === undefined) continue;
					z.visible = entry.isIntersecting;
					if (entry.isIntersecting) {
						z.lastUsed = performance.now();
						this.triggerRender(nr);
					}
				}
			},
			{ root: this.scrollEl, rootMargin: "200% 0px" },
		);
		for (const z of this.pages.values()) observer.observe(z.el);
		this.observer = observer;
	}

	private debouncedMeasure(): void {
		if (this.resizeTimer !== null) window.clearTimeout(this.resizeTimer);
		this.resizeTimer = window.setTimeout(() => {
			this.resizeTimer = null;
			this.onMeasurementNeeded?.();
			for (const z of this.pages.values()) {
				if (z.visible) this.triggerRender(z.nr, true);
			}
		}, RESIZE_DEBOUNCE_MS);
	}

	private triggerRender(nr: number, force = false): void {
		const z = this.pages.get(nr);
		if (z === undefined || this.requested.has(nr)) return;
		if (z.rendered && !force) return;
		this.requested.add(nr);
		this.queue.push({ nr, force });
		this.continueRendering();
	}

	private continueRendering(): void {
		while (this.activeDraws < PARALLEL) {
			const taskItem = this.queue.shift();
			if (taskItem === undefined) break;
			this.requested.delete(taskItem.nr);
			const z = this.pages.get(taskItem.nr);
			if (z === undefined) continue;
			if (z.rendered && !taskItem.force) continue;
			this.activeDraws++;
			void this.renderPage(z)
				.catch((err: unknown) => this.pageError(z, err))
				.finally(() => {
					this.activeDraws--;
					this.continueRendering();
				});
		}
	}

	private async renderPage(z: PageState): Promise<void> {
		const doc = this.doc;
		if (doc === null) return;
		if (z.page === null) z.page = await doc.getPage(z.nr);
		const page = z.page;
		if (page === null) return;
		if (z.viewport === null) z.viewport = page.getViewport({ scale: 1 });
		this.resetError(z);
		const scale = this.scaleFor(z);
		z.task?.cancel();
		const task = this.drawPage(page, z.canvas, scale);
		z.task = task;
		try {
			await task.promise;
		} catch (err) {
			if (z.task === task) z.task = null;
			if (isCancelled(err)) return;
			this.pageError(z, err);
			return;
		}
		if (z.task === task) z.task = null;
		z.rendered = true;
		z.lastUsed = performance.now();
		this.evict();
		this.onMeasurementNeeded?.();
	}

	private drawPage(
		page: PdfSeite,
		canvas: HTMLCanvasElement,
		scale: number,
	): PdfRenderTask {
		const viewport = page.getViewport({ scale });
		canvas.width = Math.floor(viewport.width);
		canvas.height = Math.floor(viewport.height);
		const ctx = canvas.getContext("2d");
		if (ctx === null) {
			throw new Error("Canvas context not available");
		}
		return page.render({ canvasContext: ctx, viewport });
	}

	private scaleFor(z: PageState): number {
		const viewport = z.viewport;
		if (viewport === null) return 1;
		const width = Math.max(z.el.clientWidth * this.zoomLevel, 1);
		return Math.min(
			(width / viewport.width) * window.devicePixelRatio,
			this.zoomMax(),
		);
	}

	private resetError(z: PageState): void {
		z.el.removeClass("ocr-pdf-seite-fehler");
		for (const banner of z.el.querySelectorAll(".ocr-pdf-seitenfehler")) {
			banner.remove();
		}
	}

	private pageError(z: PageState, err: unknown): void {
		console.error("OCR Preview: Page failed to render", z.nr, err);
		this.resetError(z);
		z.rendered = true;
		z.el.addClass("ocr-pdf-seite-fehler");
		z.el.createDiv({
			cls: "ocr-pdf-seitenfehler",
			text: `Page ${z.nr}: display failed. Open in PDF viewer?`,
		});
	}

	private evict(): void {
		const buffered = [...this.pages.values()].filter(
			(z) => z.rendered && z.task === null,
		);
		if (buffered.length <= MAX_CANVAS) return;
		const candidates = buffered.filter((z) => !z.visible);
		candidates.sort((a, b) => a.lastUsed - b.lastUsed);
		for (const z of candidates.slice(0, buffered.length - MAX_CANVAS)) {
			z.rendered = false;
			z.canvas.width = 0;
			z.canvas.height = 0;
			z.page?.cleanup();
		}
	}
}

function isCancelled(err: unknown): boolean {
	if (err instanceof Error && err.name === "RenderingCancelledException") {
		return true;
	}
	return String(err).includes("RenderingCancelledException");
}
