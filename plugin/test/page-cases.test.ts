// Page cases in the review view (Issue #140): the argv the plugin builds for
// `pdf2md case stash | add | list`, the stdout line it reads, and the stash
// before a page's first edit. The argv and the line come from
// contracts/cli-contract.json, which pdf2md/test/test_cases.py runs against
// the real command.

import assert from "node:assert/strict";
import { test } from "node:test";
import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";

import { runPageCase, type PageCaseResult, type SpawnFunction } from "../src/conversion.ts";
import {
	PageCases,
	addArgs,
	listArgs,
	parseCaseLine,
	stashArgs,
	type CaseRunner,
} from "../src/page-cases.ts";

interface CaseLine {
	stdout: string;
	page: number;
	status: string;
	faultStage: string;
}

interface Contract {
	exitCodes: Record<string, number>;
	pageCases: {
		stash: { args: string[] };
		add: { args: string[]; note: string };
		addWithoutNote: { args: string[] };
		list: { args: string[] };
		caseLines: CaseLine[];
		failure: { args: string[]; exitCode: string; stderr: string[] };
	};
}

const contract = JSON.parse(
	readFileSync(new URL("../../contracts/cli-contract.json", import.meta.url), "utf8"),
) as Contract;
const spec = contract.pageCases;
const PREVIEW = "_ocr-preview/skript.md";

function result(overrides: Partial<PageCaseResult> = {}): PageCaseResult {
	return {
		code: 0,
		signal: null,
		timeout: false,
		stdoutLast: [],
		stderrLast: [],
		stdout: [],
		...overrides,
	};
}

/** A runner that records its calls and answers with `answer`. */
function recorder(answer: (args: string[]) => PageCaseResult | Promise<PageCaseResult>): {
	calls: string[][];
	run: CaseRunner;
} {
	const calls: string[][] = [];
	return {
		calls,
		run: async (args) => {
			calls.push(args);
			return answer(args);
		},
	};
}

test("contract: the argv for stash, add and list", () => {
	assert.deepEqual(stashArgs(PREVIEW, 1), spec.stash.args);
	assert.deepEqual(addArgs(PREVIEW, 1, spec.add.note), spec.add.args);
	assert.deepEqual(addArgs(PREVIEW, 1), spec.addWithoutNote.args);
	assert.deepEqual(addArgs(PREVIEW, 1, "  \n "), spec.addWithoutNote.args);
	assert.deepEqual(listArgs(PREVIEW), spec.list.args);
});

test("a note is one line and cannot be read as an option", () => {
	assert.deepEqual(addArgs(PREVIEW, 12, " footnote tail\nlost  ").slice(-2), [
		"12",
		"--note=footnote tail lost",
	]);
	assert.deepEqual(addArgs(PREVIEW, 12, "--issue 3").slice(-1), ["--note=--issue 3"]);
});

test("contract: the case line gives page, status and fault stage", () => {
	for (const line of spec.caseLines) {
		assert.deepEqual(parseCaseLine(line.stdout), {
			page: line.page,
			status: line.status,
			faultStage: line.faultStage,
		});
	}
	assert.equal(parseCaseLine("stashed: skript/p001"), null);
	assert.equal(parseCaseLine("case skript/p001: broken, fault stage assembly"), null);
	assert.deepEqual(parseCaseLine("case Fall 8: Anfechtung/p1000: open, fault stage upstream"), {
		page: 1000,
		status: "open",
		faultStage: "upstream",
	});
});

test("runPageCase spawns pdf2md with the argv from the vault root and keeps every stdout line", async () => {
	const calls: Array<{ command: string; args: string[]; options: unknown }> = [];
	const child = new EventEmitter() as EventEmitter & {
		stdout: EventEmitter & { setEncoding(): void };
		stderr: EventEmitter & { setEncoding(): void };
	};
	child.stdout = Object.assign(new EventEmitter(), { setEncoding: () => {} });
	child.stderr = Object.assign(new EventEmitter(), { setEncoding: () => {} });
	const spawnFn = ((command: string, args: readonly string[], options?: object) => {
		calls.push({ command, args: [...args], options });
		return child;
	}) as unknown as SpawnFunction;

	const pending = runPageCase(listArgs(PREVIEW), "/Users/test/bin/pdf2md", "/vault", spawnFn);
	const lines = Array.from({ length: 8 }, (_, index) => `case skript/p00${index + 1}: open, fault stage assembly`);
	child.stdout.emit("data", lines.join("\n") + "\n\n");
	child.emit("close", 0);
	const listed = await pending;

	assert.deepEqual(calls, [
		{
			command: "/Users/test/bin/pdf2md",
			args: spec.list.args,
			options: { cwd: "/vault", stdio: ["ignore", "pipe", "pipe"] },
		},
	]);
	assert.equal(listed.code, 0);
	assert.deepEqual(listed.stdout, lines);
});

test("the stash runs once per page per session", async () => {
	const { calls, run } = recorder(() => result({ stdout: ["stashed: skript/p001"] }));
	const cases = new PageCases(run, () => {});

	const first = cases.stashBeforeEdit(PREVIEW, 1);
	assert.ok(first instanceof Promise);
	assert.equal(cases.stashBeforeEdit(PREVIEW, 1), null);
	await first;
	assert.equal(cases.stashBeforeEdit(PREVIEW, 1), null);
	await cases.stashBeforeEdit(PREVIEW, 2);
	await cases.stashBeforeEdit("_ocr-preview/other.md", 1);

	assert.deepEqual(calls, [
		spec.stash.args,
		stashArgs(PREVIEW, 2),
		stashArgs("_ocr-preview/other.md", 1),
	]);

	// Opening a preview starts a session: a converted-again page is stashed anew.
	cases.reset();
	await cases.stashBeforeEdit(PREVIEW, 1);
	assert.equal(calls.length, 4);
});

test("the save of the first edit waits for the stash", async () => {
	let finish: (value: PageCaseResult) => void = () => {};
	const cases = new PageCases(
		() => new Promise<PageCaseResult>((resolve) => (finish = resolve)),
		() => {},
	);
	const order: string[] = [];

	// As the view chains it: the stash goes into the write chain before the save.
	const stash = cases.stashBeforeEdit(PREVIEW, 1);
	assert.ok(stash !== null);
	const chain = Promise.resolve()
		.then(() => stash)
		.then(() => order.push("saved"));
	await new Promise((resolve) => setImmediate(resolve));
	assert.equal(order.length, 0);
	order.push("stashed");
	finish(result());
	await chain;

	assert.deepEqual(order, ["stashed", "saved"]);
});

test("contract: a failing stash is logged and does not block the edit", async () => {
	const warnings: string[] = [];
	const failing = recorder(() =>
		result({ code: contract.exitCodes[spec.failure.exitCode]!, stderrLast: spec.failure.stderr }),
	);
	const cases = new PageCases(failing.run, (message) => warnings.push(message));

	await cases.stashBeforeEdit(PREVIEW, 9);

	assert.deepEqual(warnings, [
		`OCR Preview: Page 9 of "${PREVIEW}" was not stashed — skript.md has no page 9.`,
	]);
	// Not tried again on the next keystroke.
	assert.equal(cases.stashBeforeEdit(PREVIEW, 9), null);
	assert.deepEqual(failing.calls, [spec.failure.args]);
});

test("a stash that cannot start or throws resolves too", async () => {
	const warnings: string[] = [];
	const warn = (message: string) => warnings.push(message);

	await new PageCases(
		async () => result({ code: null, stderrLast: ["Error: spawn /x/pdf2md ENOENT"] }),
		warn,
	).stashBeforeEdit(PREVIEW, 1);
	await new PageCases(() => Promise.reject(new Error("boom")), warn).stashBeforeEdit(PREVIEW, 1);
	await new PageCases(() => {
		throw new Error("sync boom");
	}, warn).stashBeforeEdit(PREVIEW, 1);
	await new PageCases(async () => result({ code: null, signal: "SIGKILL", timeout: true }), warn).stashBeforeEdit(
		PREVIEW,
		1,
	);

	assert.deepEqual(warnings, [
		`OCR Preview: Page 1 of "${PREVIEW}" was not stashed — pdf2md not found. Please run setup.sh in repo.`,
		`OCR Preview: Page 1 of "${PREVIEW}" was not stashed — Error: boom.`,
		`OCR Preview: Page 1 of "${PREVIEW}" was not stashed — Error: sync boom.`,
		`OCR Preview: Page 1 of "${PREVIEW}" was not stashed — pdf2md case did not answer.`,
	]);
});

test("marking a page runs add and returns the case with the CLI's hints", async () => {
	const line = spec.caseLines[0]!;
	const hint = "the expected block equals the produced one — correct the page and mark it again";
	const { calls, run } = recorder(() =>
		result({ stdout: ["pymupdf says hello", line.stdout, hint] }),
	);
	const cases = new PageCases(run, () => {});

	const marked = await cases.mark(PREVIEW, line.page, spec.add.note);

	assert.deepEqual(calls, [spec.add.args]);
	assert.deepEqual(marked, {
		ok: true,
		pageCase: { page: line.page, status: line.status, faultStage: line.faultStage },
		hints: [hint],
	});
	// The case holds the produced block: a later edit of the page needs no stash.
	assert.equal(cases.stashBeforeEdit(PREVIEW, line.page), null);
});

test("a failed mark names the reason; an unknown answer still counts as marked", async () => {
	const failed = await new PageCases(
		async () => result({ code: 1, stderrLast: ["pdf2md case: no page-cache entry for page 3"] }),
		() => {},
	).mark(PREVIEW, 3);
	assert.deepEqual(failed, { ok: false, reason: "no page-cache entry for page 3" });

	const silent = await new PageCases(async () => result({ code: 1 }), () => {}).mark(PREVIEW, 3);
	assert.deepEqual(silent, { ok: false, reason: "pdf2md case failed (exit code 1)" });

	const unknown = await new PageCases(async () => result({ stdout: ["done"] }), () => {}).mark(PREVIEW, 3);
	assert.deepEqual(unknown, { ok: true, pageCase: null, hints: [] });
});

test("list gives the marked pages and is empty when the query fails", async () => {
	const { calls, run } = recorder(() =>
		result({ stdout: ["pymupdf says hello", ...spec.caseLines.map((line) => line.stdout)] }),
	);
	const listed = await new PageCases(run, () => {}).list(PREVIEW);

	assert.deepEqual(calls, [spec.list.args]);
	assert.deepEqual(
		[...listed.keys()],
		spec.caseLines.map((line) => line.page),
	);
	assert.equal(listed.get(spec.caseLines[1]!.page)?.status, spec.caseLines[1]!.status);

	const warnings: string[] = [];
	const none = await new PageCases(
		async () => result({ code: 1, stderrLast: ["❌ MLX venv missing (/x/python). Run ./setup.sh in repo once."] }),
		(message) => warnings.push(message),
	).list(PREVIEW);
	assert.equal(none.size, 0);
	assert.deepEqual(warnings, [
		`OCR Preview: The page cases of "${PREVIEW}" could not be listed — MLX venv missing (/x/python). Run ./setup.sh in repo once.`,
	]);
});
