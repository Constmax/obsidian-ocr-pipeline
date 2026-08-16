// Shared types. Intentionally free of `obsidian` imports so that pure
// modules (preview-parser, status) remain testable without a running app.

/** Origin of page text. `undefined` means: file originates from
 *  a pdf2md run prior to the marker extension — then NO badge is shown.
 *  Never default to `textlayer`: a false badge is worse than no
 *  badge because it entices skipping an OCR page. */
export type Origin = "textlayer" | "ocr" | "diagram";

export interface PageBlock {
	/** Page number from the marker, 1-based as in the PDF. */
	pageNumber: number;
	origin?: Origin;
	/** Free text following the origin, e.g. "two-column, vertical @48%". */
	layout?: string;
	/** Original marker line from the file, e.g. "%% p. 1 | textlayer %%". */
	markerLine?: string;
	/** Block content without the marker, without leading/trailing blank lines. */
	markdown: string;
}

export interface Preview {
	frontmatter: Record<string, string>;
	/** Value of `source-pdf` or `quelle-pdf` from frontmatter, otherwise the `Source: [[…]]` link. */
	sourcePdf: string | null;
	/** Text before the first page marker (excluding frontmatter and `Source:` line). Normally empty. */
	preamble: string;
	/** The entire header area before the first page marker in its original state. */
	rawPreamble?: string;
	blocks: PageBlock[];
}

export type Status =
	| "open"
	| "accepted"
	| "rejected"
	| "re-created"
	| "adopted";

/** An entry in review-status.json. Key is the basename of the .md file. */
export interface StatusEntry {
	status: Status;
	/** Last seen path. Diagnostic only — folder location decides. */
	path: string;
	"source-pdf": string | null;
	"manual-source-pdf": string | null;
	pages: number | null;
	"pages-ocr": number | null;
	"pages-diagram": number | null;
	"ocr-date": string | null;
	/** Fine-grained creation timestamp (ISO with time) — distinguishes
	 *  re-conversions on the same day that `ocr-date` (date only) does not
	 *  see. Null for files from runs before this field was introduced. */
	"ocr-timestamp": string | null;
	/** ISO timestamp of the decision, null while open. */
	decided: string | null;
	/** Last inspected page — so a 40-page review session can be resumed. */
	"checked-until": number | null;
	note: string | null;
	/** For `re-created`: the previous decision so it does not disappear. */
	previous: {
		status: Status;
		decided: string | null;
		"ocr-date": string | null;
	} | null;
}

export interface StatusManifest {
	version: 1;
	updated: string;
	entries: Record<string, StatusEntry>;
}

/** Which of the three folders a file currently belongs to. */
export type FolderLocation = "open" | "accepted" | "rejected";

/** What reconciliation requires from the file system — intentionally no TFile,
 *  so reconciliation logic remains testable without Obsidian. */
export interface FoundFile {
	name: string;
	path: string;
	location: FolderLocation;
	/** Frontmatter values if available (from metadata cache). */
	frontmatter?: Record<string, unknown>;
}
