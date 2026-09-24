// Conversion lifecycle for Stage 2 (pdf2md) and Stage 1 (reprocess-raw
// --output): one conversion at a time, child ownership and cancellation,
// timeout policy, progress messages, result classification, and opening the
// Stage-2 result. Free of Obsidian imports so it runs under `node --test`; the
// plugin supplies a ConversionHost.

import { existsSync } from "fs";
import { homedir } from "os";
import { join } from "path";
import type { ChildProcess } from "child_process";

import {
	abortChild,
	convertPdf,
	createSearchableCopy,
	terminateProcessGroup,
	type ConversionOptions,
	type ConversionResult,
	type OcrEngine,
	type SearchableCopyOptions,
	type SearchableCopyResult,
	type SpawnFunction,
} from "./conversion.ts";

/**
 * pdf2md is stopped after this long without any output. A page takes 15–60 s
 * and a document may have hundreds, so no limit on the total run fits
 * (Issue #105); a quarter hour of silence leaves room for derailment retries.
 */
export const CONVERSION_IDLE_TIMEOUT_MS = 15 * 60 * 1000;
/** pdf2md exit codes the plugin tells apart; pinned in contracts/cli-contract.json. */
export const EXIT_CODES = {
	checkFailed: 4,
	cancelledPartial: 6,
	cancelledEmpty: 7,
} as const;
export const INDEX_WAIT_STEPS = 10;
export const INDEX_WAIT_MS = 200;

export interface PdfSource {
	path: string;
	basename: string;
}

export interface ProgressDisplay {
	setMessage(message: string): void;
	hide(): void;
}

/** Everything the controller needs from Obsidian. */
export interface ConversionHost {
	/** Shows a transient message. */
	notify(message: string): void;
	/** Shows a persistent progress message with a cancel control. */
	showProgress(message: string, onCancel: () => void): ProgressDisplay;
	/** Vault root on disk, or null without file-system access. */
	vaultBasePath(): string | null;
	/** Paths of other convertible vault files with the same basename. */
	pdfsWithSameBasename(pdf: PdfSource): string[];
	/**
	 * Where the existing preview `entryName` came from, or null when there is
	 * none and nothing can be overwritten. A preview that records no source
	 * returns its own path, which matches no input: an unknown origin counts
	 * as foreign rather than as safe to overwrite.
	 */
	previewSource(entryName: string, folder: string): string | null;
	/** Preview folder as configured and normalized for vault lookups. */
	previewFolder(): { configured: string; normalized: string };
	reconcile(): Promise<void>;
	hasPreviewEntry(entryName: string, folder: string): boolean;
	/** Opens the entry in the comparison view; false if the view is unavailable. */
	openPreviewEntry(entryName: string): Promise<boolean>;
	wait(ms: number): Promise<void>;
}

export type ConvertFunction = (
	pdf: string,
	out: string,
	pdf2md: string,
	cwd: string,
	spawnFn?: SpawnFunction,
	options?: ConversionOptions,
) => Promise<ConversionResult>;

export type SearchableCopyFunction = (
	source: string,
	destination: string,
	cli: string,
	cwd: string,
	spawnFn?: SpawnFunction,
	options?: SearchableCopyOptions,
) => Promise<SearchableCopyResult>;

export interface ControllerDependencies {
	convert?: ConvertFunction;
	/** Stops a Stage-2 child. */
	abort?: (child: ChildProcess) => void;
	resolveExecutable?: () => string;
	idleTimeoutMs?: number;
	searchableCopy?: SearchableCopyFunction;
	/** Stops a Stage-1 child together with its process group. */
	abortGroup?: (child: ChildProcess) => void;
	resolveReprocessRaw?: () => string;
}

/** A Stage-1 run; paths are vault-relative. */
export interface SearchableCopyRequest {
	source: PdfSource;
	destination: string;
	engine: OcrEngine;
	splitColumns: boolean;
	/** Pages exempt from the B5 gate, e.g. "1,5-7". */
	allowPages?: string;
}

export type FailureKind =
	| "partial-output"
	| "cancelled-before-output"
	| "killed"
	| "timeout"
	| "signal"
	| "start-error"
	| "not-found"
	| "missing-dependency"
	| "exit-code";

export interface FailureDescription {
	kind: FailureKind;
	message: string;
}

/** Candidate order: ~/bin, /usr/local/bin, then PATH; falls back to ~/bin. */
export function resolveCli(
	name: string,
	searchPath: string = process.env.PATH ?? "",
	home: string = homedir(),
	exists: (candidate: string) => boolean = existsSync,
): string {
	const candidates = [join(home, "bin", name), join("/usr/local/bin", name)];
	for (const part of searchPath.split(":")) {
		if (part.length > 0) candidates.push(join(part, name));
	}
	for (const candidate of candidates) {
		if (exists(candidate)) return candidate;
	}
	return candidates[0]!;
}

export function resolvePdf2md(
	searchPath?: string,
	home?: string,
	exists?: (candidate: string) => boolean,
): string {
	return resolveCli("pdf2md", searchPath, home, exists);
}

/** Maps a non-zero pdf2md result to the user-facing failure message. */
export function classifyFailure(
	result: ConversionResult,
	idleTimeoutMs: number = CONVERSION_IDLE_TIMEOUT_MS,
): FailureDescription {
	const stderrLast = result.stderrLast;
	const stdoutLast = result.stdoutLast.filter((line) => !line.startsWith("→"));
	const detail =
		(stderrLast.length > 0 ? stderrLast[stderrLast.length - 1] : undefined) ??
		(stdoutLast.length > 0 ? stdoutLast[stdoutLast.length - 1] : undefined) ??
		"";

	let kind: FailureKind;
	let codeText: string;
	if (result.code === EXIT_CODES.cancelledPartial) {
		kind = "partial-output";
		codeText = "cancelled — partial file created (incomplete)";
	} else if (result.code === EXIT_CODES.cancelledEmpty) {
		kind = "cancelled-before-output";
		codeText = "cancelled — before first page (no partial file)";
	} else if (result.signal === "SIGKILL") {
		kind = "killed";
		codeText = "cancelled — force terminated after grace period (SIGKILL)";
	} else if (result.timeout) {
		kind = "timeout";
		codeText = `cancelled — no output for ${idleTimeoutMs / 60000} min`;
	} else if (result.code === null && result.signal !== null) {
		kind = "signal";
		codeText = `cancelled (Signal ${result.signal})`;
	} else if (result.code === null) {
		kind = "start-error";
		codeText = "Start error";
	} else if (result.code === EXIT_CODES.checkFailed) {
		// pdf2md's EXIT_CHECK: a dependency check failed.
		kind = "missing-dependency";
		codeText = `Code ${EXIT_CODES.checkFailed}`;
	} else {
		kind = "exit-code";
		codeText = `Code ${result.code}`;
	}

	let extra = detail.length > 0 ? ` — ${detail}` : "";
	if (result.code === null && /ENOENT/.test(detail)) {
		if (kind === "start-error") kind = "not-found";
		extra = " — pdf2md not found. Please run setup.sh in repo.";
	}
	return { kind, message: `OCR Preview: Conversion failed (${codeText})${extra}.` };
}

/**
 * Maps a failed reprocess-raw result (not a user cancellation) to the
 * user-facing message. The script reports its reason on a line starting with
 * ❌; that line wins over the last output line ("No file written: …").
 */
export function classifyOcrFailure(result: ConversionResult): FailureDescription {
	const lines = [...result.stdoutLast, ...result.stderrLast];
	const reason = [...lines].reverse().find((line) => line.startsWith("❌"));
	const last = result.stderrLast[result.stderrLast.length - 1] ?? result.stdoutLast[result.stdoutLast.length - 1];
	let detail = (reason ?? last ?? "").replace(/^❌\s*/, "");

	let kind: FailureKind;
	let codeText: string;
	if (result.signal === "SIGKILL") {
		kind = "killed";
		codeText = "force terminated (SIGKILL)";
	} else if (result.code === null && result.signal !== null) {
		kind = "signal";
		codeText = `terminated (Signal ${result.signal})`;
	} else if (result.code === null) {
		kind = /ENOENT/.test(detail) ? "not-found" : "start-error";
		codeText = "Start error";
		if (kind === "not-found") detail = "reprocess-raw not found. Please run setup.sh in repo";
	} else {
		kind = "exit-code";
		codeText = `Code ${result.code}`;
	}

	const extra = detail.length > 0 ? ` — ${detail.replace(/\.$/, "")}` : "";
	return { kind, message: `OCR Preview: Searchable copy failed (${codeText})${extra}.` };
}

export class ConversionController {
	private readonly host: ConversionHost;
	private readonly convert: ConvertFunction;
	private readonly abort: (child: ChildProcess) => void;
	private readonly resolveExecutable: () => string;
	private readonly idleTimeoutMs: number;
	private readonly searchableCopy: SearchableCopyFunction;
	private readonly abortGroup: (child: ChildProcess) => void;
	private readonly resolveReprocessRaw: () => string;

	private running = false;
	private child: ChildProcess | null = null;
	/** How the current child is stopped: Stage 2 by PID, Stage 1 by process group. */
	private stopChild: (child: ChildProcess) => void;
	private progress: ProgressDisplay | null = null;
	private cancelRequested = false;
	private currentName = "";

	constructor(host: ConversionHost, dependencies: ControllerDependencies = {}) {
		this.host = host;
		this.convert = dependencies.convert ?? convertPdf;
		this.abort = dependencies.abort ?? ((child) => abortChild(child));
		this.resolveExecutable = dependencies.resolveExecutable ?? (() => resolvePdf2md());
		this.idleTimeoutMs = dependencies.idleTimeoutMs ?? CONVERSION_IDLE_TIMEOUT_MS;
		this.searchableCopy = dependencies.searchableCopy ?? createSearchableCopy;
		this.abortGroup =
			dependencies.abortGroup ?? ((child) => void terminateProcessGroup(child));
		this.resolveReprocessRaw =
			dependencies.resolveReprocessRaw ?? (() => resolveCli("reprocess-raw"));
		this.stopChild = this.abort;
	}

	get isRunning(): boolean {
		return this.running;
	}

	/** True if no conversion runs; otherwise tells the user and returns false. */
	ensureIdle(): boolean {
		if (!this.running) return true;
		this.host.notify("OCR Preview: A conversion is already running.");
		return false;
	}

	async run(pdf: PdfSource, pages?: string): Promise<void> {
		if (!this.ensureIdle()) return;
		this.begin(pdf.basename, this.abort);
		const name = pdf.basename;
		try {
			const folder = this.host.previewFolder();
			const entryName = `${name}.md`;
			const duplicates = this.host.pdfsWithSameBasename(pdf);
			if (duplicates.length > 0) {
				// A shared basename costs something only once it destroys
				// something: a preview that exists and came from one of the
				// rivals. Vetoing on the mere existence of a rival was
				// tolerable while only PDFs could be sources; since images
				// convert too (Issue #100), any same-named vault attachment
				// would block a conversion that overwrites nothing.
				const existing = this.host.previewSource(entryName, folder.normalized);
				if (existing !== null && existing !== pdf.path) {
					this.host.notify(
						`OCR Preview: "${entryName}" was not converted from ${pdf.path} — converting would overwrite it. Please rename one of ${[pdf.path, ...duplicates].join(", ")}.`,
					);
					return;
				}
				this.host.notify(
					`OCR Preview: "${name}" also exists as ${duplicates.join(", ")} — all of them write "${entryName}".`,
				);
			}
			const base = this.host.vaultBasePath();
			if (base === null) {
				this.host.notify("OCR Preview: Conversion requires file system access (Desktop).");
				return;
			}
			const progress = this.host.showProgress(`OCR Preview: Converting "${name}" …`, () =>
				this.cancel(),
			);
			this.progress = progress;
			const result = await this.convert(
				pdf.path,
				folder.normalized,
				this.resolveExecutable(),
				base,
				undefined,
				{
					idleTimeoutMs: this.idleTimeoutMs,
					...(pages && pages.length > 0 ? { pages } : {}),
					onChild: (child) => {
						this.child = child;
					},
					onProgress: (event) => {
						if (event.type !== "page" || this.cancelRequested) return;
						const position = event.derailed
							? `— page ${event.num} of ${event.total} (derailed)`
							: `— page ${event.num} of ${event.total}`;
						progress.setMessage(`OCR Preview: Converting "${name}" ${position} …`);
					},
				},
			);
			this.child = null;
			progress.hide();
			this.progress = null;
			if (result.code !== 0) {
				this.host.notify(classifyFailure(result, this.idleTimeoutMs).message);
				return;
			}
			await this.openResult(name, folder);
		} catch (err) {
			this.host.notify(`OCR Preview: Conversion failed — ${String(err)}.`);
		} finally {
			this.end();
		}
	}

	/**
	 * Stage 1: writes a searchable copy of the source to `request.destination`
	 * with `reprocess-raw --output`. Progress is indeterminate. Cancellation and
	 * failures are reported here, except a B5 failure with short pages: that one
	 * and success are left to the caller, which offers page exemptions or opens
	 * the new PDF. Resolves with the CLI result, or null if nothing ran.
	 */
	async runOcr(request: SearchableCopyRequest): Promise<SearchableCopyResult | null> {
		if (!this.ensureIdle()) return null;
		const name = request.source.basename;
		this.begin(name, this.abortGroup);
		try {
			const base = this.host.vaultBasePath();
			if (base === null) {
				this.host.notify("OCR Preview: A searchable copy requires file system access (Desktop).");
				return null;
			}
			const progress = this.host.showProgress(
				`OCR Preview: Creating searchable copy of "${name}" …`,
				() => this.cancel(),
			);
			this.progress = progress;
			const result = await this.searchableCopy(
				request.source.path,
				request.destination,
				this.resolveReprocessRaw(),
				base,
				undefined,
				{
					engine: request.engine,
					splitColumns: request.splitColumns,
					...(request.allowPages && request.allowPages.length > 0
						? { allowPages: request.allowPages }
						: {}),
					onChild: (child) => {
						this.child = child;
					},
				},
			);
			this.child = null;
			progress.hide();
			this.progress = null;
			if (result.code !== 0) {
				if (this.cancelRequested) {
					this.host.notify(`OCR Preview: Searchable copy of "${name}" cancelled — no file written.`);
					// A cancelled run never leads to page exemptions.
					return { ...result, shortPages: [] };
				}
				if (result.shortPages.length === 0) {
					this.host.notify(classifyOcrFailure(result).message);
				}
			}
			return result;
		} catch (err) {
			this.host.notify(`OCR Preview: Searchable copy failed — ${String(err)}.`);
			return null;
		} finally {
			this.end();
		}
	}

	/** User cancellation of the running conversion; repeated calls are ignored. */
	cancel(): void {
		if (this.progress === null || this.cancelRequested) return;
		this.cancelRequested = true;
		this.progress.setMessage(`OCR Preview: "${this.currentName}" is being cancelled …`);
		if (this.child !== null) this.stopChild(this.child);
	}

	/** Plugin unload: stop the child (Stage 1: its whole process group) without waiting. */
	dispose(): void {
		if (this.child !== null) this.stopChild(this.child);
		this.child = null;
	}

	private begin(name: string, stopChild: (child: ChildProcess) => void): void {
		this.running = true;
		this.cancelRequested = false;
		this.currentName = name;
		this.stopChild = stopChild;
	}

	private end(): void {
		this.progress?.hide();
		this.progress = null;
		this.running = false;
		this.child = null;
		this.cancelRequested = false;
	}

	private async openResult(
		name: string,
		folder: { configured: string; normalized: string },
	): Promise<void> {
		const entryName = `${name}.md`;
		const isPresent = () => this.host.hasPreviewEntry(entryName, folder.normalized);
		await this.host.reconcile();
		for (let step = 0; !isPresent() && step < INDEX_WAIT_STEPS; step++) {
			await this.host.wait(INDEX_WAIT_MS);
			await this.host.reconcile();
		}
		if (isPresent()) {
			const opened = await this.host.openPreviewEntry(entryName);
			this.host.notify(
				opened
					? `OCR Preview: "${name}" finished — comparison opened.`
					: `OCR Preview: "${name}" finished — preview created.`,
			);
		} else {
			this.host.notify(
				`OCR Preview: "${name}" finished, but was not placed in preview folder (Target: ${folder.configured}).`,
			);
		}
	}
}
