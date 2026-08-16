import { EventEmitter } from "node:events";
import assert from "node:assert/strict";
import { test } from "node:test";
import type { ChildProcess } from "node:child_process";

import {
	convertPdf,
	abortChild,
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
	kill = (signal?: NodeJS.Signals) => {
		this.signals.push(signal ?? "SIGTERM");
		if (signal === "SIGKILL") {
			this.signalCode = "SIGKILL";
			this.emit("exit", null, "SIGKILL");
			this.emit("close", null, "SIGKILL");
		} else if (this.exitOnSigterm !== undefined) {
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

test("timeout: hanging child gets SIGTERM, after grace period SIGKILL, timeout: true", async () => {
	const child = new FakeChild();
	const promise = convertPdf(
		"raw/hanging.pdf",
		"_ocr-preview",
		"/Users/test/bin/pdf2md",
		"/vault",
		spawnMock([], child),
		{ timeoutMs: 20, gracePeriodMs: 30 },
	);

	child.stdout.emit("data", "→ p.1: 12.3 s\n");

	const result = await promise;
	assert.deepEqual(child.signals, ["SIGTERM", "SIGKILL"]);
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
		{ timeoutMs: 20, gracePeriodMs: 200 },
	);

	const result = await promise;
	assert.deepEqual(child.signals, ["SIGTERM"]);
	assert.equal(result.timeout, true);
	assert.equal(result.code, 6);
	assert.equal(result.signal, null);
});

test("abortChild: hanging child gets SIGTERM, after grace period SIGKILL", async () => {
	const child = new FakeChild();

	abortChild(child as unknown as ChildProcess, 20);
	assert.deepEqual(child.signals, ["SIGTERM"]);

	await new Promise((done) => setTimeout(done, 50));
	assert.deepEqual(child.signals, ["SIGTERM", "SIGKILL"]);
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
