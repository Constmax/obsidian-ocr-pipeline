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
	/** true if process was killed after `timeoutMs`. */
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
	timeoutMs?: number;
	gracePeriodMs?: number;
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
export const ABORT_GRACE_PERIOD_MS = 5000;
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

export function abortChild(
	child: ChildProcess,
	gracePeriodMs: number = ABORT_GRACE_PERIOD_MS,
): void {
	if (child.exitCode !== null || child.signalCode !== null) return;
	if (!child.kill("SIGTERM")) return;
	const timer = safeSetTimeout(() => {
		child.kill("SIGKILL");
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
	gracePeriodMs: number = ABORT_GRACE_PERIOD_MS,
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

function parseProgressEvent(line: string): ProgressEvent | null {
	let obj: unknown;
	try {
		obj = JSON.parse(line) as unknown;
	} catch {
		return null;
	}
	if (typeof obj !== "object" || obj === null) return null;
	const e = obj as Record<string, unknown>;
	const typ = e.typ ?? e.type;
	if (typeof typ !== "string") return null;
	switch (typ) {
		case "start": {
			const file = textVal(e.datei ?? e.file);
			const pages = numVal(e.seiten ?? e.pages);
			const dpi = numVal(e.dpi);
			if (!file || pages === null || dpi === null) return null;
			return { type: "start", file, pages, dpi };
		}
		case "seite":
		case "page": {
			const num = numVal(e.nr ?? e.num);
			const total = numVal(e.von ?? e.total);
			const seconds = numVal(e.sekunden ?? e.seconds);
			const origin = textVal(e.herkunft ?? e.origin);
			const derailed = typeof (e.entgleist ?? e.derailed) === "boolean" ? (e.entgleist ?? e.derailed) as boolean : false;
			const reason = textVal(e.grund ?? e.reason);
			if (num === null || total === null || seconds === null || !origin) return null;
			return {
				type: "page",
				num,
				total,
				seconds,
				origin,
				derailed,
				...(reason ? { reason } : {}),
			};
		}
		case "fertig":
		case "finished": {
			const target = textVal(e.ziel ?? e.target);
			const sec = numVal(e.sekunden ?? e.seconds);
			const der = numVal(e.entgleist ?? e.derailed);
			if (!target || sec === null || der === null) return null;
			return { type: "finished", target, seconds: sec, derailed: der };
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
	timeoutMs?: number;
	/** Stops the child when `timeoutMs` has passed. */
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
		child.stdout?.setEncoding("utf8");
		child.stderr?.setEncoding("utf8");
		child.stdout?.on("data", (chunk) => stdoutBuf.write(String(chunk)));
		child.stderr?.on("data", (chunk) => stderrBuf.write(String(chunk)));

		let isTimeout = false;
		const timer =
			options.timeoutMs === undefined
				? null
				: safeSetTimeout(() => {
						isTimeout = true;
						options.onTimeout?.(child);
					}, options.timeoutMs);
		const done = (result: ConversionResult) => {
			if (timer !== null) safeClearTimeout(timer);
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
		...(options.timeoutMs === undefined ? {} : { timeoutMs: options.timeoutMs }),
		onTimeout: (child) => abortChild(child, options.gracePeriodMs),
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
): Promise<ConversionResult> {
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
	return runProcess(cli, args, spawnOptions, spawnFn, {
		...(options.onChild ? { onChild: options.onChild } : {}),
	});
}
