import { test } from "node:test";
import assert from "node:assert/strict";

import {
	reconcile,
	oldDateFrom,
	recordDecision,
	emptyManifest,
	readManifest,
	writeManifest,
	targetFolder,
} from "../src/status.ts";
import type { FoundFile, StatusManifest } from "../src/types.ts";

const NOW = "2026-08-05T12:00:00+02:00";

const FOLDERS = {
	previewFolder: "_ocr-preview",
	acceptedFolder: "_ocr-preview/_accepted",
	rejectedFolder: "_ocr-preview/_rejected",
};

function makeFile(
	name: string,
	location: FoundFile["location"],
	frontmatter: Record<string, unknown> = {},
): FoundFile {
	const folder = targetFolder(location, FOLDERS);
	return { name, path: `${folder}/${name}`, location, frontmatter };
}

function withEntry(
	name: string,
	partial: Partial<StatusManifest["entries"][string]>,
): StatusManifest {
	const m = emptyManifest(NOW);
	m.entries[name] = {
		status: "open",
		path: `_ocr-preview/${name}`,
		"source-pdf": null,
		"manual-source-pdf": null,
		pages: null,
		"pages-ocr": null,
		"pages-diagram": null,
		"ocr-date": null,
		"ocr-timestamp": null,
		decided: null,
		"checked-until": null,
		note: null,
		previous: null,
		...partial,
	};
	return m;
}

// ── Rule 1: startsWith trap ───────────────────────────────────────────

test("Rule 1: a file in _accepted does not count as open", () => {
	const res = reconcile(
		[makeFile("Case 8.md", "accepted")],
		emptyManifest(NOW),
		NOW,
	);
	assert.equal(res.manifest.entries["Case 8.md"]?.status, "accepted");
	assert.ok(
		res.manifest.entries["Case 8.md"]?.path.startsWith("_ocr-preview/"),
		"subfolder is inside preview folder",
	);
});

// ── Rule 3: File without entry ─────────────────────────────────────────────

test("Rule 3: new file gets an entry from frontmatter", () => {
	const res = reconcile(
		[
			makeFile("Case 8.md", "open", {
				"source-pdf": "raw/VwR/Case 8.pdf",
				pages: "14",
				"pages-ocr": "9",
				"pages-diagram": "2",
				"ocr-date": "2026-07-30",
			}),
		],
		emptyManifest(NOW),
		NOW,
	);
	const entry = res.manifest.entries["Case 8.md"];
	assert.equal(entry?.status, "open");
	assert.equal(entry?.["source-pdf"], "raw/VwR/Case 8.pdf");
	assert.equal(entry?.pages, 14);
	assert.equal(entry?.["pages-ocr"], 9);
	assert.equal(entry?.["ocr-date"], "2026-07-30");
	assert.equal(entry?.decided, null, "no timestamp invented");
});

test("Rule 3: file is already in _rejected without us having moved it", () => {
	const res = reconcile(
		[makeFile("Case 9.md", "rejected")],
		emptyManifest(NOW),
		NOW,
	);
	assert.equal(res.manifest.entries["Case 9.md"]?.status, "rejected");
	assert.equal(res.manifest.entries["Case 9.md"]?.decided, null);
});

// ── Rule 2: file system wins ────────────────────────────────────────

test("Rule 2: manually moved back to _ocr-preview -> open again", () => {
	const prev = withEntry("Case 8.md", {
		status: "accepted",
		path: "_ocr-preview/_accepted/Case 8.md",
		decided: "2026-08-01T10:00:00+02:00",
		note: "p. 7 broken",
		"checked-until": 12,
		"ocr-date": "2026-07-30",
	});
	const res = reconcile(
		[makeFile("Case 8.md", "open", { "ocr-date": "2026-07-30" })],
		prev,
		NOW,
	);
	const entry = res.manifest.entries["Case 8.md"];
	assert.equal(entry?.status, "open");
	assert.equal(entry?.decided, null, "decision revoked");
	assert.equal(entry?.note, "p. 7 broken", "note remains");
	assert.equal(entry?.["checked-until"], 12, "reading progress remains");
	assert.deepEqual(res.corrected, ["Case 8.md"]);
	assert.deepEqual(res.reCreated, []);
});

test("Rule 2: manually moved to _accepted -> status follows folder", () => {
	const prev = withEntry("Case 8.md", { status: "open" });
	const res = reconcile([makeFile("Case 8.md", "accepted")], prev, NOW);
	assert.equal(res.manifest.entries["Case 8.md"]?.status, "accepted");
	assert.deepEqual(res.corrected, ["Case 8.md"]);
});

// ── Rule 5/6: Re-conversion ─────────────────────────────────────────────

test("Rule 6: two versions at the same time -> re-created, old decision retained", () => {
	const prev = withEntry("Case 8.md", {
		status: "accepted",
		path: "_ocr-preview/_accepted/Case 8.md",
		decided: "2026-08-01T10:00:00+02:00",
		"ocr-date": "2026-07-30",
	});
	const res = reconcile(
		[
			makeFile("Case 8.md", "open", { "ocr-date": "2026-08-05" }),
			makeFile("Case 8.md", "accepted", { "ocr-date": "2026-07-30" }),
		],
		prev,
		NOW,
	);
	const entry = res.manifest.entries["Case 8.md"];
	assert.equal(entry?.status, "re-created");
	assert.equal(entry?.["ocr-date"], "2026-08-05", "fresh version counts");
	assert.equal(entry?.previous?.status, "accepted");
	assert.equal(entry?.previous?.["ocr-date"], "2026-07-30");
	assert.equal(entry?.previous?.decided, "2026-08-01T10:00:00+02:00");
	assert.deepEqual(res.reCreated, ["Case 8.md"]);
});

test("Rule 6: only one version, but different ocr-date -> also re-created", () => {
	const prev = withEntry("Case 8.md", {
		status: "rejected",
		"ocr-date": "2026-07-30",
		decided: "2026-08-01T10:00:00+02:00",
	});
	const res = reconcile(
		[makeFile("Case 8.md", "open", { "ocr-date": "2026-08-05" })],
		prev,
		NOW,
	);
	assert.equal(res.manifest.entries["Case 8.md"]?.status, "re-created");
	assert.equal(res.manifest.entries["Case 8.md"]?.previous?.status, "rejected");
});

test("Rule 6 differs from manual move: same ocr-date is not a re-conversion", () => {
	const prev = withEntry("Case 8.md", {
		status: "accepted",
		"ocr-date": "2026-07-30",
		decided: "2026-08-01T10:00:00+02:00",
	});
	const res = reconcile(
		[makeFile("Case 8.md", "open", { "ocr-date": "2026-07-30" })],
		prev,
		NOW,
	);
	assert.equal(res.manifest.entries["Case 8.md"]?.status, "open");
	assert.deepEqual(res.reCreated, []);
	assert.deepEqual(res.corrected, ["Case 8.md"]);
});

test("re-created in open folder is not re-corrected every time", () => {
	const prev = withEntry("Case 8.md", {
		status: "re-created",
		"ocr-date": "2026-08-05",
		previous: {
			status: "accepted",
			decided: "2026-08-01T10:00:00+02:00",
			"ocr-date": "2026-07-30",
		},
	});
	const res = reconcile(
		[makeFile("Case 8.md", "open", { "ocr-date": "2026-08-05" })],
		prev,
		NOW,
	);
	assert.equal(res.manifest.entries["Case 8.md"]?.status, "re-created");
	assert.deepEqual(res.corrected, []);
});

// ── Rule 4: Entry without file ─────────────────────────────────────────────

test("Rule 4: file is elsewhere in vault -> adopted, entry remains", () => {
	const prev = withEntry("Case 8.md", {
		status: "accepted",
		path: "wiki/case-8.md",
	});
	const res = reconcile([], prev, NOW, (p) => p === "wiki/case-8.md");
	assert.equal(res.manifest.entries["Case 8.md"]?.status, "adopted");
	assert.deepEqual(res.removed, []);
});

test("Rule 4: file nowhere anymore -> cache row removed (no file deleted)", () => {
	const prev = withEntry("Case 8.md", { status: "accepted" });
	const res = reconcile([], prev, NOW, () => false);
	assert.equal(res.manifest.entries["Case 8.md"], undefined);
	assert.deepEqual(res.removed, ["Case 8.md"]);
});

// ── Manifest is cache only ──────────────────────────────────────────────────

test("deleted manifest rebuilds completely from folder structure", () => {
	const files = [
		makeFile("A.md", "open", { pages: "3" }),
		makeFile("B.md", "accepted", { pages: "5" }),
		makeFile("C.md", "rejected", { pages: "7" }),
	];
	const res = reconcile(files, emptyManifest(NOW), NOW);
	assert.equal(res.manifest.entries["A.md"]?.status, "open");
	assert.equal(res.manifest.entries["B.md"]?.status, "accepted");
	assert.equal(res.manifest.entries["C.md"]?.status, "rejected");
});

// ── Serialization ──────────────────────────────────────────────────────────

test("manifest survives serialization and deserialization", () => {
	const prev = withEntry("Case 8.md", {
		status: "accepted",
		pages: 14,
		"pages-ocr": 9,
		note: "p. 7 reading order broken",
		"checked-until": 12,
		decided: NOW,
		previous: { status: "rejected", decided: null, "ocr-date": "2026-07-01" },
	});
	const parsed = readManifest(writeManifest(prev), NOW);
	assert.deepEqual(parsed.entries, prev.entries);
});

test("manifest with unknown status falls back to open instead of throwing", () => {
	const parsed = readManifest(
		JSON.stringify({
			version: 1,
			entries: { "X.md": { status: "garbage", path: "_ocr-preview/X.md" } },
		}),
		NOW,
	);
	assert.equal(parsed.entries["X.md"]?.status, "open");
});

test("entries are written sorted so a diff remains readable", () => {
	const m = emptyManifest(NOW);
	for (const name of ["Z.md", "A.md", "M.md"]) {
		m.entries[name] = withEntry(name, {}).entries[name]!;
	}
	const text = writeManifest(m);
	assert.ok(text.indexOf('"A.md"') < text.indexOf('"M.md"'));
	assert.ok(text.indexOf('"M.md"') < text.indexOf('"Z.md"'));
});

// ── Record decision ──────────────────────────────────────────────────

test("recordDecision sets status, path, and timestamp", () => {
	const prev = withEntry("Case 8.md", { status: "open" });
	const next = recordDecision(
		prev,
		"Case 8.md",
		"accepted",
		"_ocr-preview/_accepted/Case 8.md",
		NOW,
	);
	const entry = next.entries["Case 8.md"];
	assert.equal(entry?.status, "accepted");
	assert.equal(entry?.path, "_ocr-preview/_accepted/Case 8.md");
	assert.equal(entry?.decided, NOW);
});

test("resetting to open clears decision timestamp", () => {
	const prev = withEntry("Case 8.md", {
		status: "accepted",
		decided: NOW,
	});
	const next = recordDecision(
		prev,
		"Case 8.md",
		"open",
		"_ocr-preview/Case 8.md",
		NOW,
	);
	assert.equal(next.entries["Case 8.md"]?.decided, null);
});

test("a new decision clears memory of the old one", () => {
	const prev = withEntry("Case 8.md", {
		status: "re-created",
		previous: {
			status: "accepted",
			decided: "2026-08-01T10:00:00+02:00",
			"ocr-date": "2026-07-30",
		},
	});
	const next = recordDecision(
		prev,
		"Case 8.md",
		"accepted",
		"_ocr-preview/_accepted/Case 8.md",
		NOW,
	);
	assert.equal(next.entries["Case 8.md"]?.previous, null);
});

test("targetFolder maps all three locations", () => {
	assert.equal(targetFolder("open", FOLDERS), "_ocr-preview");
	assert.equal(targetFolder("accepted", FOLDERS), "_ocr-preview/_accepted");
	assert.equal(targetFolder("rejected", FOLDERS), "_ocr-preview/_rejected");
});

test("oldDateFrom: frontmatter of old file beats memory", () => {
	const entry = withEntry("Case 8.md", {
		"ocr-date": "2026-07-30",
		previous: {
			status: "accepted",
			decided: NOW,
			"ocr-date": "2026-06-01",
		},
	}).entries["Case 8.md"];
	assert.equal(oldDateFrom(entry, { "ocr-date": "2026-06-15" }), "2026-06-15");
});

test("oldDateFrom: entry of re-conversion carries NEW date — memory is used", () => {
	const entry = withEntry("Case 8.md", {
		"ocr-date": "2026-07-30",
		previous: {
			status: "accepted",
			decided: NOW,
			"ocr-date": "2026-06-01",
		},
	}).entries["Case 8.md"];
	assert.equal(oldDateFrom(entry, {}), "2026-06-01");
});

test("oldDateFrom: without indication remains 'old'", () => {
	assert.equal(oldDateFrom(undefined, {}), "old");
});
