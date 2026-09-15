// "Create searchable copy (OCR)": the Stage-1 action behind the PDF file menu,
// the command, and the comparison view. Proposes `<stem>-ocr.pdf` beside the
// source, refuses an existing destination before anything is spawned, runs
// `reprocess-raw --output` through the ConversionController with the saved OCR
// settings, and opens the new PDF. When the B5 gate reports pages with too
// little text, it offers "Run with page exemptions…" and reruns only with the
// list the user confirms. The source is never written, and nothing here
// touches the Stage-2 preview inventory. Free of Obsidian imports so it runs
// under `node --test`; the plugin supplies a SearchableCopyHost.

import {
	INDEX_WAIT_MS,
	INDEX_WAIT_STEPS,
	type ConversionController,
	type PdfSource,
} from "./conversion-controller.ts";
import { DESKTOP_ONLY_MESSAGE, type OcrSettings } from "./ocr-settings.ts";

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
export interface SearchableCopyHost {
	/** False on Obsidian mobile, where no CLI can be spawned. */
	isDesktop: boolean;
	notify(message: string): void;
	/** True if a file or folder exists at the vault path, indexed or not. */
	exists(path: string): Promise<boolean>;
	settings(): OcrSettings;
	/** Opens the PDF at the vault path; false while the vault has not indexed it. */
	openPdf(path: string): Promise<boolean>;
	wait(ms: number): Promise<void>;
	/** Shows the short pages with a way to rerun; nothing reruns unless `offer.confirm` is called. */
	offerExemptions(offer: ExemptionOffer): void;
}

/** `raw/case.pdf` → `raw/case-ocr.pdf`, in the source's own folder. */
export function searchableCopyPath(sourcePath: string): string {
	return `${sourcePath.replace(/\.pdf$/i, "")}-ocr.pdf`;
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
	return `OCR Preview: No searchable copy of "${source.basename}" — ${subject} fewer than 50 characters of text. Nothing was written.`;
}

/**
 * Runs the action for one PDF. `allowPages` is set only by a confirmed
 * exemption rerun; the B5 gate still checks every page not in that list.
 */
export async function runSearchableCopy(
	source: PdfSource,
	controller: Pick<ConversionController, "ensureIdle" | "runOcr">,
	host: SearchableCopyHost,
	allowPages?: string,
): Promise<void> {
	if (!host.isDesktop) {
		host.notify(DESKTOP_ONLY_MESSAGE);
		return;
	}
	if (!controller.ensureIdle()) return;

	const destination = searchableCopyPath(source.path);
	if (await host.exists(destination)) {
		host.notify(
			`OCR Preview: ${destination} already exists — no searchable copy was started. Rename or move it first.`,
		);
		return;
	}

	const { ocrEngine, splitColumns } = host.settings();
	const result = await controller.runOcr({
		source,
		destination,
		engine: ocrEngine,
		splitColumns,
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
					if (list !== null) await runSearchableCopy(source, controller, host, list);
				},
			});
		}
		return;
	}

	for (let step = 0; step <= INDEX_WAIT_STEPS; step++) {
		if (step > 0) await host.wait(INDEX_WAIT_MS);
		if (await host.openPdf(destination)) {
			host.notify(`OCR Preview: Searchable copy created — ${destination}.`);
			return;
		}
	}
	host.notify(
		`OCR Preview: Searchable copy created at ${destination}, but Obsidian has not listed it yet.`,
	);
}
