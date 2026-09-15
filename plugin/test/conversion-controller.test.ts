import assert from "node:assert/strict";
import { test } from "node:test";
import type { ChildProcess } from "node:child_process";

import {
	CONVERSION_TIMEOUT_MS,
	INDEX_WAIT_MS,
	INDEX_WAIT_STEPS,
	ConversionController,
	classifyFailure,
	resolvePdf2md,
	type ConversionHost,
	type ConvertFunction,
	type PdfSource,
	type ProgressDisplay,
} from "../src/conversion-controller.ts";
import type {
	ConversionOptions,
	ConversionResult,
	ProgressEvent,
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

function setup(host = new FakeHost()) {
	const calls: ConvertCall[] = [];
	const aborted: ChildProcess[] = [];
	const convert: ConvertFunction = (pdf, out, pdf2md, cwd, spawnFn, options = {}) =>
		new Promise((finish) => {
			calls.push({ args: [pdf, out, pdf2md, cwd], spawnFn, options, finish });
		});
	const controller = new ConversionController(host, {
		convert,
		abort: (child) => {
			aborted.push(child);
		},
		resolveExecutable: () => "/home/test/bin/pdf2md",
	});
	return { host, calls, aborted, controller };
}

const child = { pid: 42 } as unknown as ChildProcess;

test("success: passes paths, timeout and pages, reports page progress, opens the preview", async () => {
	const { host, calls, controller } = setup();
	const running = controller.run(PDF, "1-3");

	assert.equal(controller.isRunning, true);
	assert.equal(calls.length, 1);
	const call = calls[0]!;
	assert.deepEqual(call.args, ["raw/case-01.pdf", "_ocr-preview", "/home/test/bin/pdf2md", "/vault"]);
	assert.equal(call.spawnFn, undefined);
	assert.equal(call.options.timeoutMs, CONVERSION_TIMEOUT_MS);
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

test("duplicate basename stops before spawning", async () => {
	const host = new FakeHost();
	host.duplicates = ["other/case-01.pdf", "old/case-01.pdf"];
	const { calls, controller } = setup(host);
	await controller.run(PDF);

	assert.equal(calls.length, 0);
	assert.deepEqual(host.progress, []);
	assert.deepEqual(host.notices, [
		'OCR Preview: "case-01" also exists as other/case-01.pdf, old/case-01.pdf — output would overwrite. Please rename one of the files.',
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
		[{ code: null, signal: "SIGTERM", timeout: true }, "timeout", "cancelled after 30 min"],
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
		"OCR Preview: Conversion failed (cancelled after 1 min).",
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
