// Carry-over from the plugin's German-named install (Issue #104).
//
// Before the English rename (f220066) the plugin id was `ocr-vorschau` and its
// folders were `_ocr-vorschau/_akzeptiert|_abgelehnt`. Obsidian keeps plugin
// data per id, so the renamed plugin starts without data.json and its new
// default folders would hide every existing preview. This decides what a fresh
// install starts from instead.
//
// Pure module: no imports from `obsidian`, so it runs under `node --test`.

/** Plugin id before the rename; its data.json lives in `plugins/<id>/`. */
export const LEGACY_PLUGIN_ID = "ocr-vorschau";

/** Folder settings of the pre-rename defaults. */
export const LEGACY_FOLDERS = {
	previewFolder: "_ocr-vorschau",
	acceptedFolder: "_ocr-vorschau/_akzeptiert",
	rejectedFolder: "_ocr-vorschau/_abgelehnt",
	statusFile: "_ocr-vorschau/review-status.json",
} as const;

export interface FolderPresence {
	/** `_ocr-vorschau` exists in the vault. */
	legacyFolder: boolean;
	/** The current default preview folder exists in the vault. */
	currentFolder: boolean;
}

/**
 * Saved data to start from when this install has none of its own, or null
 * for the defaults. Data saved under the legacy id wins: it holds the user's
 * own choices, and loadSettings already migrates its keys. Without it, a vault
 * that still keeps its previews in `_ocr-vorschau` — and has not started using
 * the new folder — keeps the legacy folders.
 */
export function legacyStart(
	legacyData: unknown,
	folders: FolderPresence,
): Record<string, unknown> | null {
	if (typeof legacyData === "object" && legacyData !== null && !Array.isArray(legacyData)) {
		return legacyData as Record<string, unknown>;
	}
	if (folders.legacyFolder && !folders.currentFolder) return { ...LEGACY_FOLDERS };
	return null;
}
