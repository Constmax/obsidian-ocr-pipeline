import assert from "node:assert/strict";
import { test } from "node:test";

import {
	ProgressPresenter,
	type ProgressNotice,
	type ProgressSurfaces,
} from "../src/progress-display.ts";

class FakeNotice implements ProgressNotice {
	messages: string[];
	shown = true;
	readonly onCancel: (() => void) | null;
	constructor(message: string, onCancel: (() => void) | null) {
		this.messages = [message];
		this.onCancel = onCancel;
	}
	setMessage(message: string): void {
		this.messages.push(message);
	}
	hide(): void {
		this.shown = false;
	}
	isShown(): boolean {
		return this.shown;
	}
}

function setup() {
	const notices: FakeNotice[] = [];
	const status: Array<string | null> = [];
	const tooltips: string[] = [];
	let cancels = 0;
	const surfaces: ProgressSurfaces = {
		openNotice(message, onCancel) {
			const notice = new FakeNotice(message, onCancel);
			notices.push(notice);
			return notice;
		},
		statusBar: {
			show: (text, tooltip) => {
				status.push(text);
				tooltips.push(tooltip);
			},
			hide: () => status.push(null),
		},
	};
	const presenter = new ProgressPresenter(surfaces, 'OCR Preview: Converting "a" …', () => cancels++);
	return { presenter, notices, status, tooltips, cancels: () => cancels };
}

test("a run shows a notice and the status bar; progress updates both", () => {
	const { presenter, notices, status, tooltips } = setup();
	assert.equal(notices.length, 1);
	presenter.setMessage('OCR Preview: Converting "a" — page 1 of 3 …', "OCR 1/3");
	assert.deepEqual(notices[0]!.messages, [
		'OCR Preview: Converting "a" …',
		'OCR Preview: Converting "a" — page 1 of 3 …',
	]);
	// Short in the bar; the full message is its tooltip.
	assert.deepEqual(status, ["OCR …", "OCR 1/3"]);
	assert.deepEqual(tooltips, ['OCR Preview: Converting "a" …', 'OCR Preview: Converting "a" — page 1 of 3 …']);
});

test("a hidden notice leaves the run and the status bar going", () => {
	const { presenter, notices, status, cancels } = setup();
	notices[0]!.hide();
	presenter.setMessage('OCR Preview: Converting "a" — page 2 of 3 …', "OCR 2/3");
	assert.deepEqual(notices[0]!.messages, ['OCR Preview: Converting "a" …']);
	assert.equal(status.at(-1), "OCR 2/3");
	assert.equal(cancels(), 0);
});

test("reopen brings back a hidden notice with the current message and Cancel", () => {
	const { presenter, notices, cancels } = setup();
	presenter.reopen();
	assert.equal(notices.length, 1, "a shown notice is not opened twice");
	notices[0]!.hide();
	presenter.setMessage('OCR Preview: Converting "a" — page 2 of 3 …');
	presenter.reopen();
	assert.equal(notices.length, 2);
	assert.deepEqual(notices[1]!.messages, ['OCR Preview: Converting "a" — page 2 of 3 …']);
	notices[1]!.onCancel!();
	assert.equal(cancels(), 1);
});

test("after a cancel a reopened notice has no Cancel control", () => {
	const { presenter, notices, cancels } = setup();
	notices[0]!.onCancel!();
	notices[0]!.hide();
	presenter.reopen();
	assert.equal(notices[1]!.onCancel, null);
	assert.equal(cancels(), 1);
});

test("the end of the run hides both; later calls do nothing", () => {
	const { presenter, notices, status } = setup();
	presenter.hide();
	assert.equal(notices[0]!.shown, false);
	assert.equal(status.at(-1), null);
	presenter.setMessage("OCR Preview: late");
	presenter.reopen();
	assert.equal(notices.length, 1);
	assert.equal(status.at(-1), null);
});
