// Page cases in the review view (Issue #140; terms in CONTEXT.md): the stash
// before a page's first edit, marking a page as wrong, and which pages have a
// case. The plugin only spawns `pdf2md case stash | add | list`; it knows
// neither the page-cache path nor the case format. The argv and the stdout
// line it reads are pinned in contracts/cli-contract.json.
//
// Free of Obsidian imports so it runs under `node --test`.

import type { PageCaseResult } from "./conversion.ts";

/** Runs `pdf2md <args>` from the vault root; never rejects. */
export type CaseRunner = (args: string[]) => Promise<PageCaseResult>;

export interface PageCase {
	page: number;
	status: "open" | "fixed";
	faultStage: "assembly" | "upstream";
}

export type MarkResult =
	| {
			ok: true;
			/** The case as `add` reported it; null when its line was not understood. */
			pageCase: PageCase | null;
			/** What `add` said besides, e.g. that the page was marked uncorrected. */
			hints: string[];
	  }
	| { ok: false; reason: string };

/** `preview` is the vault-relative path of the preview, `page` its page number. */
export function stashArgs(preview: string, page: number): string[] {
	return ["case", "stash", preview, "--page", String(page)];
}

/**
 * Without a note the case keeps the one it has. The note goes in as
 * `--note=…`, so one that starts with a dash is not read as an option.
 */
export function addArgs(preview: string, page: number, note = ""): string[] {
	const line = note.replace(/\s+/g, " ").trim();
	const args = ["case", "add", preview, "--page", String(page)];
	if (line.length > 0) args.push(`--note=${line}`);
	return args;
}

export function listArgs(preview: string): string[] {
	return ["case", "list", preview];
}

/** `case <stem>/pNNN: <status>, fault stage <stage>`, as `add` and `list` print it. */
const CASE_LINE = /^case .*\/p(\d+): (open|fixed), fault stage (assembly|upstream)$/;

export function parseCaseLine(line: string): PageCase | null {
	const match = CASE_LINE.exec(line.trim());
	if (match === null) return null;
	return {
		page: Number(match[1]),
		status: match[2] as PageCase["status"],
		faultStage: match[3] as PageCase["faultStage"],
	};
}

function failureReason(result: PageCaseResult): string {
	const last = result.stderrLast[result.stderrLast.length - 1] ?? "";
	if (result.code === null && /ENOENT/.test(last)) {
		return "pdf2md not found. Please run setup.sh in repo";
	}
	if (result.timeout) return "pdf2md case did not answer";
	const reason = last
		.replace(/^pdf2md case:\s*/, "")
		.replace(/^❌\s*/, "")
		.replace(/\.$/, "");
	if (reason.length > 0) return reason;
	return `pdf2md case failed (exit code ${result.code ?? result.signal ?? "unknown"})`;
}

/**
 * The page cases of the preview that is open in the review view. `reset()`
 * starts a session: each opening of a preview, so a preview that was
 * converted again gets its pages stashed anew.
 */
export class PageCases {
	private readonly stashed = new Set<string>();
	private readonly run: CaseRunner;
	private readonly warn: (message: string) => void;

	constructor(
		run: CaseRunner,
		warn: (message: string) => void = (message) => console.warn(message),
	) {
		this.run = run;
		this.warn = warn;
	}

	reset(): void {
		this.stashed.clear();
	}

	/**
	 * Called on every edit of a page block. The first edit of a page in a
	 * session starts `pdf2md case stash` and returns its promise, for the save
	 * of that edit to wait on; later edits return null. A failed stash is
	 * logged and not tried again: the promise never rejects, and editing goes
	 * on either way.
	 */
	stashBeforeEdit(preview: string, page: number): Promise<void> | null {
		const key = `${preview}\n${page}`;
		if (this.stashed.has(key)) return null;
		this.stashed.add(key);
		return this.call(stashArgs(preview, page)).then((result) => {
			if (result.code !== 0) {
				this.warn(
					`OCR Preview: Page ${page} of "${preview}" was not stashed — ${failureReason(result)}.`,
				);
			}
		});
	}

	/** Marks a page as wrong: its block in the saved preview is the expected one. */
	async mark(preview: string, page: number, note = ""): Promise<MarkResult> {
		const result = await this.call(addArgs(preview, page, note));
		if (result.code !== 0) return { ok: false, reason: failureReason(result) };
		// The case now holds the produced block; the page needs no stash.
		this.stashed.add(`${preview}\n${page}`);
		const at = result.stdout.findIndex((line) => parseCaseLine(line) !== null);
		const line = result.stdout[at];
		return {
			ok: true,
			pageCase: line === undefined ? null : parseCaseLine(line),
			hints: at < 0 ? [] : result.stdout.slice(at + 1),
		};
	}

	/** The pages of a preview that have a case; empty when the query fails. */
	async list(preview: string): Promise<Map<number, PageCase>> {
		const result = await this.call(listArgs(preview));
		const cases = new Map<number, PageCase>();
		if (result.code !== 0) {
			this.warn(
				`OCR Preview: The page cases of "${preview}" could not be listed — ${failureReason(result)}.`,
			);
			return cases;
		}
		for (const line of result.stdout) {
			const pageCase = parseCaseLine(line);
			if (pageCase !== null) cases.set(pageCase.page, pageCase);
		}
		return cases;
	}

	private async call(args: string[]): Promise<PageCaseResult> {
		try {
			return await this.run(args);
		} catch (err) {
			return {
				code: null,
				signal: null,
				timeout: false,
				stdoutLast: [],
				stderrLast: [String(err)],
				stdout: [],
			};
		}
	}
}
