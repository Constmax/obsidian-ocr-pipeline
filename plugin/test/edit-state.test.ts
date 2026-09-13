import { test } from "node:test";
import assert from "node:assert/strict";

import { EditStateTracker } from "../src/edit-state.ts";

test("toggle edit on from rendered view auto-switches to source and remembers previous view", () => {
	const tracker = new EditStateTracker();

	const result = tracker.toggle("rendered", false);
	assert.equal(result.editable, true);
	assert.equal(result.representationToSet, "source");
	assert.equal(tracker.getSavedRepresentation(), "rendered");
});

test("toggle edit off after entering from rendered restores rendered view", () => {
	const tracker = new EditStateTracker();

	tracker.toggle("rendered", false);
	const result = tracker.toggle("source", true);

	assert.equal(result.editable, false);
	assert.equal(result.representationToSet, "rendered");
	assert.equal(tracker.getSavedRepresentation(), null);
});

test("toggle edit on from source view stays in source without setting saved representation", () => {
	const tracker = new EditStateTracker();

	const result = tracker.toggle("source", false);
	assert.equal(result.editable, true);
	assert.equal(result.representationToSet, null);
	assert.equal(tracker.getSavedRepresentation(), null);
});

test("toggle edit off after entering from source view stays in source", () => {
	const tracker = new EditStateTracker();

	tracker.toggle("source", false);
	const result = tracker.toggle("source", true);

	assert.equal(result.editable, false);
	assert.equal(result.representationToSet, null);
	assert.equal(tracker.getSavedRepresentation(), null);
});

test("explicit representation switch while editing clears saved representation", () => {
	const tracker = new EditStateTracker();

	tracker.toggle("rendered", false);
	assert.equal(tracker.getSavedRepresentation(), "rendered");

	// User explicitly selects source view or presses representation toggle
	tracker.onExplicitRepresentationChange();
	assert.equal(tracker.getSavedRepresentation(), null);

	// Toggling edit mode off should now stay in source view
	const result = tracker.toggle("source", true);
	assert.equal(result.editable, false);
	assert.equal(result.representationToSet, null);
});

test("reset clears saved representation", () => {
	const tracker = new EditStateTracker();

	tracker.toggle("rendered", false);
	assert.equal(tracker.getSavedRepresentation(), "rendered");

	tracker.reset();
	assert.equal(tracker.getSavedRepresentation(), null);
});
