// Stage-1 OCR settings for the "Add OCR text layer" action: type, defaults,
// validation of loaded plugin data, and the engines the settings tab offers.
// Free of Obsidian imports so it runs under `node --test`.

/** Engines the settings can store. PaddleOCR runs in fast mode and is offered
 *  only where `reprocess-raw --check-engine` finds it usable (issue #73); the
 *  benchmark in #71 retained it (docs/paddle-textlayer.md). */
export const OCR_ENGINES = ["auto", "apple", "tesseract", "paddle"] as const;
export type OcrEngine = (typeof OCR_ENGINES)[number];

export interface OcrSettings {
	/** Passed to `reprocess-raw --engine`; `auto` prefers PaddleOCR fast when it is ready,
	 *  then Apple Vision, then Tesseract (#198). */
	ocrEngine: OcrEngine;
}

export const DEFAULT_OCR_SETTINGS: OcrSettings = {
	ocrEngine: "auto",
};

/** Shown instead of the OCR controls where the action cannot run. */
export const DESKTOP_ONLY_MESSAGE =
	"OCR text layers are only available in Obsidian for desktop: " +
	"OCR runs the locally installed reprocess-raw command.";

export function isOcrEngine(value: unknown): value is OcrEngine {
	return typeof value === "string" && (OCR_ENGINES as readonly string[]).includes(value);
}

/**
 * OCR settings from saved plugin data. Data from before this setting has no
 * engine; an unknown one (for example "easyocr") is invalid. Both fall back to
 * the default. The column split and maximum DPI settings were removed in #180
 * (in-place OCR keeps the pages); their saved values drop out.
 */
export function parseOcrSettings(saved: unknown): OcrSettings {
	const data =
		typeof saved === "object" && saved !== null ? (saved as Record<string, unknown>) : {};
	return {
		ocrEngine: isOcrEngine(data.ocrEngine) ? data.ocrEngine : DEFAULT_OCR_SETTINGS.ocrEngine,
	};
}
