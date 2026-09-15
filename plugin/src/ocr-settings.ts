// Stage-1 OCR settings for the searchable-copy action: type, defaults,
// validation of loaded plugin data, and the engines the settings tab offers.
// Free of Obsidian imports so it runs under `node --test`.

/** Engines offered in the settings. PaddleOCR joins only after its retention
 *  and installation gates pass (docs/paddle-textlayer.md). */
export const OCR_ENGINES = ["auto", "apple", "tesseract"] as const;
export type OcrEngine = (typeof OCR_ENGINES)[number];

export interface OcrSettings {
	/** Passed to `reprocess-raw --engine`; `auto` prefers Apple Vision. */
	ocrEngine: OcrEngine;
	/** Passes `--split-columns`: two-column pages are split before OCR and merged back. */
	splitColumns: boolean;
}

export const DEFAULT_OCR_SETTINGS: OcrSettings = {
	ocrEngine: "auto",
	splitColumns: false,
};

/** Shown instead of the OCR controls where the action cannot run. */
export const DESKTOP_ONLY_MESSAGE =
	"Searchable copies are only available in Obsidian for desktop: " +
	"OCR runs the locally installed reprocess-raw command.";

export function isOcrEngine(value: unknown): value is OcrEngine {
	return typeof value === "string" && (OCR_ENGINES as readonly string[]).includes(value);
}

/**
 * OCR settings from saved plugin data. Data from before these settings existed
 * has neither key; an unknown engine (for example "paddle") or a non-boolean
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
	};
}
