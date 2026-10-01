import { test } from "node:test";
import assert from "node:assert/strict";

import { EditStateTracker } from "../src/edit-state.ts";

test("editing from rendered switches to source and back", () => {
	const tracker = new EditStateTracker();
	assert.deepEqual(tracker.toggle("rendered", false), { editable: true, representationToSet: "source" });
	assert.deepEqual(tracker.toggle("source", true), { editable: false, representationToSet: "rendered" });
	// The restore is spent: the next round from source stays in source.
	assert.deepEqual(tracker.toggle("source", false), { editable: true, representationToSet: null });
	assert.deepEqual(tracker.toggle("source", true), { editable: false, representationToSet: null });
});

test("an explicit view switch cancels the restore", () => {
	const tracker = new EditStateTracker();
	tracker.toggle("rendered", false);
	tracker.onExplicitRepresentationChange();
	assert.deepEqual(tracker.toggle("source", true), { editable: false, representationToSet: null });
});

test("a reset cancels the restore", () => {
	const tracker = new EditStateTracker();
	tracker.toggle("rendered", false);
	tracker.reset();
	assert.deepEqual(tracker.toggle("source", true), { editable: false, representationToSet: null });
});
