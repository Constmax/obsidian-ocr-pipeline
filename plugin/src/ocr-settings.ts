// Stage-1 OCR settings for the searchable-copy action: type, defaults,
// validation of loaded plugin data, and the engines the settings tab offers.
// Free of Obsidian imports so it runs under `node --test`.

/** Engines the settings can store. PaddleOCR runs in fast mode and is offered
 *  only where `reprocess-raw --check-engine` finds it usable (issue #73); the
 *  benchmark in #71 retained it (docs/paddle-textlayer.md). */
export const OCR_ENGINES = ["auto", "apple", "tesseract", "paddle"] as const;
export type OcrEngine = (typeof OCR_ENGINES)[number];

export interface OcrSettings {
	/** Passed to `reprocess-raw --engine`; `auto` prefers PaddleOCR fast when it is ready and
	 *  no split is requested, then Apple Vision, then Tesseract (#198). */
	ocrEngine: OcrEngine;
	/** Passes `--split-columns`: two-column pages are split before OCR and merged back. */
	splitColumns: boolean;
	/** Passed as `--dpi`: scans above it are downscaled before OCR; 0 = off (issue #201). */
	maxDpi: number;
}

export const DEFAULT_OCR_SETTINGS: OcrSettings = {
	ocrEngine: "auto",
	splitColumns: false,
	maxDpi: 300,
};

/** Shown instead of the OCR controls where the action cannot run. */
export const DESKTOP_ONLY_MESSAGE =
	"Searchable copies are only available in Obsidian for desktop: " +
	"OCR runs the locally installed reprocess-raw command.";

export function isOcrEngine(value: unknown): value is OcrEngine {
	return typeof value === "string" && (OCR_ENGINES as readonly string[]).includes(value);
}

/** Above any scan resolution worth keeping; also keeps bash arithmetic in range. */
export const MAX_DPI_LIMIT = 2400;

/** A whole number from 0 (off) to MAX_DPI_LIMIT, as `reprocess-raw --dpi` accepts it. */
export function isMaxDpi(value: unknown): value is number {
	return Number.isInteger(value) && (value as number) >= 0 && (value as number) <= MAX_DPI_LIMIT;
}

/** The DPI text field: empty means the default; null for anything invalid. */
export function parseMaxDpiInput(text: string): number | null {
	const value = text.trim() === "" ? DEFAULT_OCR_SETTINGS.maxDpi : Number(text);
	return isMaxDpi(value) ? value : null;
}

/**
 * OCR settings from saved plugin data. Data from before these settings existed
 * has neither key; an unknown engine (for example "easyocr") or a non-boolean
 * flag is invalid. Missing and invalid values fall back to the defaults field
 * by field, so one bad value does not reset the other.
 */
export function parseOcrSettings(saved: unknown): OcrSettings {
	const data =
		typeof saved === "object" && saved !== null ? (saved as Record<string, unknown>) : {};
	return {
		ocrEngine: isOcrEngine(data.ocrEngine) ? data.ocrEngine : DEFAULT_OCR_SETTINGS.ocrEngine,
		splitColumns:
			typeof data.splitColumns === "boolean"
				? data.splitColumns
				: DEFAULT_OCR_SETTINGS.splitColumns,
		maxDpi: isMaxDpi(data.maxDpi) ? data.maxDpi : DEFAULT_OCR_SETTINGS.maxDpi,
	};
}
