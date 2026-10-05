import { EventEmitter } from "node:events";
import assert from "node:assert/strict";
import { test } from "node:test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
	convertPdf,
	abortChild,
	checkEngine,
	addTextLayer,
	checkPdf2md,
	stage1Path,
	terminateProcessGroup,
	type ConversionResult,
	type SpawnFunction,
} from "../src/conversion.ts";

class FakeChild extends EventEmitter {
	stdout = Object.assign(new EventEmitter(), { setEncoding: () => {} });
	stderr = Object.assign(new EventEmitter(), { setEncoding: () => {} });
	exitCode: number | null = null;
	signalCode: NodeJS.Signals | null = null;
	signals: NodeJS.Signals[] = [];
	exitOnSigterm?: number;
	/** Which SIGTERM makes the child exit with `exitOnSigterm`. */
	sigtermsToExit = 1;
	kill = (signal?: NodeJS.Signals) => {
		this.signals.push(signal ?? "SIGTERM");
		const sigterms = this.signals.filter((sent) => sent === "SIGTERM").length;
		if (signal === "SIGKILL") {
			this.signalCode = "SIGKILL";
			this.emit("exit", null, "SIGKILL");
			this.emit("close", null, "SIGKILL");
		} else if (this.exitOnSigterm !== undefined && sigterms >= this.sigtermsToExit) {
			this.exitCode = this.exitOnSigterm;
			this.emit("exit", this.exitOnSigterm, null);
			this.emit("close", this.exitOnSigterm, null);
		}
		return true;
	};
}

function spawnMock(
	calls: Array<{ command: string; args: string[]; options: unknown }>,
	child: FakeChild,
): SpawnFunction {
	return (command: string, args: readonly string[], options?: object) => {
		calls.push({ command, args: [...args], options });
		return child as unknown as ReturnType<SpawnFunction>;
	};
}

test("calls pdf2md with --out and cwd and returns exit code 0 with lines", async () => {
	const calls: Array<{ command: string; args: string[]; options: unknown }> = [];
	const child = new FakeChild();
	const promise = convertPdf(
		"raw/case-01.pdf",
		"_ocr-preview",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnMock(calls, child),
	);

	child.stdout.emit("data", "Analyzing case-01.pdf (scan pages @ 150 dpi) ...\n");
	child.stdout.emit("data", "→ p.1: 12.3 s | 100 l. Textlayer → OCR\n");
	child.stderr.emit("data", "\n");
	child.emit("close", 0);

	const result = await promise;
	assert.deepEqual(calls, [
		{
			command: "/Users/test/bin/pdf2md",
			args: ["raw/case-01.pdf", "--out", "_ocr-preview", "--fortschritt"],
			options: { cwd: "/vault", stdio: ["ignore", "pipe", "pipe"] },
		},
	]);
	assert.equal(result.code, 0);
	assert.equal(result.signal, null);
	assert.equal(result.timeout, false);
	assert.deepEqual(result.stdoutLast, [
		"Analyzing case-01.pdf (scan pages @ 150 dpi) ...",
		"→ p.1: 12.3 s | 100 l. Textlayer → OCR",
	]);
	assert.deepEqual(result.stderrLast, []);
});

test("line spread across two data events counts as one", async () => {
	const child = new FakeChild();
	const promise = convertPdf(
		"raw/case-01.pdf",
		"_ocr-preview",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnMock([], child),
	);

	child.stdout.emit("data", "→ p.1: 12.3 s | 100 l. Text");
	child.stdout.emit("data", "layer → OCR\n");
	child.stdout.emit("data", "last line without newline");
	child.emit("close", 0);

	const result = await promise;
	assert.deepEqual(result.stdoutLast, [
		"→ p.1: 12.3 s | 100 l. Textlayer → OCR",
		"last line without newline",
	]);
});

test("exit code non-zero: stderr lines returned, only last 5", async () => {
	const child = new FakeChild();
	const promise = convertPdf(
		"raw/broken.pdf",
		"_ocr-preview",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnMock([], child),
	);

	child.stdout.emit("data", "something\n");
	for (let i = 1; i <= 7; i++) {
		child.stderr.emit("data", `Line ${i}\n`);
	}
	child.emit("close", 1);

	const result = await promise;
	assert.equal(result.code, 1);
	assert.deepEqual(result.stderrLast, ["Line 3", "Line 4", "Line 5", "Line 6", "Line 7"]);
});

test("error on start (child emits 'error') gives null code", async () => {
	const child = new FakeChild();
	const promise = convertPdf(
		"raw/x.pdf",
		"_ocr-preview",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnMock([], child),
	);

	child.emit("error", new Error("ENOENT: pdf2md missing"));

	const result = await promise;
	assert.equal(result.code, null);
	assert.equal(result.signal, null);
	assert.equal(result.timeout, false);
	assert.ok(
		result.stderrLast.some((z) => z.includes("ENOENT")),
		`expected ENOENT message, got: ${JSON.stringify(result.stderrLast)}`,
	);
});

test("spawnFn throws synchronously: null code, message in stderrLast", async () => {
	const result: ConversionResult = await convertPdf(
		"raw/x.pdf",
		"_ocr-preview",
		"/Users/test/bin/pdf2md",
		"/vault",
		() => {
			throw new Error("spawn not available");
		},
	);
	assert.equal(result.code, null);
	assert.deepEqual(result.stderrLast, ["Error: spawn not available"]);
});

test("onChild reports child process", async () => {
	const child = new FakeChild();
	let reported: unknown = null;
	const promise = convertPdf(
		"raw/case-01.pdf",
		"_ocr-preview",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnMock([], child),
		{
			onChild: (k) => {
				reported = k;
			},
		},
	);

	child.emit("close", 0);
	await promise;
	assert.equal(reported, child);
});

test("timeout: hanging child gets SIGTERM twice, then SIGKILL, timeout: true", async () => {
	const child = new FakeChild();
	const promise = convertPdf(
		"raw/hanging.pdf",
		"_ocr-preview",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnMock([], child),
		{ idleTimeoutMs: 20, gracePeriodMs: 30, killDelayMs: 30 },
	);

	child.stdout.emit("data", "→ p.1: 12.3 s\n");

	const result = await promise;
	assert.deepEqual(child.signals, ["SIGTERM", "SIGTERM", "SIGKILL"]);
	assert.equal(result.timeout, true);
	assert.equal(result.code, null);
	assert.equal(result.signal, "SIGKILL");
	assert.deepEqual(result.stdoutLast, ["→ p.1: 12.3 s"]);
});

test("timeout: child exits cleanly on SIGTERM (code 6), no SIGKILL", async () => {
	const child = new FakeChild();
	child.exitOnSigterm = 6;
	const promise = convertPdf(
		"raw/hanging.pdf",
		"_ocr-preview",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnMock([], child),
		{ idleTimeoutMs: 20, gracePeriodMs: 200 },
	);

	const result = await promise;
	assert.deepEqual(child.signals, ["SIGTERM"]);
	assert.equal(result.timeout, true);
	assert.equal(result.code, 6);
	assert.equal(result.signal, null);
});

test("abortChild: hanging child gets a second SIGTERM after the grace period, then SIGKILL", async () => {
	const child = new FakeChild();

	abortChild(child as unknown as ChildProcess, 20, 40);
	assert.deepEqual(child.signals, ["SIGTERM"]);

	await new Promise((done) => setTimeout(done, 35));
	assert.deepEqual(child.signals, ["SIGTERM", "SIGTERM"]);

	await new Promise((done) => setTimeout(done, 50));
	assert.deepEqual(child.signals, ["SIGTERM", "SIGTERM", "SIGKILL"]);
});

test("abortChild: a child writing its partial file on the second SIGTERM gets no SIGKILL", async () => {
	// Issue #105: pdf2md stops the current page on the second signal and
	// exits 6 with the pages before it; SIGKILL would lose that file.
	const child = new FakeChild();
	child.exitOnSigterm = 6;
	child.sigtermsToExit = 2;

	abortChild(child as unknown as ChildProcess, 20, 20);
	await new Promise((done) => setTimeout(done, 80));

	assert.deepEqual(child.signals, ["SIGTERM", "SIGTERM"]);
	assert.equal(child.exitCode, 6);
});

test("idle timeout: a long run is not stopped while it keeps writing", async () => {
	// Issue #105: a 60-page document runs far past any fixed limit; only
	// silence means the child hangs.
	const child = new FakeChild();
	const promise = convertPdf(
		"raw/long.pdf",
		"_ocr-preview",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnMock([], child),
		{ idleTimeoutMs: 40 },
	);

	for (let page = 1; page <= 60; page++) {
		await new Promise((done) => setTimeout(done, 5));
		child.stderr.emit(
			"data",
			`{"typ": "seite", "nr": ${page}, "von": 60, "sekunden": 55.0, "herkunft": "ocr", "entgleist": false}\n`,
		);
	}
	child.emit("close", 0, null);

	const result = await promise;
	assert.equal(result.timeout, false);
	assert.deepEqual(child.signals, []);
});

test("abortChild: exited child receives no signal", () => {
	const child = new FakeChild();
	child.exitCode = 6;

	abortChild(child as unknown as ChildProcess, 20);
	assert.deepEqual(child.signals, []);
});

test("process terminated by signal: signal reported", async () => {
	const child = new FakeChild();
	const promise = convertPdf(
		"raw/case-01.pdf",
		"_ocr-preview",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnMock([], child),
	);

	child.emit("close", null, "SIGTERM");

	const result = await promise;
	assert.equal(result.code, null);
	assert.equal(result.signal, "SIGTERM");
});

test("calls pdf2md with --seiten when pages set", async () => {
	const calls: Array<{ command: string; args: string[]; options: unknown }> = [];
	const child = new FakeChild();
	const promise = convertPdf(
		"raw/case-01.pdf",
		"_ocr-preview",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnMock(calls, child),
		{ pages: "1,3-5" },
	);

	child.emit("close", 0);
	await promise;
	assert.deepEqual(calls[0]!.args, [
		"raw/case-01.pdf",
		"--out",
		"_ocr-preview",
		"--seiten",
		"1,3-5",
		"--fortschritt",
	]);
});

test("without pages: no --seiten in args", async () => {
	const calls: Array<{ command: string; args: string[]; options: unknown }> = [];
	const child = new FakeChild();
	const promise = convertPdf(
		"raw/case-01.pdf",
		"_ocr-preview",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnMock(calls, child),
	);

	child.emit("close", 0);
	await promise;
	assert.deepEqual(calls[0]!.args, [
		"raw/case-01.pdf",
		"--out",
		"_ocr-preview",
		"--fortschritt",
	]);
});

test("empty pages string: no --seiten in args", async () => {
	const calls: Array<{ command: string; args: string[]; options: unknown }> = [];
	const child = new FakeChild();
	const promise = convertPdf(
		"raw/case-01.pdf",
		"_ocr-preview",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnMock(calls, child),
		{ pages: "" },
	);

	child.emit("close", 0);
	await promise;
	assert.deepEqual(calls[0]!.args, [
		"raw/case-01.pdf",
		"--out",
		"_ocr-preview",
		"--fortschritt",
	]);
});

// ── Stage 1: addTextLayer ──────────────────────────────────────────

test("text layer: exact arguments, own process group, extended PATH", async () => {
	const calls: Array<{ command: string; args: string[]; options: unknown }> = [];
	const child = new FakeChild();
	let reported: unknown = null;
	// A launchd-like PATH, so the expectation does not depend on the shell running the tests.
	const savedPath = process.env.PATH;
	process.env.PATH = "/usr/bin:/bin";
	const promise = addTextLayer(
		"raw/case-01.pdf",
		"/Users/test/bin/reprocess-raw",
		"/vault",
		spawnMock(calls, child),
		{
			engine: "tesseract",
			keepOriginal: ".ocr-originals/1/case-01.pdf",
			allowPages: "1,5-7",
			onChild: (k) => {
				reported = k;
			},
		},
	);
	process.env.PATH = savedPath;

	child.stdout.emit("data", "✅ Overwritten: /vault/raw/case-01.pdf\n");
	child.emit("close", 0);
	const result = await promise;

	assert.equal(calls.length, 1);
	const { command, args, options } = calls[0]! as {
		command: string;
		args: string[];
		options: { cwd: string; stdio: string[]; detached: boolean; env: NodeJS.ProcessEnv };
	};
	assert.equal(command, "/Users/test/bin/reprocess-raw");
	assert.deepEqual(args, [
		"raw/case-01.pdf",
		"--in-place",
		"--engine",
		"tesseract",
		"--keep-original",
		".ocr-originals/1/case-01.pdf",
		"--allow-pages",
		"1,5-7",
	]);
	// Existing text is preserved: --force-ocr is never passed.
	assert.equal(args.includes("--force-ocr"), false);
	assert.equal(options.cwd, "/vault");
	assert.deepEqual(options.stdio, ["ignore", "pipe", "pipe"]);
	assert.equal(options.detached, true);
	assert.equal(options.env.PATH, stage1Path("/usr/bin:/bin"));
	assert.notEqual(options.env.PATH, "/usr/bin:/bin");
	assert.equal(reported, child);
	assert.equal(result.code, 0);
	assert.deepEqual(result.stdoutLast, ["✅ Overwritten: /vault/raw/case-01.pdf"]);
});

test("text layer: defaults pass only the source and --in-place", async () => {
	const calls: Array<{ command: string; args: string[]; options: unknown }> = [];
	const child = new FakeChild();
	const promise = addTextLayer(
		"raw/case-01.pdf",
		"/Users/test/bin/reprocess-raw",
		"/vault",
		spawnMock(calls, child),
		{ allowPages: "" },
	);

	child.emit("close", 0);
	await promise;
	assert.deepEqual(calls[0]!.args, ["raw/case-01.pdf", "--in-place"]);
});

test("text layer: PaddleOCR always runs in fast mode (issue #73)", async () => {
	const calls: Array<{ command: string; args: string[]; options: unknown }> = [];
	const child = new FakeChild();
	const promise = addTextLayer(
		"raw/case-01.pdf",
		"/Users/test/bin/reprocess-raw",
		"/vault",
		spawnMock(calls, child),
		{ engine: "paddle" },
	);

	child.emit("close", 0);
	await promise;
	assert.deepEqual(calls[0]!.args, [
		"raw/case-01.pdf",
		"--in-place",
		"--engine",
		"paddle",
		"--paddle-mode",
		"fast",
	]);
});

test("text layer: spawn errors and ordinary failure are results, not exceptions", async () => {
	const thrown = await addTextLayer("a.pdf", "/x/reprocess-raw", "/vault", () => {
		throw new Error("spawn not available");
	});
	assert.equal(thrown.code, null);
	assert.deepEqual(thrown.stderrLast, ["Error: spawn not available"]);

	const missing = new FakeChild();
	const missingRun = addTextLayer("a.pdf", "/x/reprocess-raw", "/vault", spawnMock([], missing));
	missing.emit("error", new Error("spawn /x/reprocess-raw ENOENT"));
	const missingResult = await missingRun;
	assert.equal(missingResult.code, null);
	assert.deepEqual(missingResult.stderrLast, ["Error: spawn /x/reprocess-raw ENOENT"]);

	const failing = new FakeChild();
	const failingRun = addTextLayer("a.pdf", "/x/reprocess-raw", "/vault", spawnMock([], failing));
	failing.stdout.emit("data", "❌ Source changed during processing, not replacing it: /vault/a.pdf\n");
	failing.emit("close", 1);
	const failingResult = await failingRun;
	assert.equal(failingResult.code, 1);
	assert.deepEqual(failingResult.stdoutLast, ["❌ Source changed during processing, not replacing it: /vault/a.pdf"]);
});

test("stage1Path: adds missing tool folders first and system folders last", () => {
	assert.equal(
		stage1Path("/usr/bin:/bin:/usr/sbin:/sbin", "/Users/test"),
		"/Users/test/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
	);
	assert.equal(
		stage1Path("/opt/homebrew/bin:/custom::/usr/bin", "/Users/test"),
		"/Users/test/bin:/usr/local/bin:/opt/homebrew/bin:/custom:/usr/bin:/bin:/usr/sbin:/sbin",
	);
	assert.equal(
		stage1Path("", "/Users/test"),
		"/Users/test/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
	);
});

// ── Stage 1: terminateProcessGroup ─────────────────────────────────────────

/** Records signals; the group answers probes until `aliveProbes` is used up. */
function groupSignals(aliveProbes: number, goneOnTerm = false) {
	const sent: Array<[number, NodeJS.Signals | 0]> = [];
	let probes = aliveProbes;
	const signal = (pid: number, sig: NodeJS.Signals | 0) => {
		sent.push([pid, sig]);
		const gone = (sig === "SIGTERM" && goneOnTerm) || (sig === 0 && probes-- <= 0);
		if (gone) throw Object.assign(new Error("kill ESRCH"), { code: "ESRCH" });
	};
	return { sent, signal };
}

const groupLeader = { pid: 4242 } as unknown as ChildProcess;

test("terminateProcessGroup: group that exits after SIGTERM gets no SIGKILL", async () => {
	const { sent, signal } = groupSignals(1);
	await terminateProcessGroup(groupLeader, 1000, signal);

	assert.deepEqual(sent, [
		[-4242, "SIGTERM"],
		[-4242, 0],
		[-4242, 0],
	]);
});

test("terminateProcessGroup: remaining member is escalated to SIGKILL after the grace period", async () => {
	const { sent, signal } = groupSignals(Number.POSITIVE_INFINITY);
	const started = Date.now();
	await terminateProcessGroup(groupLeader, 60, signal);

	assert.ok(Date.now() - started >= 55, "SIGKILL must wait for the grace period");
	assert.deepEqual(sent[0], [-4242, "SIGTERM"]);
	assert.deepEqual(sent[sent.length - 1], [-4242, "SIGKILL"]);
	assert.ok(sent.slice(1, -1).every(([, sig]) => sig === 0));
});

test("terminateProcessGroup: an already exited group is cleaned up, not an error", async () => {
	const { sent, signal } = groupSignals(0, true);
	await terminateProcessGroup(groupLeader, 1000, signal);
	assert.deepEqual(sent, [[-4242, "SIGTERM"]]);
});

test("terminateProcessGroup: a child that never started sends nothing", async () => {
	const { sent, signal } = groupSignals(0);
	await terminateProcessGroup({ pid: undefined } as unknown as ChildProcess, 1000, signal);
	assert.deepEqual(sent, []);
});

function exists(pid: number): boolean {
	try {
		process.kill(pid, 0);
		return true;
	} catch {
		return false;
	}
}

function readPids(file: string): number[] {
	try {
		return readFileSync(file, "utf8").trim().split("\n").filter(Boolean).map(Number);
	} catch {
		return [];
	}
}

async function until(condition: () => boolean, ms: number): Promise<boolean> {
	const deadline = Date.now() + ms;
	while (!condition()) {
		if (Date.now() > deadline) return false;
		await new Promise((done) => setTimeout(done, 20));
	}
	return true;
}

test(
	"process tree: cancellation leaves no descendant, even one that ignores SIGTERM",
	{ skip: process.platform === "win32" },
	async () => {
		const dir = mkdtempSync(join(tmpdir(), "ocr-process-tree-"));
		const pidFile = join(dir, "pids");
		const fixture = new URL("./fixtures/process-tree.sh", import.meta.url).pathname;
		try {
			let leader: ChildProcess | null = null;
			const running = addTextLayer(pidFile, fixture, dir, spawn, {
				onChild: (child) => {
					leader = child;
				},
			});
			const pids = () => readPids(pidFile);
			assert.ok(await until(() => pids().length === 5, 5000), "fixture did not start its descendants");
			const pgid = (leader as ChildProcess | null)?.pid;
			assert.ok(pgid !== undefined);
			assert.ok(pids().every(exists));

			await terminateProcessGroup(leader!, 300);

			// Check before awaiting the result: a surviving descendant keeps the
			// output pipes open, so the result would only arrive once it exits.
			const groupGone = await until(() => !exists(-pgid), 2000);
			assert.ok(groupGone, "process group still has members");
			assert.deepEqual(pids().filter(exists), [], "descendants survived cancellation");
			await running;
		} finally {
			// A failed assertion must not leave the fixture's sleepers running.
			for (const pid of readPids(pidFile).filter(exists)) {
				try {
					process.kill(pid, "SIGKILL");
				} catch {
					// Exited in the meantime.
				}
			}
			rmSync(dir, { recursive: true, force: true });
		}
	},
);

// ── Stage 1: checkEngine (issue #73) ───────────────────────────────────────

test("engine check: usable engine resolves to null", async () => {
	const calls: Array<{ command: string; args: string[]; options: unknown }> = [];
	const child = new FakeChild();
	const promise = checkEngine("paddle", "/Users/test/bin/reprocess-raw", "/vault", spawnMock(calls, child));

	child.stdout.emit("data", "✅ Engine usable\n   🧠 Engine:    PaddleOCR fast (manual)\n");
	child.emit("close", 0);
	assert.equal(await promise, null);
	assert.equal(calls[0]!.command, "/Users/test/bin/reprocess-raw");
	assert.deepEqual(calls[0]!.args, ["--check-engine", "--engine", "paddle", "--paddle-mode", "fast"]);
	const options = calls[0]!.options as { cwd: string; env: NodeJS.ProcessEnv };
	assert.equal(options.cwd, "/vault");
	assert.equal(options.env.PATH, stage1Path(process.env.PATH ?? ""));
});

test("engine check: a failed check returns the CLI's reason", async () => {
	const child = new FakeChild();
	const promise = checkEngine("paddle", "/x/reprocess-raw", "/vault", spawnMock([], child));

	child.stdout.emit("data", "🔍 Checking dependencies...\n");
	child.stderr.emit("data", "❌ PaddleOCR engine is not ready:\n  model file missing: /m/x.onnx\n");
	child.emit("close", 4);
	assert.equal(await promise, "PaddleOCR engine is not ready: model file missing: /m/x.onnx");
});

test("engine check: a CLI that cannot start is a reason too", async () => {
	const child = new FakeChild();
	const promise = checkEngine("paddle", "/x/reprocess-raw", "/vault", spawnMock([], child));

	child.emit("error", new Error("spawn /x/reprocess-raw ENOENT"));
	assert.equal(await promise, "reprocess-raw not found (/x/reprocess-raw)");
	const silent = new FakeChild();
	const quiet = checkEngine("apple", "/x/reprocess-raw", "/vault", spawnMock([], silent));
	silent.emit("close", 1);
	assert.equal(await quiet, "reprocess-raw --check-engine failed (exit code 1)");
});

test("pdf2md check: runs against the preview folder and keeps every line", async () => {
	const calls: Array<{ command: string; args: string[]; options: object }> = [];
	const child = new FakeChild();
	const promise = checkPdf2md("/x/pdf2md", "_ocr-preview", "/vault", spawnMock(calls, child));

	child.stdout.emit("data", "[ ok ] python: 3.12\n[fehlt] mlx_vlm: nicht installiert\n\n—\n");
	child.stderr.emit("data", "Hinweis: Modell fehlt\n");
	child.emit("close", 4);
	assert.deepEqual(await promise, {
		code: 4,
		lines: ["[ ok ] python: 3.12", "[fehlt] mlx_vlm: nicht installiert", "—", "Hinweis: Modell fehlt"],
	});
	assert.equal(calls[0]!.command, "/x/pdf2md");
	assert.deepEqual(calls[0]!.args, ["--check", "--out", "_ocr-preview"]);
	assert.equal((calls[0]!.options as { cwd: string }).cwd, "/vault");
});

test("pdf2md check: a missing pdf2md names its path", async () => {
	const child = new FakeChild();
	const promise = checkPdf2md("/x/pdf2md", "_ocr-preview", "/vault", spawnMock([], child));

	child.emit("error", new Error("spawn /x/pdf2md ENOENT"));
	assert.deepEqual(await promise, { code: null, lines: ["pdf2md not found (/x/pdf2md)"] });
});

test("pdf2md check: a stopped check says so once", async () => {
	const child = new FakeChild();
	const promise = checkPdf2md("/x/pdf2md", "_ocr-preview", "/vault", spawnMock([], child));

	child.stderr.emit("data", "Traceback\n");
	child.emit("close", null, "SIGTERM");
	assert.deepEqual(await promise, { code: null, lines: ["Traceback", "pdf2md --check stopped (SIGTERM)"] });
});
