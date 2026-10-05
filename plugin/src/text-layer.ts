// "Add OCR text layer": the Stage-1 action behind the PDF file menu and the
// command (never the comparison view). Runs `reprocess-raw --in-place` through
// the ConversionController with the saved OCR settings, so the PDF keeps its
// path and every link to it (#180). The CLI replaces the file only after all
// checks passed, in one rename; on failure or cancellation it stays as it was.
// The replaced file is kept (`--keep-original`) and moved to the trash, so
// every run can be undone.
// When the B5 gate reports pages with too little text, the action offers
// "Run with page exemptions…" and reruns only with the list the user
// confirms. Nothing here touches the Stage-2 preview inventory. Free of
// Obsidian imports so it runs under `node --test`; the plugin supplies a
// TextLayerHost.

import { type ConversionController, type PdfSource } from "./conversion-controller.ts";
import { DESKTOP_ONLY_MESSAGE, type OcrEngine, type OcrSettings } from "./ocr-settings.ts";

/** A failed B5 gate, offered to the user as "Run with page exemptions…". */
export interface ExemptionOffer {
	source: PdfSource;
	/** Notice text naming the short pages. */
	message: string;
	shortPages: number[];
	/** Suggested list: the exemptions of this run plus the new short pages. */
	prefill: string;
	/** Reruns with exactly this list; call it only after the user confirmed the list. */
	confirm(pages: string): Promise<void>;
}

/** Everything the action needs from Obsidian. */
export interface TextLayerHost {
	/** False on Obsidian mobile, where no CLI can be spawned. */
	isDesktop: boolean;
	notify(message: string): void;
	settings(): OcrSettings;
	/** Shows the short pages with a way to rerun; nothing reruns unless `offer.confirm` is called. */
	offerExemptions(offer: ExemptionOffer): void;
	/** `reprocess-raw --check-engine`: null when the engine is usable here, otherwise the reason. */
	checkEngine(engine: OcrEngine): Promise<string | null>;
	/** A vault path `<new empty hidden folder>/<fileName>` for `--keep-original`; null if none can be made. */
	originalSlot(fileName: string): Promise<string | null>;
	/** Moves the kept original to the trash; false if it is still at `path`. */
	trashOriginal(path: string): Promise<boolean>;
	/** Removes the slot's folder if it is empty. */
	releaseOriginalSlot(path: string): Promise<void>;
}

const PAGE_LIST = /^\d+(-\d+)?(,\d+(-\d+)?)*$/;

function bounds(part: string): [number, number] {
	const numbers = part.split("-").map(Number);
	const lo = numbers[0] ?? 0;
	return [lo, numbers[1] ?? lo];
}

/**
 * An explicit page list such as "1, 5-7" in the CLI's form ("1,5-7"), or null
 * if it is empty or malformed, names page 0, or has a backwards range.
 */
export function normalizePageList(input: string): string | null {
	const compact = input.replace(/\s+/g, "");
	if (!PAGE_LIST.test(compact)) return null;
	for (const part of compact.split(",")) {
		const [lo, hi] = bounds(part);
		if (lo < 1 || hi < lo) return null;
	}
	return compact;
}

/** The earlier list followed by the short pages it does not already cover. */
export function mergePageLists(existing: string | undefined, pages: number[]): string {
	const parts = existing ? existing.split(",") : [];
	const covered = (page: number) =>
		parts.some((part) => {
			const [lo, hi] = bounds(part);
			return page >= lo && page <= hi;
		});
	const added = [...new Set(pages)]
		.sort((a, b) => a - b)
		.filter((page) => !covered(page))
		.map(String);
	return [...parts, ...added].join(",");
}

function shortPagesMessage(source: PdfSource, pages: number[]): string {
	const list = pages.join(", ");
	const subject = pages.length === 1 ? `page ${list} has` : `pages ${list} have`;
	return `OCR Preview: No text layer added to "${source.basename}" — ${subject} fewer than 50 characters of text. The PDF is unchanged.`;
}

/**
 * The engine this run uses. A stored PaddleOCR is checked first, because the
 * setting can outlive its installation (another Mac, a removed venv): if it
 * is not usable, the run takes Automatic and says so. The other engines are
 * reported by the CLI itself.
 */
async function usableEngine(engine: OcrEngine, host: TextLayerHost): Promise<OcrEngine> {
	if (engine !== "paddle") return engine;
	const problem = await host.checkEngine(engine);
	if (problem === null) return engine;
	host.notify(
		`OCR Preview: This run uses Automatic, because Apple Vision + RapidOCR (Paddle fast) cannot run here — ${problem.replace(/\.$/, "")}. ` +
			"Choose another engine in the settings.",
	);
	return "auto";
}

/**
 * Runs the action for one PDF. `allowPages` is set only by a confirmed
 * exemption rerun; the B5 gate still checks every page not in that list.
 */
export async function runAddTextLayer(
	source: PdfSource,
	controller: Pick<ConversionController, "ensureIdle" | "runOcr">,
	host: TextLayerHost,
	allowPages?: string,
): Promise<void> {
	if (!host.isDesktop) {
		host.notify(DESKTOP_ONLY_MESSAGE);
		return;
	}
	if (!controller.ensureIdle()) return;

	const engine = await usableEngine(host.settings().ocrEngine, host);
	const keepOriginal = await host.originalSlot(source.path.split("/").pop() ?? source.path);
	if (keepOriginal === null) {
		host.notify(
			`OCR Preview: No text layer added to "${source.basename}" — there is no place to keep the original for the trash. The PDF is unchanged.`,
		);
		return;
	}
	try {
		await run(source, controller, host, engine, keepOriginal, allowPages);
	} finally {
		await host.releaseOriginalSlot(keepOriginal);
	}
}

async function run(
	source: PdfSource,
	controller: Pick<ConversionController, "ensureIdle" | "runOcr">,
	host: TextLayerHost,
	engine: OcrEngine,
	keepOriginal: string,
	allowPages: string | undefined,
): Promise<void> {
	const result = await controller.runOcr({
		source,
		engine,
		keepOriginal,
		...(allowPages ? { allowPages } : {}),
	});
	if (result === null) return;
	if (result.code !== 0) {
		// Other failures and cancellation were already reported by the controller.
		if (result.shortPages.length > 0) {
			host.offerExemptions({
				source,
				message: shortPagesMessage(source, result.shortPages),
				shortPages: result.shortPages,
				prefill: mergePageLists(allowPages, result.shortPages),
				confirm: async (pages) => {
					const list = normalizePageList(pages);
					if (list !== null) await runAddTextLayer(source, controller, host, list);
				},
			});
		}
		return;
	}
	host.notify(
		(await host.trashOriginal(keepOriginal))
			? `OCR Preview: Text layer added — ${source.path}. The original is in the trash.`
			: `OCR Preview: Text layer added — ${source.path}. The original could not be moved to the trash; it is at ${keepOriginal}.`,
	);
}
