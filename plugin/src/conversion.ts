// Child processes of the pipeline. Stage 2: calls the local pdf2md script with
// `--out` and collects the last output lines; machine-readable progress is
// available via the `--fortschritt` flag. Stage 1: calls `reprocess-raw
// --output` in its own process group, so cancellation reaches OCRmyPDF and
// every other descendant.

import { spawn, type ChildProcess } from "child_process";
import { homedir } from "os";
import { join } from "path";

export interface ConversionResult {
	/** Exit code of child process; `null` means not terminated via code (start error, signal, timeout). */
	code: number | null;
	/** Signal if process ended due to signal. */
	signal: NodeJS.Signals | null;
	/** true if the process was stopped after `idleTimeoutMs` without output. */
	timeout: boolean;
	/** Last non-empty stdout lines (at most 5). */
	stdoutLast: string[];
	/** Last non-empty stderr lines (at most 5). */
	stderrLast: string[];
}

export type ProgressEvent =
	| { type: "start"; file: string; pages: number; dpi: number }
	| {
			type: "page";
			num: number;
			total: number;
			seconds: number;
			origin: string;
			derailed: boolean;
			reason?: string;
	  }
	| { type: "finished"; target: string; seconds: number; derailed: number };

export interface ConversionOptions {
	/** Stops the run once it has written nothing for this long. */
	idleTimeoutMs?: number;
	gracePeriodMs?: number;
	killDelayMs?: number;
	pages?: string;
	onChild?: (child: ChildProcess) => void;
	onProgress?: (event: ProgressEvent) => void;
}

// One definition of the offered engines, shared with the settings.
import type { OcrEngine } from "./ocr-settings.ts";
export type { OcrEngine };

export interface SearchableCopyOptions {
	/** Omitted: the CLI default (`auto`). */
	engine?: OcrEngine;
	splitColumns?: boolean;
	/** Pages exempt from the B5 gate, e.g. "1,5-7". */
	allowPages?: string;
	onChild?: (child: ChildProcess) => void;
}

const LAST_LINES = 5;
/**
 * Stage 2: time the current page gets to finish after the first SIGTERM. A
 * page takes 15–60 s, so most cancels end the page early with the second
 * SIGTERM; a textlayer page or a nearly finished one still makes it.
 */
export const ABORT_GRACE_PERIOD_MS = 15_000;
/** Stage 2: time pdf2md gets to write the partial file after the second SIGTERM. */
export const ABORT_KILL_DELAY_MS = 5000;
/** Stage 1: time the process group gets after SIGTERM before SIGKILL. */
export const GROUP_GRACE_PERIOD_MS = 5000;
/** How often terminateProcessGroup checks whether the group is gone. */
export const GROUP_POLL_MS = 100;

function safeSetTimeout(fn: () => void, ms?: number): number | ReturnType<typeof setTimeout> {
	if (typeof window !== "undefined") {
		return window.setTimeout(fn, ms);
	}
	return setTimeout(fn, ms);
}

function safeClearTimeout(timer: number | ReturnType<typeof setTimeout> | null): void {
	if (timer === null) return;
	if (typeof window !== "undefined") {
		window.clearTimeout(timer as number);
	} else {
		clearTimeout(timer as ReturnType<typeof setTimeout>);
	}
}

/**
 * Stops a pdf2md child in three steps (Issue #105). The first SIGTERM lets the
 * current page finish. After `gracePeriodMs` a second SIGTERM stops that page,
 * and pdf2md writes the partial file from the pages before it (exit 6, or 7
 * without any). Only a child still alive `killDelayMs` later gets SIGKILL,
 * which loses the partial file.
 */
export function abortChild(
	child: ChildProcess,
	gracePeriodMs: number = ABORT_GRACE_PERIOD_MS,
	killDelayMs: number = ABORT_KILL_DELAY_MS,
): void {
	const alive = () => child.exitCode === null && child.signalCode === null;
	if (!alive()) return;
	if (!child.kill("SIGTERM")) return;
	let timer = safeSetTimeout(() => {
		// The exit listener below cannot clear a timer armed after the exit.
		if (!child.kill("SIGTERM") || !alive()) return;
		timer = safeSetTimeout(() => {
			child.kill("SIGKILL");
		}, killDelayMs);
	}, gracePeriodMs);
	child.once("exit", () => safeClearTimeout(timer));
}

export type SignalFunction = (pid: number, signal: NodeJS.Signals | 0) => void;

/**
 * Stops a child spawned with `detached: true` together with every process in
 * its group. Sends SIGTERM to the group, then SIGKILL once `gracePeriodMs` has
 * passed and a member remains. Resolves when the group is gone or SIGKILL was
 * sent. A group that is already gone counts as cleaned up, not as an error.
 *
 * The leader exiting says nothing about its descendants, so the group is
 * probed with signal 0 instead of watching the child's exit event. Any error
 * (ESRCH: gone; EPERM: the id now belongs to someone else) ends the cleanup.
 */
export function terminateProcessGroup(
	child: ChildProcess,
	gracePeriodMs: number = GROUP_GRACE_PERIOD_MS,
	signal: SignalFunction = (pid, sig) => process.kill(pid, sig),
): Promise<void> {
	return new Promise((done) => {
		const pgid = child.pid;
		if (pgid === undefined) {
			done();
			return;
		}
		const send = (sig: NodeJS.Signals | 0): boolean => {
			try {
				signal(-pgid, sig);
				return true;
			} catch {
				return false;
			}
		};
		if (!send("SIGTERM")) {
			done();
			return;
		}
		const deadline = Date.now() + gracePeriodMs;
		const poll = () => {
			if (!send(0)) {
				done();
				return;
			}
			const remaining = deadline - Date.now();
			if (remaining <= 0) {
				send("SIGKILL");
				done();
				return;
			}
			safeSetTimeout(poll, Math.min(GROUP_POLL_MS, remaining));
		};
		safeSetTimeout(poll, Math.min(GROUP_POLL_MS, gracePeriodMs));
	});
}

export type SpawnFunction = (
	command: string,
	args: readonly string[],
	options?: object,
) => ChildProcess;

function collect(last: string[], line: string): void {
	const cleaned = line.trim();
	if (cleaned.length === 0) return;
	last.push(cleaned);
	if (last.length > LAST_LINES) last.shift();
}

/**
 * Version of pdf2md's `--fortschritt` protocol this parser reads (Issue #55).
 * Keys, types and the version are fixed in contracts/cli-contract.json, which
 * pdf2md's tests read too; docs/cli-contract.md explains the rules.
 */
export const PROGRESS_PROTOCOL = 1;

/**
 * Parses one stderr line of `pdf2md --fortschritt`, or null when the line is
 * no progress event of a protocol this plugin reads. Unknown fields are
 * ignored; a missing or mistyped required field rejects the event. An event
 * without `protokoll` comes from a pdf2md older than the field and is read as
 * version 1, which is what those versions emitted.
 */
export function parseProgressEvent(line: string): ProgressEvent | null {
	let obj: unknown;
	try {
		obj = JSON.parse(line) as unknown;
	} catch {
		return null;
	}
	if (typeof obj !== "object" || obj === null || Array.isArray(obj)) return null;
	const e = obj as Record<string, unknown>;
	if (e.protokoll !== undefined && e.protokoll !== PROGRESS_PROTOCOL) return null;
	switch (e.typ) {
		case "start": {
			const file = textVal(e.datei);
			const pages = intVal(e.seiten);
			const dpi = intVal(e.dpi);
			if (file === null || pages === null || dpi === null) return null;
			return { type: "start", file, pages, dpi };
		}
		case "seite": {
			const num = intVal(e.nr);
			const total = intVal(e.von);
			const seconds = numVal(e.sekunden);
			const origin = textVal(e.herkunft);
			if (num === null || total === null || seconds === null || origin === null) return null;
			if (typeof e.entgleist !== "boolean") return null;
			if (e.grund !== undefined && typeof e.grund !== "string") return null;
			const reason = textVal(e.grund);
			return {
				type: "page",
				num,
				total,
				seconds,
				origin,
				derailed: e.entgleist,
				...(reason !== null ? { reason } : {}),
			};
		}
		case "fertig": {
			const target = textVal(e.ziel);
			const seconds = numVal(e.sekunden);
			const derailed = intVal(e.entgleist);
			if (target === null || seconds === null || derailed === null) return null;
			return { type: "finished", target, seconds, derailed };
		}
		default:
			return null;
	}
}

function textVal(v: unknown): string | null {
	return typeof v === "string" && v.length > 0 ? v : null;
}

function numVal(v: unknown): number | null {
	return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function intVal(v: unknown): number | null {
	return typeof v === "number" && Number.isInteger(v) ? v : null;
}

function lineBuffer(last: string[], onLine?: (line: string) => boolean): { write: (chunk: string) => void; flush: () => void } {
	let rest = "";
	return {
		write(chunk: string): void {
			const parts = (rest + chunk).split("\n");
			rest = parts.pop() ?? "";
			for (const line of parts) {
				if (!onLine || onLine(line)) collect(last, line);
			}
		},
		flush(): void {
			if (rest.length > 0) {
				if (!onLine || onLine(rest)) collect(last, rest);
			}
			rest = "";
		},
	};
}

interface RunOptions {
	/** Restarted by every chunk of output, so only a silent child times out. */
	idleTimeoutMs?: number;
	/** Stops the child when `idleTimeoutMs` has passed without output. */
	onTimeout?: (child: ChildProcess) => void;
	onChild?: (child: ChildProcess) => void;
	/** Sees every stderr line; returning false keeps it out of `stderrLast`. */
	onStderrLine?: (line: string) => boolean;
}

/** Spawns one CLI call and collects its result; shared by both stages. */
function runProcess(
	command: string,
	args: string[],
	spawnOptions: object,
	spawnFn: SpawnFunction,
	options: RunOptions,
): Promise<ConversionResult> {
	return new Promise((resolve) => {
		let child: ChildProcess;
		try {
			child = spawnFn(command, args, spawnOptions);
		} catch (err) {
			resolve({
				code: null,
				signal: null,
				timeout: false,
				stdoutLast: [],
				stderrLast: [String(err)],
			});
			return;
		}
		options.onChild?.(child);
		const stdoutLast: string[] = [];
		const stderrLast: string[] = [];
		const stdoutBuf = lineBuffer(stdoutLast);
		const stderrBuf = lineBuffer(stderrLast, options.onStderrLine);

		// A fixed limit killed long documents that were still converting
		// (Issue #105); only silence means the child hangs. Page lines, the
		// model load and analysis output all count as a sign of life.
		let isTimeout = false;
		let finished = false;
		let timer: number | ReturnType<typeof setTimeout> | null = null;
		const armIdleTimer = () => {
			if (options.idleTimeoutMs === undefined || isTimeout || finished) return;
			safeClearTimeout(timer);
			timer = safeSetTimeout(() => {
				isTimeout = true;
				options.onTimeout?.(child);
			}, options.idleTimeoutMs);
		};
		armIdleTimer();
		child.stdout?.setEncoding("utf8");
		child.stderr?.setEncoding("utf8");
		child.stdout?.on("data", (chunk) => {
			armIdleTimer();
			stdoutBuf.write(String(chunk));
		});
		child.stderr?.on("data", (chunk) => {
			armIdleTimer();
			stderrBuf.write(String(chunk));
		});
		const done = (result: ConversionResult) => {
			finished = true;
			safeClearTimeout(timer);
			resolve(result);
		};
		child.on("error", (err) => {
			stdoutBuf.flush();
			stderrBuf.flush();
			collect(stderrLast, String(err));
			done({
				code: null,
				signal: null,
				timeout: isTimeout,
				stdoutLast,
				stderrLast,
			});
		});
		child.on("close", (code, signal) => {
			stdoutBuf.flush();
			stderrBuf.flush();
			done({
				code,
				signal: signal ?? null,
				timeout: isTimeout,
				stdoutLast,
				stderrLast,
			});
		});
	});
}

export function convertPdf(
	pdf: string,
	out: string,
	pdf2md: string,
	cwd: string,
	spawnFn: SpawnFunction = spawn,
	options: ConversionOptions = {},
): Promise<ConversionResult> {
	const args = [pdf, "--out", out];
	if (options.pages && options.pages.length > 0) {
		args.push("--seiten", options.pages);
	}
	args.push("--fortschritt");
	return runProcess(pdf2md, args, { cwd, stdio: ["ignore", "pipe", "pipe"] }, spawnFn, {
		...(options.idleTimeoutMs === undefined ? {} : { idleTimeoutMs: options.idleTimeoutMs }),
		onTimeout: (child) => abortChild(child, options.gracePeriodMs, options.killDelayMs),
		...(options.onChild ? { onChild: options.onChild } : {}),
		onStderrLine: (line) => {
			const event = parseProgressEvent(line);
			if (event && options.onProgress) {
				options.onProgress(event);
				return false;
			}
			return true;
		},
	});
}

const TOOL_DIRS = (home: string) => [join(home, "bin"), "/opt/homebrew/bin", "/usr/local/bin"];
const SYSTEM_DIRS = ["/usr/bin", "/bin", "/usr/sbin", "/sbin"];

/**
 * PATH for Stage-1 CLIs. Obsidian started from the Dock inherits launchd's
 * minimal PATH, but reprocess-raw needs ~/bin (the ocrmypdf symlink from
 * setup.sh) and Homebrew's qpdf, gs, and Poppler. Missing tool folders go
 * first, missing system folders last; existing entries keep their order.
 */
export function stage1Path(path: string, home: string = homedir()): string {
	const parts = path.split(":").filter((part) => part.length > 0);
	const missing = (dirs: string[]) => dirs.filter((dir) => !parts.includes(dir));
	return [...missing(TOOL_DIRS(home)), ...parts, ...missing(SYSTEM_DIRS)].join(":");
}

/**
 * Stage 1: `reprocess-raw <source> --output <destination> [options]` with
 * `cwd` as working directory, in a new process group (`detached: true`) so
 * terminateProcessGroup reaches every descendant. No timeout in the first
 * release: OCR time grows with page count, and the user can cancel.
 */
export function createSearchableCopy(
	source: string,
	destination: string,
	cli: string,
	cwd: string,
	spawnFn: SpawnFunction = spawn,
	options: SearchableCopyOptions = {},
): Promise<SearchableCopyResult> {
	const args = [source, "--output", destination];
	if (options.engine !== undefined) args.push("--engine", options.engine);
	if (options.splitColumns) args.push("--split-columns");
	if (options.allowPages && options.allowPages.length > 0) {
		args.push("--allow-pages", options.allowPages);
	}
	const spawnOptions = {
		cwd,
		stdio: ["ignore", "pipe", "pipe"],
		detached: true,
		env: { ...process.env, PATH: stage1Path(process.env.PATH ?? "") },
	};
	const shortPages = new Set<number>();
	return runProcess(cli, args, spawnOptions, spawnFn, {
		...(options.onChild ? { onChild: options.onChild } : {}),
		// Every B5 line counts, not only the last few kept in stderrLast.
		onStderrLine: (line) => {
			const match = SHORT_PAGE_LINE.exec(line);
			if (match) shortPages.add(Number(match[1]));
			return true;
		},
	}).then((result) => ({ ...result, shortPages: [...shortPages].sort((a, b) => a - b) }));
}

/** A Stage-1 result with the pages the B5 gate reported as too short. */
export interface SearchableCopyResult extends ConversionResult {
	/** Ascending page numbers; empty unless the B5 gate failed. */
	shortPages: number[];
}

/**
 * column_tools.py verify-pages: "🗑️  Page 3: only 5 characters (min: 50)".
 * Stage 1 has no structured channel; the exact line is pinned in
 * contracts/cli-contract.json and checked against the real script (Issue #55).
 */
export const SHORT_PAGE_LINE = /\bPage (\d+): only \d+ characters\b/;
