import assert from "node:assert/strict";
import { test } from "node:test";
import { SavePolicy } from "../src/save-policy.ts";

test("opening a preview without edits never writes", () => {
	const policy = new SavePolicy();
	policy.loaded("a");
	assert.equal(policy.verdict("a"), "nothing");
});

test("re-conversion of the open preview reloads and never writes the stale model", () => {
	const policy = new SavePolicy();
	policy.loaded("old");
	assert.equal(policy.verdict("new"), "reload");
});

test("an outside edit while the view has edits is a conflict, not a write", () => {
	const policy = new SavePolicy();
	policy.loaded("old");
	policy.edited();
	assert.equal(policy.verdict("outside"), "conflict");
});

test("edit, then switch: written once, then nothing more to write", () => {
	const policy = new SavePolicy();
	policy.loaded("old");
	policy.edited();
	assert.equal(policy.verdict("old"), "write");
	policy.beginWrite("edited");
	assert.equal(policy.verdict("edited"), "nothing");
});

test("a failed write keeps the edits and the old disk state", () => {
	const policy = new SavePolicy();
	policy.loaded("old");
	policy.edited();
	policy.beginWrite("edited");
	policy.failedWrite();
	assert.equal(policy.verdict("old"), "write");
});

test("an edit made during a write is still pending afterwards", () => {
	const policy = new SavePolicy();
	policy.loaded("old");
	policy.edited();
	policy.beginWrite("v1");
	policy.edited();
	assert.equal(policy.verdict("v1"), "write");
});

test("without a loaded preview there is nothing to write", () => {
	const policy = new SavePolicy();
	policy.edited();
	assert.equal(policy.verdict("x"), "nothing");
});

test("edit, outside change, then switch: still a conflict at every save", () => {
	const policy = new SavePolicy();
	policy.loaded("old");
	policy.edited();
	assert.equal(policy.verdict("outside"), "conflict");
	assert.equal(policy.verdict("outside"), "conflict");
});
