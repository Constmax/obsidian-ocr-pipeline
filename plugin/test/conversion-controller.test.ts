import assert from "node:assert/strict";
import { test } from "node:test";
import type { ChildProcess } from "node:child_process";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
	CONVERSION_IDLE_TIMEOUT_MS,
	INDEX_WAIT_MS,
	INDEX_WAIT_STEPS,
	ConversionController,
	classifyFailure,
	classifyOcrFailure,
	resolveCli,
	findCli,
	commandPathProblem,
	pdf2mdExecutable,
	INSTALL_DOCS_URL,
	resolvePdf2md,
	type ConversionHost,
	type PreviewAtRisk,
	type ConvertFunction,
	type PdfSource,
	type ProgressDisplay,
	type TextLayerFunction,
	type TextLayerRequest,
} from "../src/conversion-controller.ts";
import type {
	ConversionOptions,
	ConversionResult,
	ProgressEvent,
	TextLayerOptions,
} from "../src/conversion.ts";

const PDF: PdfSource = { path: "raw/case-01.pdf", basename: "case-01" };

class FakeHost implements ConversionHost {
	notices: string[] = [];
	progress: string[] = [];
	/** Status-bar texts the controller passed with a message. */
	statusTexts: string[] = [];
	hidden = 0;
	cancelControl: (() => void) | null = null;
	basePath: string | null = "/vault";
	duplicates: string[] = [];
	folder = { configured: "_ocr-preview/", normalized: "_ocr-preview" };
	reconciles = 0;
	waits: number[] = [];
	/** The entry is in the inventory once this many reconciles happened. */
	presentAfterReconciles = 1;
	openSucceeds = true;
	opened: string[] = [];
	lookups: Array<[string, string]> = [];
	/** Origin of an already existing preview; null = no preview yet. */
	existingPreviewSource: string | null = null;
	/** What a re-conversion would destroy; null = nothing. */
	atRisk: PreviewAtRisk = null;
	confirmAnswer = true;
	confirms: string[] = [];
	copySucceeds = true;
	copies: string[] = [];

	notify(message: string): void {
		this.notices.push(message);
	}
	showProgress(message: string, onCancel: () => void): ProgressDisplay {
		this.progress.push(message);
		this.cancelControl = onCancel;
		return {
			setMessage: (text, status) => {
				this.progress.push(text);
				if (status !== undefined) this.statusTexts.push(status);
			},
			hide: () => {
				this.hidden++;
			},
		};
	}
	vaultBasePath(): string | null {
		return this.basePath;
	}
	pdfsWithSameBasename(): string[] {
		return this.duplicates;
	}
	previewSource(): string | null {
		return this.existingPreviewSource;
	}
	previewFolder(): { configured: string; normalized: string } {
		return this.folder;
	}
	previewAtRisk(): PreviewAtRisk {
		return this.atRisk;
	}
	async confirmReconvert(message: string): Promise<boolean> {
		this.confirms.push(message);
		return this.confirmAnswer;
	}
	async keepEditedCopy(entryName: string): Promise<boolean> {
		this.copies.push(entryName);
		return this.copySucceeds;
	}
	async reconcile(): Promise<void> {
		this.reconciles++;
	}
	hasPreviewEntry(entryName: string, folder: string): boolean {
		this.lookups.push([entryName, folder]);
		return this.reconciles >= this.presentAfterReconciles;
	}
	async openPreviewEntry(entryName: string): Promise<boolean> {
		this.opened.push(entryName);
		return this.openSucceeds;
	}
	async wait(ms: number): Promise<void> {
		this.waits.push(ms);
	}
}

interface ConvertCall {
	args: [string, string, string, string];
	spawnFn: unknown;
	options: ConversionOptions;
	finish: (result: ConversionResult) => void;
}

function result(overrides: Partial<ConversionResult> = {}): ConversionResult {
	return { code: 0, signal: null, timeout: false, stdoutLast: [], stderrLast: [], ...overrides };
}

function page(num: number, total: number, derailed = false): ProgressEvent {
	return { type: "page", num, total, seconds: 1, origin: "OCR", derailed };
}

interface OcrCall {
	args: [string, string, string];
	spawnFn: unknown;
	options: TextLayerOptions;
	/** Missing shortPages default to none. */
	finish: (result: ConversionResult & { shortPages?: number[] }) => void;
}

function setup(host = new FakeHost()) {
	const calls: ConvertCall[] = [];
	const ocrCalls: OcrCall[] = [];
	const aborted: ChildProcess[] = [];
	const groupAborted: ChildProcess[] = [];
	const convert: ConvertFunction = (pdf, out, pdf2md, cwd, spawnFn, options = {}) =>
		new Promise((finish) => {
			calls.push({ args: [pdf, out, pdf2md, cwd], spawnFn, options, finish });
		});
	const addTextLayer: TextLayerFunction = (source, cli, cwd, spawnFn, options = {}) =>
		new Promise((resolve) => {
			ocrCalls.push({
				args: [source, cli, cwd],
				spawnFn,
				options,
				finish: (r) => resolve({ shortPages: [], ...r }),
			});
		});
	const controller = new ConversionController(host, {
		convert,
		abort: (child) => {
			aborted.push(child);
		},
		resolveExecutable: () => "/home/test/bin/pdf2md",
		addTextLayer,
		abortGroup: (child) => {
			groupAborted.push(child);
		},
		resolveReprocessRaw: () => "/home/test/bin/reprocess-raw",
	});
	return { host, calls, ocrCalls, aborted, groupAborted, controller };
}

const OCR_REQUEST: TextLayerRequest = {
	source: PDF,
	engine: "apple",
	keepOriginal: ".ocr-originals/1/case-01.pdf",
	allowPages: "1",
};

const child = { pid: 42 } as unknown as ChildProcess;

test("success: passes paths, timeout and pages, reports page progress, opens the preview", async () => {
	const { host, calls, controller } = setup();
	const running = controller.run(PDF, "1-3");

	assert.equal(controller.isRunning, true);
	assert.equal(calls.length, 1);
	const call = calls[0]!;
	assert.deepEqual(call.args, ["raw/case-01.pdf", "_ocr-preview", "/home/test/bin/pdf2md", "/vault"]);
	assert.equal(call.spawnFn, undefined);
	assert.equal(call.options.idleTimeoutMs, CONVERSION_IDLE_TIMEOUT_MS);
	assert.equal(call.options.pages, "1-3");

	call.options.onChild!(child);
	call.options.onProgress!({ type: "start", file: "case-01.pdf", pages: 3, dpi: 150 });
	call.options.onProgress!(page(1, 3));
	call.options.onProgress!(page(2, 3, true));
	call.finish(result());
	await running;

	assert.deepEqual(host.progress, [
		'OCR Preview: Converting "case-01" …',
		'OCR Preview: Converting "case-01" — 3 pages …',
		'OCR Preview: Converting "case-01" — page 1 of 3 · under 1 min left …',
		'OCR Preview: Converting "case-01" — page 2 of 3 · 1 derailed · under 1 min left …',
	]);
	// Progress first: the status bar clips the end, not the page count.
	assert.deepEqual(host.statusTexts, [
		'OCR: 3 pages — Converting "case-01"',
		'OCR: page 1 of 3 · under 1 min left — Converting "case-01"',
		'OCR: page 2 of 3 · 1 derailed · under 1 min left — Converting "case-01"',
	]);
	assert.equal(host.hidden, 1);
	assert.equal(host.reconciles, 1);
	assert.deepEqual(host.lookups[0], ["case-01.md", "_ocr-preview"]);
	assert.deepEqual(host.opened, ["case-01.md"]);
	assert.deepEqual(host.notices, ['OCR Preview: "case-01" finished — comparison opened.']);
	assert.equal(controller.isRunning, false);
});

test("missing or empty pages: no pages option", async () => {
	for (const pages of [undefined, ""]) {
		const { calls, controller } = setup();
		const running = controller.run(PDF, pages);
		assert.equal("pages" in calls[0]!.options, false);
		calls[0]!.finish(result());
		await running;
	}
});

test("waits for the inventory before opening the new entry", async () => {
	const host = new FakeHost();
	host.presentAfterReconciles = 3;
	const { calls, controller } = setup(host);
	const running = controller.run(PDF);
	calls[0]!.finish(result());
	await running;

	assert.deepEqual(host.waits, [INDEX_WAIT_MS, INDEX_WAIT_MS]);
	assert.equal(host.reconciles, 3);
	assert.deepEqual(host.opened, ["case-01.md"]);
});

test("entry never appears: bounded waiting, then names the configured folder", async () => {
	const host = new FakeHost();
	host.presentAfterReconciles = Number.POSITIVE_INFINITY;
	const { calls, controller } = setup(host);
	const running = controller.run(PDF);
	calls[0]!.finish(result());
	await running;

	assert.equal(host.waits.length, INDEX_WAIT_STEPS);
	assert.equal(host.reconciles, INDEX_WAIT_STEPS + 1);
	assert.deepEqual(host.opened, []);
	assert.deepEqual(host.notices, [
		'OCR Preview: "case-01" finished, but was not placed in preview folder (Target: _ocr-preview/).',
	]);
});

test("view cannot open the entry: reports the created preview", async () => {
	const host = new FakeHost();
	host.openSucceeds = false;
	const { calls, controller } = setup(host);
	const running = controller.run(PDF);
	calls[0]!.finish(result());
	await running;

	assert.deepEqual(host.notices, ['OCR Preview: "case-01" finished — preview created.']);
});

test("a second request while running is refused without spawning", async () => {
	const { host, calls, controller } = setup();
	const running = controller.run(PDF);

	await controller.run({ path: "raw/other.pdf", basename: "other" });
	assert.equal(controller.ensureIdle(), false);
	assert.equal(calls.length, 1);

	calls[0]!.finish(result());
	await running;
	assert.equal(controller.ensureIdle(), true);
	assert.deepEqual(
		host.notices.filter((n) => n === "OCR Preview: A conversion is already running."),
		["OCR Preview: A conversion is already running.", "OCR Preview: A conversion is already running."],
	);
});

test("duplicate basename stops before spawning when a foreign preview exists", async () => {
	const host = new FakeHost();
	host.duplicates = ["other/case-01.pdf", "old/case-01.pdf"];
	host.existingPreviewSource = "other/case-01.pdf";
	const { calls, controller } = setup(host);
	await controller.run(PDF);

	assert.equal(calls.length, 0);
	assert.deepEqual(host.progress, []);
	assert.deepEqual(host.notices, [
		'OCR Preview: "case-01.md" was not converted from raw/case-01.pdf — converting would overwrite it. Please rename one of raw/case-01.pdf, other/case-01.pdf, old/case-01.pdf.',
	]);
	assert.equal(controller.isRunning, false);
});

test("duplicate basename only warns while there is no preview to overwrite", async () => {
	// A vault image named like the PDF must not veto a conversion that
	// destroys nothing (Issue #100).
	const host = new FakeHost();
	host.duplicates = ["attachments/case-01.png"];
	const { calls, controller } = setup(host);
	const running = controller.run(PDF);

	assert.equal(calls.length, 1);
	assert.deepEqual(host.notices, [
		'OCR Preview: "case-01" also exists as attachments/case-01.png — all of them write "case-01.md".',
	]);
	calls[0]!.finish(result());
	await running;
});

test("duplicate basename does not block re-converting the same source", async () => {
	const host = new FakeHost();
	host.duplicates = ["attachments/case-01.png"];
	host.existingPreviewSource = PDF.path;
	const { calls, controller } = setup(host);
	const running = controller.run(PDF);

	assert.equal(calls.length, 1);
	calls[0]!.finish(result());
	await running;
});

test("an edited preview: asks first, keeps a copy, then converts", async () => {
	const host = new FakeHost();
	host.atRisk = "edited";
	const { calls, controller } = setup(host);
	const running = controller.run(PDF, "2");
	await new Promise((done) => setImmediate(done));

	assert.deepEqual(host.confirms, [
		'"case-01.md" has manual edits. Converting again overwrites them; a copy of the edited file is kept in the rejected folder.',
	]);
	assert.deepEqual(host.copies, ["case-01.md"]);
	assert.equal(calls.length, 1);
	calls[0]!.finish(result());
	await running;
});

test("a decided preview: asks first, converts without a copy", async () => {
	const host = new FakeHost();
	host.atRisk = "accepted";
	const { calls, controller } = setup(host);
	const running = controller.run(PDF);
	await new Promise((done) => setImmediate(done));

	assert.deepEqual(host.confirms, [
		'"case-01.md" is already accepted. Converting again creates a new version to review.',
	]);
	assert.deepEqual(host.copies, []);
	assert.equal(calls.length, 1);
	calls[0]!.finish(result());
	await running;
});

test("declining the re-conversion spawns nothing and leaves the controller idle", async () => {
	const host = new FakeHost();
	host.atRisk = "edited";
	host.confirmAnswer = false;
	const { calls, controller } = setup(host);
	await controller.run(PDF);

	assert.equal(calls.length, 0);
	assert.deepEqual(host.copies, []);
	assert.deepEqual(host.progress, []);
	assert.equal(controller.ensureIdle(), true);
});

test("a failed copy of the edited preview stops the conversion", async () => {
	const host = new FakeHost();
	host.atRisk = "edited";
	host.copySucceeds = false;
	const { calls, controller } = setup(host);
	await controller.run(PDF);

	assert.equal(calls.length, 0);
	assert.deepEqual(host.notices, [
		'OCR Preview: "case-01.md" was not converted — the copy of its edits could not be kept.',
	]);
	assert.equal(controller.isRunning, false);
});

test("without file-system access nothing is spawned", async () => {
	const host = new FakeHost();
	host.basePath = null;
	const { calls, controller } = setup(host);
	await controller.run(PDF);

	assert.equal(calls.length, 0);
	assert.deepEqual(host.notices, ["OCR Preview: Conversion requires file system access (Desktop)."]);
	assert.equal(controller.isRunning, false);
});

test("cancel: shows the cancelling state, aborts once, ignores later progress", async () => {
	const { host, calls, aborted, controller } = setup();
	const running = controller.run(PDF);
	const call = calls[0]!;
	call.options.onChild!(child);

	host.cancelControl!();
	controller.cancel();
	call.options.onProgress!(page(2, 5));
	call.finish(result({ code: 6, stderrLast: ["Cancelled after page 1."] }));
	await running;

	assert.deepEqual(aborted, [child]);
	assert.deepEqual(host.progress, [
		'OCR Preview: Converting "case-01" …',
		'OCR Preview: "case-01" is being cancelled …',
	]);
	assert.deepEqual(host.notices, [
		"OCR Preview: Conversion failed (cancelled — partial file created (incomplete)) — Cancelled after page 1..",
	]);
	assert.equal(controller.isRunning, false);
});

test("cancel and dispose without a running conversion do nothing", () => {
	const { host, aborted, controller } = setup();
	controller.cancel();
	controller.dispose();
	assert.deepEqual(aborted, []);
	assert.deepEqual(host.progress, []);
});

test("dispose aborts the running child", async () => {
	const { calls, aborted, controller } = setup();
	const running = controller.run(PDF);
	calls[0]!.options.onChild!(child);

	controller.dispose();
	assert.deepEqual(aborted, [child]);

	calls[0]!.finish(result({ code: null, signal: "SIGTERM" }));
	await running;
});

test("a rejected conversion is reported and the controller is idle again", async () => {
	const host = new FakeHost();
	const controller = new ConversionController(host, {
		convert: () => Promise.reject(new Error("boom")),
		resolveExecutable: () => "/home/test/bin/pdf2md",
	});
	await controller.run(PDF);

	assert.deepEqual(host.notices, ["OCR Preview: Conversion failed — Error: boom."]);
	assert.equal(host.hidden, 1);
	assert.equal(controller.isRunning, false);
});

test("non-zero result is reported through classifyFailure", async () => {
	const { host, calls, controller } = setup();
	const running = controller.run(PDF);
	calls[0]!.finish(result({ code: 1, stderrLast: ["Traceback", "ValueError: bad page"] }));
	await running;

	assert.equal(host.reconciles, 0);
	assert.deepEqual(host.notices, ["OCR Preview: Conversion failed (Code 1) — ValueError: bad page."]);
});

test("classifyFailure maps exit codes, signals, timeouts, and ENOENT", () => {
	const cases: Array<[Partial<ConversionResult>, string, string]> = [
		[{ code: 6 }, "partial-output", "cancelled — partial file created (incomplete)"],
		[{ code: 7 }, "cancelled-before-output", "cancelled — before first page (no partial file)"],
		[
			{ code: null, signal: "SIGKILL", timeout: true },
			"killed",
			"cancelled — force terminated after grace period (SIGKILL)",
		],
		[{ code: null, signal: "SIGTERM", timeout: true }, "timeout", "cancelled — no output for 15 min"],
		[{ code: 6, timeout: true }, "partial-output", "cancelled — partial file created (incomplete)"],
		[{ code: null, signal: "SIGTERM" }, "signal", "cancelled (Signal SIGTERM)"],
		[{ code: null }, "start-error", "Start error"],
		[{ code: 1 }, "exit-code", "Code 1"],
	];
	for (const [overrides, kind, codeText] of cases) {
		const failure = classifyFailure(result(overrides));
		assert.equal(failure.kind, kind, JSON.stringify(overrides));
		assert.equal(failure.message, `OCR Preview: Conversion failed (${codeText}).`);
	}
	const check = classifyFailure(result({ code: 4 }));
	assert.equal(check.kind, "missing-dependency");
	assert.equal(
		check.message,
		"OCR Preview: Conversion failed (Code 4, an installation check failed). Settings → General → Check installation lists every check.",
	);
});

test("classifyFailure detail: last stderr line, else last stdout line without progress arrows", () => {
	assert.equal(
		classifyFailure(result({ code: 1, stderrLast: ["a", "b"], stdoutLast: ["c"] })).message,
		"OCR Preview: Conversion failed (Code 1) — b.",
	);
	assert.equal(
		classifyFailure(result({ code: 1, stdoutLast: ["Analyzing case-01.pdf", "→ p.1: 12.3 s"] })).message,
		"OCR Preview: Conversion failed (Code 1) — Analyzing case-01.pdf.",
	);
	assert.equal(
		classifyFailure(result({ code: 4, stderrLast: ["[fehlt] mlx_vlm: nicht installiert"] })).message,
		"OCR Preview: Conversion failed (Code 4, an installation check failed) — [fehlt] mlx_vlm: nicht installiert. Settings → General → Check installation lists every check.",
	);
	assert.equal(
		classifyFailure(result({ code: null, signal: "SIGTERM", timeout: true }), 60_000).message,
		"OCR Preview: Conversion failed (cancelled — no output for 1 min).",
	);
});

test("classifyFailure: ENOENT on start means pdf2md is missing", () => {
	const failure = classifyFailure(
		result({ code: null, stderrLast: ["Error: spawn /home/test/bin/pdf2md ENOENT"] }),
	);
	assert.equal(failure.kind, "not-found");
	assert.equal(
		failure.message,
		`OCR Preview: Conversion failed (Start error) — pdf2md not found. Install it with setup.sh: ${INSTALL_DOCS_URL}.`,
	);
});

test("resolvePdf2md: prefers ~/bin, then /usr/local/bin, then PATH; falls back to ~/bin", () => {
	const onlyExisting = (...paths: string[]) => (candidate: string) => paths.includes(candidate);

	assert.equal(
		resolvePdf2md("/opt/bin", "/home/test", onlyExisting("/opt/bin/pdf2md", "/home/test/bin/pdf2md")),
		"/home/test/bin/pdf2md",
	);
	assert.equal(
		resolvePdf2md("/opt/bin", "/home/test", onlyExisting("/opt/bin/pdf2md", "/usr/local/bin/pdf2md")),
		"/usr/local/bin/pdf2md",
	);
	assert.equal(
		resolvePdf2md("::/opt/bin:", "/home/test", onlyExisting("/opt/bin/pdf2md")),
		"/opt/bin/pdf2md",
	);
	assert.equal(resolvePdf2md("/opt/bin", "/home/test", onlyExisting()), "/home/test/bin/pdf2md");
});

test("resolveCli: same order for reprocess-raw", () => {
	assert.equal(
		resolveCli("reprocess-raw", "/opt/bin", "/home/test", (c) => c === "/opt/bin/reprocess-raw"),
		"/opt/bin/reprocess-raw",
	);
	assert.equal(resolveCli("reprocess-raw", "", "/home/test", () => false), "/home/test/bin/reprocess-raw");
});

// ── Stage 1: runOcr ────────────────────────────────────────────────────────

test("runOcr: passes paths and options, indeterminate progress, leaves success to the caller", async () => {
	const { host, calls, ocrCalls, controller } = setup();
	const running = controller.runOcr(OCR_REQUEST);

	assert.equal(controller.isRunning, true);
	assert.equal(calls.length, 0);
	assert.equal(ocrCalls.length, 1);
	const call = ocrCalls[0]!;
	assert.deepEqual(call.args, [
		"raw/case-01.pdf",
		"/home/test/bin/reprocess-raw",
		"/vault",
	]);
	assert.equal(call.spawnFn, undefined);
	assert.equal(call.options.engine, "apple");
	assert.equal(call.options.keepOriginal, ".ocr-originals/1/case-01.pdf");
	assert.equal(call.options.allowPages, "1");

	call.options.onChild!(child);
	const done = result({ stdoutLast: ["✅ Overwritten: /vault/raw/case-01.pdf"] });
	call.finish(done);

	assert.deepEqual(await running, { ...done, shortPages: [] });
	assert.deepEqual(host.progress, ['OCR Preview: Adding OCR text layer to "case-01" …']);
	assert.equal(host.hidden, 1);
	assert.deepEqual(host.notices, []);
	// Stage 1 has no Markdown result: no inventory reconciliation.
	assert.equal(host.reconciles, 0);
	assert.equal(controller.isRunning, false);
});

test("runOcr: empty allowed pages are not passed", async () => {
	const { ocrCalls, controller } = setup();
	const running = controller.runOcr({ ...OCR_REQUEST, allowPages: "" });
	assert.equal("allowPages" in ocrCalls[0]!.options, false);
	ocrCalls[0]!.finish(result());
	await running;
});

test("runOcr: failure is reported with the script's ❌ reason", async () => {
	const { host, ocrCalls, controller } = setup();
	const running = controller.runOcr(OCR_REQUEST);
	ocrCalls[0]!.finish(
		result({
			code: 1,
			stdoutLast: [
				"📋 B5 Gate: checking chars/page (min: 50)...",
				"❌ B5 gate failed (see pages above) — /vault/raw/case-01.pdf remains unchanged",
				"No file written: /vault/raw/case-01.pdf remains unchanged",
			],
			stderrLast: ["🗑️  Page 3: only 5 characters (min: 50)"],
		}),
	);

	assert.equal((await running)?.code, 1);
	assert.deepEqual(host.notices, [
		"OCR Preview: OCR text layer failed (Code 1) — B5 gate failed (see pages above) — /vault/raw/case-01.pdf remains unchanged.",
	]);
});

test("runOcr: cancel stops the process group, not the single child", async () => {
	const { host, ocrCalls, aborted, groupAborted, controller } = setup();
	const running = controller.runOcr(OCR_REQUEST);
	ocrCalls[0]!.options.onChild!(child);

	host.cancelControl!();
	controller.cancel();
	ocrCalls[0]!.finish(result({ code: null, signal: "SIGTERM" }));
	await running;

	assert.deepEqual(groupAborted, [child]);
	assert.deepEqual(aborted, []);
	assert.deepEqual(host.progress, [
		'OCR Preview: Adding OCR text layer to "case-01" …',
		'OCR Preview: "case-01" is being cancelled …',
	]);
	assert.deepEqual(host.notices, ['OCR Preview: OCR text layer for "case-01" cancelled — the PDF is unchanged.']);
	assert.equal(controller.isRunning, false);
});

test("runOcr: dispose (plugin unload) stops the process group", async () => {
	const { ocrCalls, aborted, groupAborted, controller } = setup();
	const running = controller.runOcr(OCR_REQUEST);
	ocrCalls[0]!.options.onChild!(child);

	controller.dispose();
	assert.deepEqual(groupAborted, [child]);
	assert.deepEqual(aborted, []);

	ocrCalls[0]!.finish(result({ code: null, signal: "SIGTERM" }));
	await running;
});

test("Stage 2 after Stage 1 is stopped by PID again", async () => {
	const { calls, ocrCalls, aborted, groupAborted, controller } = setup();
	const ocr = controller.runOcr(OCR_REQUEST);
	ocrCalls[0]!.finish(result());
	await ocr;

	const conversion = controller.run(PDF);
	calls[0]!.options.onChild!(child);
	controller.dispose();
	calls[0]!.finish(result({ code: null, signal: "SIGTERM" }));
	await conversion;

	assert.deepEqual(aborted, [child]);
	assert.deepEqual(groupAborted, []);
});

test("runOcr: refused while any conversion runs, and without file-system access", async () => {
	const { host, calls, ocrCalls, controller } = setup();
	const conversion = controller.run(PDF);

	assert.equal(await controller.runOcr(OCR_REQUEST), null);
	assert.equal(ocrCalls.length, 0);
	calls[0]!.finish(result());
	await conversion;

	const ocr = controller.runOcr(OCR_REQUEST);
	await controller.run(PDF);
	assert.equal(calls.length, 1);
	ocrCalls[0]!.finish(result());
	await ocr;
	assert.equal(
		host.notices.filter((n) => n === "OCR Preview: A conversion is already running.").length,
		2,
	);

	const noAccess = new FakeHost();
	noAccess.basePath = null;
	const offline = setup(noAccess);
	assert.equal(await offline.controller.runOcr(OCR_REQUEST), null);
	assert.equal(offline.ocrCalls.length, 0);
	assert.deepEqual(noAccess.notices, ["OCR Preview: An OCR text layer requires file system access (Desktop)."]);
	assert.equal(offline.controller.isRunning, false);
});

test("runOcr: a rejected call is reported and the controller is idle again", async () => {
	const host = new FakeHost();
	const controller = new ConversionController(host, {
		addTextLayer: () => Promise.reject(new Error("boom")),
		resolveReprocessRaw: () => "/home/test/bin/reprocess-raw",
	});

	assert.equal(await controller.runOcr(OCR_REQUEST), null);
	assert.deepEqual(host.notices, ["OCR Preview: OCR text layer failed — Error: boom."]);
	assert.equal(host.hidden, 1);
	assert.equal(controller.isRunning, false);
});

test("classifyOcrFailure maps signals, start errors, ENOENT, and exit codes", () => {
	const cases: Array<[Partial<ConversionResult>, string, string]> = [
		[{ code: null, signal: "SIGKILL" }, "killed", "OCR Preview: OCR text layer failed (force terminated (SIGKILL))."],
		[{ code: null, signal: "SIGTERM" }, "signal", "OCR Preview: OCR text layer failed (terminated (Signal SIGTERM))."],
		[{ code: null, stderrLast: ["Error: spawn EACCES"] }, "start-error", "OCR Preview: OCR text layer failed (Start error) — Error: spawn EACCES."],
		[
			{ code: null, stderrLast: ["Error: spawn /home/test/bin/reprocess-raw ENOENT"] },
			"not-found",
			`OCR Preview: OCR text layer failed (Start error) — reprocess-raw not found. Install it with setup.sh: ${INSTALL_DOCS_URL}.`,
		],
		[{ code: 1, stderrLast: ["Traceback", "ValueError: bad page."] }, "exit-code", "OCR Preview: OCR text layer failed (Code 1) — ValueError: bad page."],
		[{ code: 2 }, "exit-code", "OCR Preview: OCR text layer failed (Code 2)."],
	];
	for (const [overrides, kind, message] of cases) {
		const failure = classifyOcrFailure(result(overrides));
		assert.equal(failure.kind, kind, JSON.stringify(overrides));
		assert.equal(failure.message, message);
	}
});

test("runOcr: a B5 failure with short pages is left to the caller", async () => {
	const { host, ocrCalls, controller } = setup();
	const running = controller.runOcr(OCR_REQUEST);
	ocrCalls[0]!.finish({ ...result({ code: 1 }), shortPages: [1, 5] });

	const failed = await running;
	assert.deepEqual(failed?.shortPages, [1, 5]);
	assert.deepEqual(host.notices, []);
	assert.equal(controller.isRunning, false);
});

test("runOcr: a cancelled run reports cancellation and drops short pages", async () => {
	const { host, ocrCalls, controller } = setup();
	const running = controller.runOcr(OCR_REQUEST);
	ocrCalls[0]!.options.onChild!(child);
	controller.cancel();
	ocrCalls[0]!.finish({ ...result({ code: 1 }), shortPages: [2] });

	assert.deepEqual((await running)?.shortPages, []);
	assert.deepEqual(host.notices, ['OCR Preview: OCR text layer for "case-01" cancelled — the PDF is unchanged.']);
});

test("findCli: null when no candidate exists; resolveCli then falls back to ~/bin", () => {
	const none = () => false;
	assert.equal(findCli("pdf2md", "/opt/a", "/home/test", none), null);
	assert.equal(resolveCli("pdf2md", "/opt/a", "/home/test", none), "/home/test/bin/pdf2md");
	assert.equal(
		findCli("pdf2md", "/opt/a", "/home/test", (c) => c === "/opt/a/pdf2md"),
		"/opt/a/pdf2md",
	);
});

test("commandPathProblem: relative, missing, directory, not executable, executable", () => {
	const dir = mkdtempSync(join(tmpdir(), "ocr-cli-"));
	try {
		const plain = join(dir, "plain");
		const runnable = join(dir, "pdf2md");
		writeFileSync(plain, "");
		writeFileSync(runnable, "#!/bin/sh\n");
		chmodSync(runnable, 0o755);
		assert.equal(commandPathProblem("~/bin/pdf2md"), "Enter a full path, starting with /");
		assert.equal(commandPathProblem(join(dir, "missing")), "No file at this path");
		assert.equal(commandPathProblem(dir), "This is not a file");
		assert.equal(commandPathProblem(plain), "This file is not executable");
		assert.equal(commandPathProblem(runnable), null);
	} finally {
		rmSync(dir, { recursive: true, force: true });
	}
});

test("pdf2mdExecutable: a configured path wins over the search", () => {
	assert.equal(pdf2mdExecutable("/opt/pdf2md"), "/opt/pdf2md");
	assert.equal(pdf2mdExecutable(""), resolveCli("pdf2md"));
});
