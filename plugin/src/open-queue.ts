/** Runs only the newest queued task, while letting an active task finish. */
interface PendingTask {
	task: () => Promise<void>;
	resolve: (completed: boolean) => void;
	reject: (reason?: unknown) => void;
}

export class LatestTaskQueue {
	private pending: PendingTask | null = null;
	private running: Promise<void> | null = null;

	enqueue(task: () => Promise<void>): Promise<boolean> {
		const result = new Promise<boolean>((resolve, reject) => {
			this.pending?.resolve(false);
			this.pending = { task, resolve, reject };
		});
		if (this.running === null) {
			this.running = Promise.resolve().then(() => this.drain());
		}
		return result;
	}

	cancel(): void {
		this.pending?.resolve(false);
		this.pending = null;
	}

	async waitForIdle(): Promise<void> {
		await this.running;
	}

	private async drain(): Promise<void> {
		while (this.pending !== null) {
			const pending = this.pending;
			this.pending = null;
			try {
				await pending.task();
				pending.resolve(true);
			} catch (err) {
				pending.reject(err);
			}
		}
		this.running = null;
	}
}
