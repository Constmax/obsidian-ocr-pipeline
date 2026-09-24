import assert from "node:assert/strict";
import { test } from "node:test";
import type { ChildProcess } from "node:child_process";

import {
	CONVERSION_IDLE_TIMEOUT_MS,
	INDEX_WAIT_MS,
	INDEX_WAIT_STEPS,
	ConversionController,
	classifyFailure,
	classifyOcrFailure,
	resolveCli,
	resolvePdf2md,
	type ConversionHost,
	type ConvertFunction,
	type PdfSource,
	type ProgressDisplay,
	type SearchableCopyFunction,
	type SearchableCopyRequest,
} from "../src/conversion-controller.ts";
import type {
	ConversionOptions,
	ConversionResult,
	ProgressEvent,
	SearchableCopyOptions,
} from "../src/conversion.ts";

const PDF: PdfSource = { path: "raw/case-01.pdf", basename: "case-01" };

class FakeHost implements ConversionHost {
	notices: string[] = [];
	progress: string[] = [];
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

	notify(message: string): void {
		this.notices.push(message);
	}
	showProgress(message: string, onCancel: () => void): ProgressDisplay {
		this.progress.push(message);
		this.cancelControl = onCancel;
		return {
			setMessage: (text) => {
				this.progress.push(text);
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
	args: [string, string, string, string];
	spawnFn: unknown;
	options: SearchableCopyOptions;
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
	const searchableCopy: SearchableCopyFunction = (source, destination, cli, cwd, spawnFn, options = {}) =>
		new Promise((resolve) => {
			ocrCalls.push({
				args: [source, destination, cli, cwd],
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
		searchableCopy,
		abortGroup: (child) => {
			groupAborted.push(child);
		},
		resolveReprocessRaw: () => "/home/test/bin/reprocess-raw",
	});
	return { host, calls, ocrCalls, aborted, groupAborted, controller };
}

const OCR_REQUEST: SearchableCopyRequest = {
	source: PDF,
	destination: "raw/case-01-ocr.pdf",
	engine: "apple",
	splitColumns: true,
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
		'OCR Preview: Converting "case-01" — page 1 of 3 …',
		'OCR Preview: Converting "case-01" — page 2 of 3 (derailed) …',
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
		[{ code: 4 }, "missing-dependency", "Code 4"],
		[{ code: 1 }, "exit-code", "Code 1"],
	];
	for (const [overrides, kind, codeText] of cases) {
		const failure = classifyFailure(result(overrides));
		assert.equal(failure.kind, kind, JSON.stringify(overrides));
		assert.equal(failure.message, `OCR Preview: Conversion failed (${codeText}).`);
	}
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
		"OCR Preview: Conversion failed (Code 4) — [fehlt] mlx_vlm: nicht installiert.",
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
	// The doubled period is the message as main.ts produced it before #53.
	assert.equal(
		failure.message,
		"OCR Preview: Conversion failed (Start error) — pdf2md not found. Please run setup.sh in repo..",
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
		"raw/case-01-ocr.pdf",
		"/home/test/bin/reprocess-raw",
		"/vault",
	]);
	assert.equal(call.spawnFn, undefined);
	assert.equal(call.options.engine, "apple");
	assert.equal(call.options.splitColumns, true);
	assert.equal(call.options.allowPages, "1");

	call.options.onChild!(child);
	const done = result({ stdoutLast: ["✅ Written: /vault/raw/case-01-ocr.pdf"] });
	call.finish(done);

	assert.deepEqual(await running, { ...done, shortPages: [] });
	assert.deepEqual(host.progress, ['OCR Preview: Creating searchable copy of "case-01" …']);
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
				"No file written: /vault/raw/case-01-ocr.pdf",
			],
			stderrLast: ["🗑️  Page 3: only 5 characters (min: 50)"],
		}),
	);

	assert.equal((await running)?.code, 1);
	assert.deepEqual(host.notices, [
		"OCR Preview: Searchable copy failed (Code 1) — B5 gate failed (see pages above) — /vault/raw/case-01.pdf remains unchanged.",
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
		'OCR Preview: Creating searchable copy of "case-01" …',
		'OCR Preview: "case-01" is being cancelled …',
	]);
	assert.deepEqual(host.notices, ['OCR Preview: Searchable copy of "case-01" cancelled — no file written.']);
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
	assert.deepEqual(noAccess.notices, ["OCR Preview: A searchable copy requires file system access (Desktop)."]);
	assert.equal(offline.controller.isRunning, false);
});

test("runOcr: a rejected call is reported and the controller is idle again", async () => {
	const host = new FakeHost();
	const controller = new ConversionController(host, {
		searchableCopy: () => Promise.reject(new Error("boom")),
		resolveReprocessRaw: () => "/home/test/bin/reprocess-raw",
	});

	assert.equal(await controller.runOcr(OCR_REQUEST), null);
	assert.deepEqual(host.notices, ["OCR Preview: Searchable copy failed — Error: boom."]);
	assert.equal(host.hidden, 1);
	assert.equal(controller.isRunning, false);
});

test("classifyOcrFailure maps signals, start errors, ENOENT, and exit codes", () => {
	const cases: Array<[Partial<ConversionResult>, string, string]> = [
		[{ code: null, signal: "SIGKILL" }, "killed", "OCR Preview: Searchable copy failed (force terminated (SIGKILL))."],
		[{ code: null, signal: "SIGTERM" }, "signal", "OCR Preview: Searchable copy failed (terminated (Signal SIGTERM))."],
		[{ code: null, stderrLast: ["Error: spawn EACCES"] }, "start-error", "OCR Preview: Searchable copy failed (Start error) — Error: spawn EACCES."],
		[
			{ code: null, stderrLast: ["Error: spawn /home/test/bin/reprocess-raw ENOENT"] },
			"not-found",
			"OCR Preview: Searchable copy failed (Start error) — reprocess-raw not found. Please run setup.sh in repo.",
		],
		[{ code: 1, stderrLast: ["Traceback", "ValueError: bad page."] }, "exit-code", "OCR Preview: Searchable copy failed (Code 1) — ValueError: bad page."],
		[{ code: 2 }, "exit-code", "OCR Preview: Searchable copy failed (Code 2)."],
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
	assert.deepEqual(host.notices, ['OCR Preview: Searchable copy of "case-01" cancelled — no file written.']);
});
