// "Create searchable copy (OCR)": the Stage-1 action behind the PDF file menu,
// the command, and the comparison view. Proposes `<stem>-ocr.pdf` beside the
// source, refuses an existing destination before anything is spawned, runs
// `reprocess-raw --output` through the ConversionController with the saved OCR
// settings, and opens the new PDF. The source is never written, and nothing
// here touches the Stage-2 preview inventory. Free of Obsidian imports so it
// runs under `node --test`; the plugin supplies a SearchableCopyHost.

import {
	INDEX_WAIT_MS,
	INDEX_WAIT_STEPS,
	type ConversionController,
	type PdfSource,
} from "./conversion-controller.ts";
import { DESKTOP_ONLY_MESSAGE, type OcrSettings } from "./ocr-settings.ts";

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
}

/** `raw/case.pdf` → `raw/case-ocr.pdf`, in the source's own folder. */
export function searchableCopyPath(sourcePath: string): string {
	return `${sourcePath.replace(/\.pdf$/i, "")}-ocr.pdf`;
}

export async function runSearchableCopy(
	source: PdfSource,
	controller: Pick<ConversionController, "ensureIdle" | "runOcr">,
	host: SearchableCopyHost,
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
	});
	// null: nothing ran. Non-zero: the controller already reported failure or cancellation.
	if (result === null || result.code !== 0) return;

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
