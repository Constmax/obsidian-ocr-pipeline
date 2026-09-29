// Stage-2 settings for "Convert with pdf2md": which pdf2md runs, and the
// `--dpi` / `--tile-from` values passed to it (issue #28). Type, defaults,
// validation of loaded plugin data and of typed values. Free of Obsidian
// imports so it runs under `node --test`.

import { accessSync, constants, statSync } from "fs";
import { homedir } from "os";
import { isAbsolute, join } from "path";

import { resolvePdf2md } from "./conversion-controller.ts";

export interface Pdf2mdSettings {
	/** Executable to run; empty searches ~/bin, /usr/local/bin and PATH. */
	pdf2mdPath: string;
	/** `--dpi`; null leaves pdf2md's default (150). */
	pdf2mdDpi: number | null;
	/** `--tile-from`; null leaves pdf2md's default (3000). */
	pdf2mdTileFrom: number | null;
}

export const DEFAULT_PDF2MD_SETTINGS: Pdf2mdSettings = {
	pdf2mdPath: "",
	pdf2mdDpi: null,
	pdf2mdTileFrom: null,
};

/** pdf2md's own defaults, shown as placeholders; pinned by a pdf2md test. */
export const PDF2MD_DEFAULTS = { dpi: 150, tileFrom: 3000 } as const;

/** Render resolution pdf2md accepts from the settings. */
export const DPI_RANGE = { min: 72, max: 600 } as const;

export type Parsed = { value: number | null } | { error: string };

/** A typed DPI value: empty means pdf2md's default. */
export function parseDpi(text: string): Parsed {
	const trimmed = text.trim();
	if (trimmed.length === 0) return { value: null };
	const n = Number(trimmed);
	if (!Number.isInteger(n) || n < DPI_RANGE.min || n > DPI_RANGE.max) {
		return { error: `DPI must be a whole number from ${DPI_RANGE.min} to ${DPI_RANGE.max}.` };
	}
	return { value: n };
}

/** A typed `--tile-from` value: empty means pdf2md's default; 0 tiles every OCR page. */
export function parseTileFrom(text: string): Parsed {
	const trimmed = text.trim();
	if (trimmed.length === 0) return { value: null };
	const n = Number(trimmed);
	if (!Number.isInteger(n) || n < 0) {
		return { error: "Tile threshold must be a whole number of characters (0 or more)." };
	}
	return { value: n };
}

/** `~/…` expanded; everything else unchanged. */
export function expandHome(path: string, home: string = homedir()): string {
	if (path === "~") return home;
	return path.startsWith("~/") ? join(home, path.slice(2)) : path;
}

export interface FileProbe {
	isFile(path: string): boolean | null;
	isExecutable(path: string): boolean;
}

const diskProbe: FileProbe = {
	isFile(path) {
		try {
			return statSync(path).isFile();
		} catch {
			return null;
		}
	},
	isExecutable(path) {
		try {
			accessSync(path, constants.X_OK);
			return true;
		} catch {
			return false;
		}
	},
};

/**
 * Why `path` cannot run as pdf2md, or null when it can. Checked when the
 * setting is saved, so a typo shows up there and not at the next conversion.
 */
export function pdf2mdPathProblem(
	path: string,
	home: string = homedir(),
	probe: FileProbe = diskProbe,
): string | null {
	const expanded = expandHome(path.trim(), home);
	if (!isAbsolute(expanded)) return "Enter an absolute path (or ~/…).";
	const isFile = probe.isFile(expanded);
	if (isFile === null) return `Not found: ${expanded}`;
	if (!isFile) return `Not a file: ${expanded}`;
	if (!probe.isExecutable(expanded)) return `Not executable: ${expanded}`;
	return null;
}

/** The pdf2md a conversion runs: the configured path, else the PATH search. */
export function pdf2mdExecutable(settings: Pdf2mdSettings, home: string = homedir()): string {
	const configured = settings.pdf2mdPath.trim();
	return configured.length > 0 ? expandHome(configured, home) : resolvePdf2md(undefined, home);
}

/**
 * pdf2md settings from saved plugin data. Missing and invalid values fall back
 * to the defaults field by field, like parseOcrSettings.
 */
export function parsePdf2mdSettings(saved: unknown): Pdf2mdSettings {
	const data =
		typeof saved === "object" && saved !== null ? (saved as Record<string, unknown>) : {};
	const number = (value: unknown, parse: (text: string) => Parsed): number | null => {
		if (typeof value !== "number") return null;
		const parsed = parse(String(value));
		return "value" in parsed ? parsed.value : null;
	};
	return {
		pdf2mdPath: typeof data.pdf2mdPath === "string" ? data.pdf2mdPath.trim() : "",
		pdf2mdDpi: number(data.pdf2mdDpi, parseDpi),
		pdf2mdTileFrom: number(data.pdf2mdTileFrom, parseTileFrom),
	};
}
