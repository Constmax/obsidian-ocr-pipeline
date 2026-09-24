// Consumer side of the CLI contract with pdf2md and the Stage-1 scripts
// (Issue #55). contracts/cli-contract.json and contracts/progress-v1.jsonl are
// read here and by pdf2md/test/test_cli_contract.py and
// bin/test/test_stage1_contract.py: a key, an exit code or a message changed
// on only one side turns one of the suites red.

import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";

import {
	PROGRESS_PROTOCOL,
	SHORT_PAGE_LINE,
	parseProgressEvent,
	type ConversionResult,
} from "../src/conversion.ts";
import { EXIT_CODES, classifyFailure, classifyOcrFailure } from "../src/conversion-controller.ts";
import { CONVERTIBLE_EXTENSIONS } from "../src/input-formats.ts";
import {
	SUPPORTED_PREVIEW_FORMAT,
	parsePreview,
	previewFormatWarning,
} from "../src/preview-parser.ts";

interface EventSpec {
	required: Record<string, string>;
	optional: Record<string, string>;
}

interface Contract {
	progress: {
		protocol: number;
		fixture: string;
		events: Record<string, EventSpec>;
		origins: string[];
	};
	exitCodes: Record<string, number>;
	inputSuffixes: string[];
	previewFormat: number;
	stage1: {
		shortPages: { stderr: string[]; shortPageNumbers: number[] };
		failure: { stdout: string[]; reason: string };
	};
}

const contracts = new URL("../../contracts/", import.meta.url);
const contract = JSON.parse(
	readFileSync(new URL("cli-contract.json", contracts), "utf8"),
) as Contract;
const fixture = readFileSync(new URL(contract.progress.fixture, contracts), "utf8")
	.split("\n")
	.filter((line) => line.length > 0)
	.map((line) => JSON.parse(line) as Record<string, unknown>);

/** A value of the wrong type for each contract type. */
const WRONG: Record<string, unknown> = {
	string: 7,
	integer: 1.5,
	number: "1.5",
	boolean: "false",
};

function result(overrides: Partial<ConversionResult>): ConversionResult {
	return { code: 0, signal: null, timeout: false, stdoutLast: [], stderrLast: [], ...overrides };
}

test("contract: the parser reads the protocol version of the contract", () => {
	assert.equal(PROGRESS_PROTOCOL, contract.progress.protocol);
});

test("contract: every canonical event parses to its values", () => {
	const parsed = fixture.map((event) => parseProgressEvent(JSON.stringify(event)));
	assert.deepEqual(parsed, [
		{ type: "start", file: "Verwaltungsrecht AT Fall 8.pdf", pages: 3, dpi: 150 },
		{ type: "page", num: 1, total: 3, seconds: 0.1, origin: "textlayer", derailed: false },
		{
			type: "page",
			num: 2,
			total: 3,
			seconds: 41.7,
			origin: "ocr",
			derailed: true,
			reason: "repetition loop in tile 2, retried as halves",
		},
		{ type: "page", num: 3, total: 3, seconds: 18.2, origin: "diagramm", derailed: false },
		{
			type: "finished",
			target: "_ocr-vorschau/Verwaltungsrecht AT Fall 8.md",
			seconds: 60,
			derailed: 1,
		},
	]);
});

test("contract: a missing or mistyped required field rejects the event", () => {
	for (const event of fixture) {
		const spec = contract.progress.events[event.typ as string];
		assert.ok(spec, `fixture event type ${String(event.typ)} is in the contract`);
		for (const [key, kind] of Object.entries(spec.required)) {
			if (key === "protokoll") continue; // absent = legacy version 1, see below
			const { [key]: _removed, ...missing } = event;
			assert.equal(parseProgressEvent(JSON.stringify(missing)), null, `${String(event.typ)} without ${key}`);
			const mistyped = { ...event, [key]: WRONG[kind] };
			assert.equal(parseProgressEvent(JSON.stringify(mistyped)), null, `${String(event.typ)} with a ${kind} ${key} of the wrong type`);
		}
		for (const [key, kind] of Object.entries(spec.optional)) {
			const mistyped = { ...event, [key]: WRONG[kind] };
			assert.equal(parseProgressEvent(JSON.stringify(mistyped)), null, `${String(event.typ)} with a mistyped ${key}`);
		}
	}
});

test("contract: unknown fields are ignored", () => {
	for (const event of fixture) {
		const line = JSON.stringify(event);
		const extended = JSON.stringify({ ...event, spaeter: { neu: true } });
		assert.deepEqual(parseProgressEvent(extended), parseProgressEvent(line));
	}
});

test("contract: another protocol version is not read; a missing one is legacy version 1", () => {
	for (const event of fixture) {
		const newer = { ...event, protokoll: contract.progress.protocol + 1 };
		assert.equal(parseProgressEvent(JSON.stringify(newer)), null);
		const { protokoll: _version, ...legacy } = event;
		assert.deepEqual(parseProgressEvent(JSON.stringify(legacy)), parseProgressEvent(JSON.stringify(event)));
	}
});

test("contract: English keys are not a second vocabulary", () => {
	const english = { type: "page", num: 1, total: 3, seconds: 1, origin: "ocr", derailed: false };
	assert.equal(parseProgressEvent(JSON.stringify(english)), null);
});

test("contract: exit codes match the contract table", () => {
	assert.deepEqual(
		{ ...EXIT_CODES },
		{
			checkFailed: contract.exitCodes["check-failed"],
			cancelledPartial: contract.exitCodes["cancelled-partial"],
			cancelledEmpty: contract.exitCodes["cancelled-empty"],
		},
	);
	assert.equal(classifyFailure(result({ code: contract.exitCodes["cancelled-partial"] })).kind, "partial-output");
	assert.equal(classifyFailure(result({ code: contract.exitCodes["cancelled-empty"] })).kind, "cancelled-before-output");
	assert.equal(classifyFailure(result({ code: contract.exitCodes["check-failed"] })).kind, "missing-dependency");
	assert.equal(classifyFailure(result({ code: contract.exitCodes["error"] })).kind, "exit-code");
});

test("contract: the convertible extensions are the CLI's input suffixes", () => {
	assert.deepEqual(
		[...CONVERTIBLE_EXTENSIONS].sort(),
		contract.inputSuffixes.map((suffix) => suffix.replace(/^\./, "")).sort(),
	);
});

test("contract: the parser reads the preview format pdf2md writes and warns on a newer one", () => {
	assert.equal(SUPPORTED_PREVIEW_FORMAT, contract.previewFormat);
	const current = parsePreview(`---\nvorschau-format: ${contract.previewFormat}\n---\n%% S. 1 %%\n\nText\n`);
	assert.equal(previewFormatWarning(current), null);
	const newer = parsePreview(`---\nvorschau-format: ${contract.previewFormat + 1}\n---\n%% S. 1 %%\n\nText\n`);
	assert.match(previewFormatWarning(newer) ?? "", /unknown preview format 2/);
	assert.equal(previewFormatWarning(parsePreview("%% S. 1 %%\n\nText\n")), null);
});

test("contract: a JSON-quoted title with colon and quotes reads back unchanged", () => {
	const title = 'Fall 8: "Anfechtung"';
	const preview = parsePreview(`---\ntitel: ${JSON.stringify(title)}\n---\n%% S. 1 %%\n`);
	assert.equal(preview.frontmatter["titel"], title);
});

test("contract: Stage-1 B5 lines yield the short pages", () => {
	const pages = contract.stage1.shortPages.stderr
		.map((line) => SHORT_PAGE_LINE.exec(line))
		.map((match) => (match === null ? null : Number(match[1])));
	assert.deepEqual(pages, contract.stage1.shortPages.shortPageNumbers);
});

test("contract: the Stage-1 failure line gives the failure reason", () => {
	const failure = classifyOcrFailure(
		result({ code: contract.exitCodes["error"], stdoutLast: contract.stage1.failure.stdout }),
	);
	assert.equal(
		failure.message,
		`OCR Preview: Searchable copy failed (Code ${contract.exitCodes["error"]}) — ${contract.stage1.failure.reason}.`,
	);
});
