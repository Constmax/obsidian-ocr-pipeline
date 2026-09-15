// Conversion lifecycle for Stage 2 (pdf2md): one conversion at a time, child
// ownership and cancellation, timeout policy, progress messages, result
// classification, and opening the result. Free of Obsidian imports so it runs
// under `node --test`; the plugin supplies a ConversionHost.

import { existsSync } from "fs";
import { homedir } from "os";
import { join } from "path";
import type { ChildProcess } from "child_process";

import {
	abortChild,
	convertPdf,
	type ConversionOptions,
	type ConversionResult,
	type SpawnFunction,
} from "./conversion.ts";

export const CONVERSION_TIMEOUT_MS = 30 * 60 * 1000;
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
	/** Paths of other vault PDFs with the same basename. */
	pdfsWithSameBasename(pdf: PdfSource): string[];
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

export interface ControllerDependencies {
	convert?: ConvertFunction;
	abort?: (child: ChildProcess) => void;
	resolveExecutable?: () => string;
	timeoutMs?: number;
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
export function resolvePdf2md(
	searchPath: string = process.env.PATH ?? "",
	home: string = homedir(),
	exists: (candidate: string) => boolean = existsSync,
): string {
	const candidates = [join(home, "bin", "pdf2md"), "/usr/local/bin/pdf2md"];
	for (const part of searchPath.split(":")) {
		if (part.length > 0) candidates.push(join(part, "pdf2md"));
	}
	for (const candidate of candidates) {
		if (exists(candidate)) return candidate;
	}
	return candidates[0]!;
}

/** Maps a non-zero pdf2md result to the user-facing failure message. */
export function classifyFailure(
	result: ConversionResult,
	timeoutMs: number = CONVERSION_TIMEOUT_MS,
): FailureDescription {
	const stderrLast = result.stderrLast;
	const stdoutLast = result.stdoutLast.filter((line) => !line.startsWith("→"));
	const detail =
		(stderrLast.length > 0 ? stderrLast[stderrLast.length - 1] : undefined) ??
		(stdoutLast.length > 0 ? stdoutLast[stdoutLast.length - 1] : undefined) ??
		"";

	let kind: FailureKind;
	let codeText: string;
	if (result.code === 6) {
		kind = "partial-output";
		codeText = "cancelled — partial file created (incomplete)";
	} else if (result.code === 7) {
		kind = "cancelled-before-output";
		codeText = "cancelled — before first page (no partial file)";
	} else if (result.signal === "SIGKILL") {
		kind = "killed";
		codeText = "cancelled — force terminated after grace period (SIGKILL)";
	} else if (result.timeout) {
		kind = "timeout";
		codeText = `cancelled after ${timeoutMs / 60000} min`;
	} else if (result.code === null && result.signal !== null) {
		kind = "signal";
		codeText = `cancelled (Signal ${result.signal})`;
	} else if (result.code === null) {
		kind = "start-error";
		codeText = "Start error";
	} else if (result.code === 4) {
		// pdf2md's EXIT_CHECK: a dependency check failed.
		kind = "missing-dependency";
		codeText = "Code 4";
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

export class ConversionController {
	private readonly host: ConversionHost;
	private readonly convert: ConvertFunction;
	private readonly abort: (child: ChildProcess) => void;
	private readonly resolveExecutable: () => string;
	private readonly timeoutMs: number;

	private running = false;
	private child: ChildProcess | null = null;
	private progress: ProgressDisplay | null = null;
	private cancelRequested = false;
	private currentName = "";

	constructor(host: ConversionHost, dependencies: ControllerDependencies = {}) {
		this.host = host;
		this.convert = dependencies.convert ?? convertPdf;
		this.abort = dependencies.abort ?? ((child) => abortChild(child));
		this.resolveExecutable = dependencies.resolveExecutable ?? (() => resolvePdf2md());
		this.timeoutMs = dependencies.timeoutMs ?? CONVERSION_TIMEOUT_MS;
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
		this.running = true;
		this.cancelRequested = false;
		const name = pdf.basename;
		this.currentName = name;
		try {
			const duplicates = this.host.pdfsWithSameBasename(pdf);
			if (duplicates.length > 0) {
				this.host.notify(
					`OCR Preview: "${name}" also exists as ${duplicates.join(", ")} — output would overwrite. Please rename one of the files.`,
				);
				return;
			}
			const base = this.host.vaultBasePath();
			if (base === null) {
				this.host.notify("OCR Preview: Conversion requires file system access (Desktop).");
				return;
			}
			const folder = this.host.previewFolder();
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
					timeoutMs: this.timeoutMs,
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
				this.host.notify(classifyFailure(result, this.timeoutMs).message);
				return;
			}
			await this.openResult(name, folder);
		} catch (err) {
			this.host.notify(`OCR Preview: Conversion failed — ${String(err)}.`);
		} finally {
			this.progress?.hide();
			this.progress = null;
			this.running = false;
			this.child = null;
			this.cancelRequested = false;
		}
	}

	/** User cancellation of the running conversion; repeated calls are ignored. */
	cancel(): void {
		if (this.progress === null || this.cancelRequested) return;
		this.cancelRequested = true;
		this.progress.setMessage(`OCR Preview: "${this.currentName}" is being cancelled …`);
		if (this.child !== null) this.abort(this.child);
	}

	/** Plugin unload: stop the child without waiting for its result. */
	dispose(): void {
		if (this.child !== null) this.abort(this.child);
		this.child = null;
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
