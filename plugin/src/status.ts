// review-status.json: read, write, reconcile with file system.
//
// CARDINAL RULE: The file system wins. Always.
//
// The three folders are what the user sees and touches in Finder.
// The manifest is a cache with notes on top (`note`, `checked-until`).
// Never move a file to match the JSON — doing so would silently undo a conscious
// manual move, destroying trust in a tool that moves files.
//
// Consequence: the manifest can be deleted at any time. Everything except `note`
// and `checked-until` rebuilds from folder location and frontmatter.
//
// Pure module: no imports from `obsidian`.

import type {
	FolderLocation,
	FoundFile,
	Status,
	StatusEntry,
	StatusManifest,
} from "./types.ts";
import { textFromFrontmatter, numberFromFrontmatter } from "./preview-parser.ts";

export function emptyManifest(now: string): StatusManifest {
	return { version: 1, updated: now, entries: {} };
}

const STATUS_VALUES: readonly Status[] = [
	"open",
	"accepted",
	"rejected",
	"re-created",
	"adopted",
];

function isStatus(val: unknown): val is Status {
	return typeof val === "string" && (STATUS_VALUES as string[]).includes(val);
}

function mapLegacyStatus(val: unknown): Status {
	if (isStatus(val)) return val;
	if (val === "offen") return "open";
	if (val === "akzeptiert") return "accepted";
	if (val === "abgelehnt") return "rejected";
	if (val === "neu-erzeugt") return "re-created";
	if (val === "uebernommen") return "adopted";
	return "open";
}

function textOrNull(val: unknown): string | null {
	return typeof val === "string" && val.length > 0 ? val : null;
}

function numberOrNull(val: unknown): number | null {
	return typeof val === "number" && Number.isFinite(val) ? val : null;
}

/** Reads the manifest defensively: unknown or broken fields are set to safe
 *  values instead of failing the entire run. On a real parse error the function
 *  throws — caller then renames the file to `.corrupted` and rebuilds from disk. */
export function readManifest(text: string, now: string): StatusManifest {
	const raw: unknown = JSON.parse(text);
	if (typeof raw !== "object" || raw === null) return emptyManifest(now);
	const obj = raw as Record<string, unknown>;
	const rawEntries = obj["entries"] ?? obj["eintraege"];
	const entries: Record<string, StatusEntry> = {};
	if (typeof rawEntries === "object" && rawEntries !== null) {
		for (const [name, rawValue] of Object.entries(
			rawEntries as Record<string, unknown>,
		)) {
			if (typeof rawValue !== "object" || rawValue === null) continue;
			const e = rawValue as Record<string, unknown>;
			const rawPrev = e["previous"] ?? e["vorher"];
			const prevObj =
				typeof rawPrev === "object" && rawPrev !== null
					? (rawPrev as Record<string, unknown>)
					: null;
			entries[name] = {
				status: mapLegacyStatus(e["status"]),
				path: textOrNull(e["path"] ?? e["pfad"]) ?? "",
				"source-pdf": textOrNull(e["source-pdf"] ?? e["quelle-pdf"]),
				"manual-source-pdf": textOrNull(e["manual-source-pdf"] ?? e["quelle-pdf-manuell"]),
				pages: numberOrNull(e["pages"] ?? e["seiten"]),
				"pages-ocr": numberOrNull(e["pages-ocr"] ?? e["seiten-ocr"]),
				"pages-diagram": numberOrNull(e["pages-diagram"] ?? e["seiten-diagramm"]),
				"ocr-date": textOrNull(e["ocr-date"] ?? e["ocr-datum"]),
				"ocr-timestamp": textOrNull(e["ocr-timestamp"] ?? e["ocr-zeitpunkt"]),
				decided: textOrNull(e["decided"] ?? e["entschieden"]),
				"checked-until": numberOrNull(e["checked-until"] ?? e["geprueft-bis"]),
				note: textOrNull(e["note"] ?? e["notiz"]),
				previous:
					prevObj !== null
						? {
								status: mapLegacyStatus(prevObj["status"]),
								decided: textOrNull(prevObj["decided"] ?? prevObj["entschieden"]),
								"ocr-date": textOrNull(prevObj["ocr-date"] ?? prevObj["ocr-datum"]),
							}
						: null,
			};
		}
	}
	return {
		version: 1,
		updated: textOrNull(obj["updated"] ?? obj["aktualisiert"]) ?? now,
		entries,
	};
}

export function writeManifest(manifest: StatusManifest): string {
	// Stable key order so JSON diffs remain readable.
	const sorted: Record<string, StatusEntry> = {};
	for (const name of Object.keys(manifest.entries).sort()) {
		const entry = manifest.entries[name];
		if (entry !== undefined) sorted[name] = entry;
	}
	return `${JSON.stringify({ ...manifest, entries: sorted }, null, 2)}\n`;
}

function newEntry(
	file: FoundFile,
	location: FolderLocation,
	now: string,
): StatusEntry {
	const fm = file.frontmatter ?? {};
	return {
		status: location,
		path: file.path,
		"source-pdf": textFromFrontmatter(fm, "source-pdf") ?? textFromFrontmatter(fm, "quelle-pdf"),
		"manual-source-pdf": null,
		pages: numberFromFrontmatter(fm, "pages") ?? numberFromFrontmatter(fm, "seiten"),
		"pages-ocr": numberFromFrontmatter(fm, "pages-ocr") ?? numberFromFrontmatter(fm, "seiten-ocr"),
		"pages-diagram": numberFromFrontmatter(fm, "pages-diagram") ?? numberFromFrontmatter(fm, "seiten-diagramm"),
		"ocr-date": textFromFrontmatter(fm, "ocr-date") ?? textFromFrontmatter(fm, "ocr-datum"),
		"ocr-timestamp": textFromFrontmatter(fm, "ocr-timestamp") ?? textFromFrontmatter(fm, "ocr-zeitpunkt"),
		// An entry created by reconciliation has no tool decision behind it —
		// even if the file already resides in _accepted/. Do not invent timestamp.
		decided: null,
		"checked-until": null,
		note: null,
		previous: null,
	};
}

/** `ocr-date` of OLD version upon replacement (Rule 6): first frontmatter of the
 *  file itself — its truth —, then memory of state prior to re-conversion.
 *  Re-conversion entry carries NEW date; that would be wrong filename. */
export function oldDateFrom(
	entry: StatusEntry | undefined,
	frontmatter: Record<string, unknown>,
): string {
	return (
		textFromFrontmatter(frontmatter, "ocr-date") ??
		textFromFrontmatter(frontmatter, "ocr-datum") ??
		entry?.previous?.["ocr-date"] ??
		"old"
	);
}

export interface ReconciliationResult {
	manifest: StatusManifest;
	/** Names whose status was corrected from folder location (Rule 2). */
	corrected: string[];
	/** Names detected as re-conversions (Rule 6). */
	reCreated: string[];
	/** Names whose cache entry was discarded (Rule 4). */
	removed: string[];
}

/**
 * Reconciles manifest and file system.
 *
 * @param files             All .md from the three folders. Caller must filter
 *                          by EXACT parent path, not `startsWith` — `_accepted`
 *                          is inside `_ocr-preview`; prefix test lists accepted files as open.
 * @param existsInVault     Checks whether a path outside three folders is in vault.
 *                          Rule 4 distinguishes "adopted into wiki" from "deleted".
 */
export function reconcile(
	files: readonly FoundFile[],
	previous: StatusManifest,
	now: string,
	existsInVault: (path: string) => boolean = () => false,
): ReconciliationResult {
	const corrected: string[] = [];
	const reCreated: string[] = [];
	const removed: string[] = [];

	// Group by basename: same name can exist in two folders (re-conversion case, Rule 5/6).
	const byName = new Map<string, FoundFile[]>();
	for (const file of files) {
		const list = byName.get(file.name);
		if (list === undefined) byName.set(file.name, [file]);
		else list.push(file);
	}

	const entries: Record<string, StatusEntry> = {};

	for (const [name, found] of byName) {
		// File in open folder is always freshest: pdf2md.py main() output writes
		// exclusively to <out>/<stem>.md and does not know subfolders.
		const openFile = found.find((d) => d.location === "open");
		const decidedFile = found.find((d) => d.location !== "open");
		const primary = openFile ?? decidedFile;
		if (primary === undefined) continue;

		const oldEntry = previous.entries[name];
		if (oldEntry === undefined) {
			// Rule 3 — File without entry.
			entries[name] = newEntry(primary, primary.location, now);
			continue;
		}

		const fm = primary.frontmatter ?? {};
		// Comparison attribute for re-conversion: prefers fine-grained `ocr-timestamp`,
		// fallback to `ocr-date`.
		const dateNow =
			textFromFrontmatter(fm, "ocr-timestamp") ??
			textFromFrontmatter(fm, "ocr-zeitpunkt") ??
			textFromFrontmatter(fm, "ocr-date") ??
			textFromFrontmatter(fm, "ocr-datum");
		const oldDate = oldEntry["ocr-timestamp"] ?? oldEntry["ocr-date"];
		const entry: StatusEntry = {
			...oldEntry,
			path: primary.path,
			"source-pdf": textFromFrontmatter(fm, "source-pdf") ?? textFromFrontmatter(fm, "quelle-pdf") ?? oldEntry["source-pdf"],
			pages: numberFromFrontmatter(fm, "pages") ?? numberFromFrontmatter(fm, "seiten") ?? oldEntry.pages,
			"pages-ocr": numberFromFrontmatter(fm, "pages-ocr") ?? numberFromFrontmatter(fm, "seiten-ocr") ?? oldEntry["pages-ocr"],
			"pages-diagram":
				numberFromFrontmatter(fm, "pages-diagram") ?? numberFromFrontmatter(fm, "seiten-diagramm") ?? oldEntry["pages-diagram"],
			"ocr-date": textFromFrontmatter(fm, "ocr-date") ?? textFromFrontmatter(fm, "ocr-datum") ?? oldEntry["ocr-date"],
			"ocr-timestamp":
				textFromFrontmatter(fm, "ocr-timestamp") ?? textFromFrontmatter(fm, "ocr-zeitpunkt") ?? oldEntry["ocr-timestamp"],
		};

		const wasDecided =
			oldEntry.status === "accepted" || oldEntry.status === "rejected";

		// Rule 5/6 — Re-conversion of previously decided file.
		// Two signals:
		//   (a) same name present open AND decided simultaneously,
		//   (b) ocr-date/ocr-timestamp of open file differs from recorded.
		const twoVersions = openFile !== undefined && decidedFile !== undefined;
		const differentDate =
			openFile !== undefined &&
			dateNow !== null &&
			oldDate !== null &&
			dateNow !== oldDate;

		if (wasDecided && (twoVersions || differentDate)) {
			entry.status = "re-created";
			entry.decided = null;
			// New version = new document: review position and note of OLD version belong to past.
			entry["checked-until"] = null;
			entry.note = null;
			entry.previous = {
				status: oldEntry.status,
				decided: oldEntry.decided,
				"ocr-date": oldEntry["ocr-date"],
			};
			reCreated.push(name);
			entries[name] = entry;
			continue;
		}

		// Re-conversion of an open file: same signal (b) as Rule 5/6, without prior decision.
		if (differentDate) {
			entry.status = "re-created";
			entry["checked-until"] = null;
			entry.note = null;
			reCreated.push(name);
			entries[name] = entry;
			continue;
		}

		// Rule 2 — Folder location differs from status: folder location wins.
		// `re-created` in open folder is not a contradiction, but unacknowledged state.
		const matches =
			oldEntry.status === primary.location ||
			(oldEntry.status === "re-created" && primary.location === "open");
		if (!matches) {
			entry.status = primary.location;
			if (primary.location === "open") entry.decided = null;
			corrected.push(name);
		}
		entries[name] = entry;
	}

	// Rule 4 — Entry without file in the three folders.
	for (const [name, oldEntry] of Object.entries(previous.entries)) {
		if (byName.has(name)) continue;
		if (oldEntry.path.length > 0 && existsInVault(oldEntry.path)) {
			// Located elsewhere in vault — adopted into wiki. Keep entry as memory.
			entries[name] = { ...oldEntry, status: "adopted" };
		} else {
			removed.push(name);
		}
	}

	return {
		manifest: { version: 1, updated: now, entries },
		corrected,
		reCreated,
		removed,
	};
}

/** Records a decision. Moving file is handled by caller — function is agnostic to disk. */
export function recordDecision(
	manifest: StatusManifest,
	name: string,
	status: FolderLocation,
	newPath: string,
	now: string,
): StatusManifest {
	const oldEntry = manifest.entries[name];
	if (oldEntry === undefined) return manifest;
	return {
		...manifest,
		updated: now,
		entries: {
			...manifest.entries,
			[name]: {
				...oldEntry,
				status,
				path: newPath,
				decided: status === "open" ? null : now,
				previous: null,
			},
		},
	};
}

/** FolderLocation → target folder. Single source of truth. */
export function targetFolder(
	location: FolderLocation,
	settings: {
		previewFolder: string;
		acceptedFolder: string;
		rejectedFolder: string;
	},
): string {
	if (location === "accepted") return settings.acceptedFolder;
	if (location === "rejected") return settings.rejectedFolder;
	return settings.previewFolder;
}
