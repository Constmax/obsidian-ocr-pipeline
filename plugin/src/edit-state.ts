import type { Representation } from "./md-pane.ts";

export interface ToggleEditResult {
	editable: boolean;
	representationToSet: Representation | null;
}

/**
 * Editing needs the source view. Entering edit mode from the rendered view
 * switches to source; leaving it switches back, unless the user picked a
 * view explicitly in between.
 */
export class EditStateTracker {
	private restoreRendered = false;

	toggle(currentRepresentation: Representation, currentlyEditable: boolean): ToggleEditResult {
		if (!currentlyEditable) {
			this.restoreRendered = currentRepresentation === "rendered";
			return { editable: true, representationToSet: this.restoreRendered ? "source" : null };
		}
		const restore = this.restoreRendered && currentRepresentation === "source";
		this.restoreRendered = false;
		return { editable: false, representationToSet: restore ? "rendered" : null };
	}

	onExplicitRepresentationChange(): void {
		this.restoreRendered = false;
	}

	reset(): void {
		this.restoreRendered = false;
	}
}
