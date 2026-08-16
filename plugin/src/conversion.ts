// PDF conversion via child process: calls local pdf2md script (stage 2 of pipeline)
// with `--out` and collects last output lines. Machine-readable progress is available via `--fortschritt` flag.

import { spawn, type ChildProcess } from "child_process";

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

const LAST_LINES = 5;
export const ABORT_GRACE_PERIOD_MS = 5000;

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

export function convertPdf(
	pdf: string,
	out: string,
	pdf2md: string,
	cwd: string,
	spawnFn: SpawnFunction = spawn,
	options: ConversionOptions = {},
): Promise<ConversionResult> {
	return new Promise((resolve) => {
		let child: ChildProcess;
		try {
			const args = [pdf, "--out", out];
			if (options.pages && options.pages.length > 0) {
				args.push("--seiten", options.pages);
			}
			args.push("--fortschritt");
			child = spawnFn(pdf2md, args, {
				cwd,
				stdio: ["ignore", "pipe", "pipe"],
			});
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
		const stderrBuf = lineBuffer(stderrLast, (line) => {
			const event = parseProgressEvent(line);
			if (event && options.onProgress) {
				options.onProgress(event);
				return false;
			}
			return true;
		});
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
						abortChild(child, options.gracePeriodMs);
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
