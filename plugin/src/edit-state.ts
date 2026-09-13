import type { Representation } from "./md-pane.ts";

export interface ToggleEditResult {
	editable: boolean;
	representationToSet: Representation | null;
}

/**
 * Tracks edit mode state and previous view representation to restore
 * the representation (e.g. Rendered view) when edit mode is toggled off.
 */
export class EditStateTracker {
	private savedRepresentation: Representation | null = null;

	toggle(
		currentRepresentation: Representation,
		currentlyEditable: boolean,
	): ToggleEditResult {
		const nextEditable = !currentlyEditable;
		if (nextEditable) {
			if (currentRepresentation === "rendered") {
				this.savedRepresentation = "rendered";
				return {
					editable: true,
					representationToSet: "source",
				};
			}
			this.savedRepresentation = null;
			return {
				editable: true,
				representationToSet: null,
			};
		}

		let representationToSet: Representation | null = null;
		if (
			this.savedRepresentation !== null &&
			currentRepresentation === "source"
		) {
			representationToSet = this.savedRepresentation;
		}
		this.savedRepresentation = null;
		return {
			editable: false,
			representationToSet,
		};
	}

	onExplicitRepresentationChange(): void {
		this.savedRepresentation = null;
	}

	reset(): void {
		this.savedRepresentation = null;
	}

	getSavedRepresentation(): Representation | null {
		return this.savedRepresentation;
	}
}
