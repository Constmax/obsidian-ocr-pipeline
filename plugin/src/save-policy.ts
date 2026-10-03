/**
 * What the view may do with the open preview file (Issue #213): write only
 * its own unsaved edits, and never over a file that changed on disk since the
 * view read or wrote it. `known` is the exact text the view last saw on disk.
 */
export type DiskVerdict =
	| "write" // the view has edits and the file is as the view left it
	| "nothing" // no edits, file unchanged
	| "reload" // no edits, file changed outside: load the new text
	| "conflict"; // edits and a changed file: write neither way

export class SavePolicy {
	private known: string | null = null;
	private dirty = false;
	private previous: { known: string | null; dirty: boolean } | null = null;

	/** The preview was read from disk. */
	loaded(text: string): void {
		this.known = text;
		this.dirty = false;
	}

	/** The user changed the text. */
	edited(): void {
		this.dirty = true;
	}

	verdict(disk: string): DiskVerdict {
		if (this.known === null) return "nothing";
		if (disk === this.known) return this.dirty ? "write" : "nothing";
		return this.dirty ? "conflict" : "reload";
	}

	/** Call before `vault.modify`: its modify event may arrive before it resolves. */
	beginWrite(text: string): void {
		this.previous = { known: this.known, dirty: this.dirty };
		this.known = text;
		this.dirty = false;
	}

	failedWrite(): void {
		if (this.previous === null) return;
		this.known = this.previous.known;
		this.dirty = true;
		this.previous = null;
	}
}
