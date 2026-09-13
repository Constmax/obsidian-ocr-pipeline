import assert from "node:assert/strict";
import { test } from "node:test";

import { LatestTaskQueue } from "../src/open-queue.ts";

function deferred(): { promise: Promise<void>; resolve: () => void } {
	let resolve!: () => void;
	const promise = new Promise<void>((done) => {
		resolve = done;
	});
	return { promise, resolve };
}

test("coalesces startup requests before the first task starts", async () => {
	const queue = new LatestTaskQueue();
	const started: string[] = [];

	const first = queue.enqueue(async () => {
		started.push("first");
	});
	const second = queue.enqueue(async () => {
		started.push("second");
	});

	const results = await Promise.all([first, second]);

	assert.deepEqual(started, ["second"]);
	assert.deepEqual(results, [false, true]);
});

test("runs a request queued during an active task after that task", async () => {
	const queue = new LatestTaskQueue();
	const started: string[] = [];
	const firstStarted = deferred();
	const firstRelease = deferred();

	const first = queue.enqueue(async () => {
		started.push("first");
		firstStarted.resolve();
		await firstRelease.promise;
	});
	await firstStarted.promise;

	const second = queue.enqueue(async () => {
		started.push("second");
	});
	firstRelease.resolve();

	const results = await Promise.all([first, second]);

	assert.deepEqual(started, ["first", "second"]);
	assert.deepEqual(results, [true, true]);
});

test("cancels pending work without interrupting the active task", async () => {
	const queue = new LatestTaskQueue();
	const started: string[] = [];
	const firstStarted = deferred();
	const firstRelease = deferred();

	const first = queue.enqueue(async () => {
		started.push("first");
		firstStarted.resolve();
		await firstRelease.promise;
	});
	await firstStarted.promise;

	const pending = queue.enqueue(async () => {
		started.push("pending");
	});
	queue.cancel();
	firstRelease.resolve();

	const results = await Promise.all([first, pending]);

	assert.deepEqual(started, ["first"]);
	assert.deepEqual(results, [true, false]);
});

test("continues with newer work after an earlier task rejects", async () => {
	const queue = new LatestTaskQueue();
	const started: string[] = [];
	const firstStarted = deferred();

	const run = queue.enqueue(async () => {
		started.push("first");
		firstStarted.resolve();
		throw new Error("first failed");
	});
	await firstStarted.promise;
	const newer = queue.enqueue(async () => {
		started.push("second");
	});

	await assert.rejects(run, /first failed/);
	assert.equal(await newer, true);
	assert.deepEqual(started, ["first", "second"]);
});

test("starts work enqueued from a completion continuation", async () => {
	const queue = new LatestTaskQueue();
	const started: string[] = [];

	const first = queue.enqueue(async () => {
		started.push("first");
	});
	await first;

	const second = queue.enqueue(async () => {
		started.push("second");
	});
	assert.equal(await second, true);

	assert.deepEqual(started, ["first", "second"]);
});
