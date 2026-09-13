// Left column: preview files with status filter, text filter, and empty states.
// Pure UI — inventory (manifest + reconciliation) lives in `file-actions.ts`.

import { Notice, setIcon } from "obsidian";

import type { InventoryEntry } from "./file-actions.ts";
import type { FolderLocation, Status } from "./types.ts";

export type StatusFilter = FolderLocation | "all";

function pdf2mdCommand(previewFolder: string): string {
	return `python .ocr-bench/pdf2md.py "raw/…/file.pdf" --out ${previewFolder}`;
}

interface FilterOption {
	value: StatusFilter;
	label: string;
}

const FILTERS: readonly FilterOption[] = [
	{ value: "open", label: "Open" },
	{ value: "accepted", label: "Accepted" },
	{ value: "rejected", label: "Rejected" },
	{ value: "all", label: "All" },
];

function matchesFilter(status: Status, filter: StatusFilter): boolean {
	if (filter === "all") return true;
	if (filter === "open") return status === "open" || status === "re-created";
	return status === filter;
}

const PROGRESS_THRESHOLD = 5;

const STATUS_GROUPS: ReadonlyArray<{ status: Status; label: string }> = [
	{ status: "re-created", label: "Re-created" },
	{ status: "open", label: "Open" },
	{ status: "accepted", label: "Accepted" },
	{ status: "rejected", label: "Rejected" },
	{ status: "adopted", label: "Adopted" },
];

function groupRank(status: Status): number {
	const index = STATUS_GROUPS.findIndex((group) => group.status === status);
	return index < 0 ? STATUS_GROUPS.length : index;
}

function hasMixedStatuses(entries: readonly InventoryEntry[]): boolean {
	const first = entries[0];
	return first !== undefined && entries.some((item) => item.entry.status !== first.entry.status);
}

export class Sidebar {
	onSelect: ((name: string) => void) | null = null;
	onRefresh: (() => void) | null = null;
	onSettings: (() => void) | null = null;

	private filter: StatusFilter = "open";
	private textFilter = "";
	private inventory: InventoryEntry[] = [];
	private folderMissing = false;
	private selected: string | null = null;
	private header: HTMLElement;
	private progressEl: HTMLElement;
	private progressText: HTMLElement;
	private progressBar: HTMLElement;
	private filterRow: HTMLElement;
	private searchInput: HTMLInputElement;
	private listEl: HTMLElement;
	private emptyEl: HTMLElement;

	constructor(
		root: HTMLElement,
		private previewFolder: () => string,
	) {
		this.header = root.createDiv({ cls: "ocr-liste-kopf" });
		this.header.createSpan({ cls: "ocr-liste-titel", text: "Previews" });
		const refreshBtn = this.header.createEl("button", {
			cls: "ocr-ikonknopf",
			attr: { "aria-label": "Refresh", title: "Refresh" },
		});
		setIcon(refreshBtn.createSpan({ cls: "ocr-ikon" }), "refresh-cw");
		refreshBtn.addEventListener("click", () => this.onRefresh?.());

		this.progressEl = root.createDiv({ cls: "ocr-fortschritt" });
		this.progressText = this.progressEl.createDiv({ cls: "ocr-fortschritt-text" });
		this.progressBar = this.progressEl
			.createDiv({ cls: "ocr-fortschritt-balken" })
			.createDiv({ cls: "ocr-fortschritt-fuellung" });
		this.progressEl.hide();

		this.filterRow = root.createDiv({ cls: "ocr-filterzeile" });
		for (const opt of FILTERS) {
			const btn = this.filterRow.createEl("button", {
				cls: "ocr-chip",
				text: opt.label,
			});
			btn.dataset["filter"] = opt.value;
			btn.addEventListener("click", () => {
				this.filter = opt.value;
				this.render();
			});
		}

		this.searchInput = root.createEl("input", {
			cls: "ocr-suchfeld",
			type: "search",
			placeholder: "Filter…",
			attr: { "aria-label": "Filter by filename" },
		});
		this.searchInput.addEventListener("input", () => {
			this.textFilter = this.searchInput.value.trim().toLowerCase();
			this.render();
		});

		this.listEl = root.createDiv({ cls: "ocr-liste" });
		this.emptyEl = root.createDiv({ cls: "ocr-leer" });
	}

	update(inventory: InventoryEntry[], folderMissing: boolean): void {
		this.inventory = inventory;
		this.folderMissing = folderMissing;
		if (this.selected !== null && !inventory.some((b) => b.name === this.selected)) {
			this.selected = null;
		}
		this.render();
	}

	selectedName(): string | null {
		return this.selected;
	}

	setSelected(name: string | null): void {
		this.selected = name;
		for (const [n, row] of this.rowsMap()) {
			row.toggleClass("ocr-selektiert", n === name);
		}
	}

	private filtered(): InventoryEntry[] {
		const filter = this.filter;
		const text = this.textFilter;
		const entries = this.inventory.filter(
			(b) =>
				matchesFilter(b.entry.status, filter) &&
				(text.length === 0 || b.name.toLowerCase().includes(text)),
		);
		if (!hasMixedStatuses(entries)) return entries;
		return [...entries].sort(
			(a, b) => groupRank(a.entry.status) - groupRank(b.entry.status),
		);
	}

	nextAfter(name: string): string | null {
		const list = this.filtered();
		const idx = list.findIndex((b) => b.name === name);
		return idx >= 0 ? (list[idx + 1]?.name ?? null) : null;
	}

	firstVisible(): string | null {
		return this.filtered()[0]?.name ?? null;
	}

	moveSelection(step: number): string | null {
		const list = this.filtered();
		if (list.length === 0) return null;
		let idx = list.findIndex((b) => b.name === this.selected);
		if (idx < 0) idx = step < 0 ? list.length : -1;
		const target = list[Math.min(Math.max(idx + step, 0), list.length - 1)];
		if (target === undefined) return null;
		this.setSelected(target.name);
		return target.name;
	}

	private render(): void {
		this.updateChips();
		this.updateProgress();
		const filteredList = this.filtered();
		const isEmpty =
			this.folderMissing || this.inventory.length === 0 || filteredList.length === 0;
		this.listEl.toggleClass("ocr-liste-versteckt", isEmpty);
		this.listEl.empty();
		this.emptyEl.empty();
		this.emptyEl.hide();

		if (this.folderMissing) {
			this.emptyState(
				`The preview folder "${this.previewFolder()}" does not exist.`,
				"The folder will be created after the first pdf2md run.",
				"Open settings",
				() => this.onSettings?.(),
			);
			return;
		}
		if (this.inventory.length === 0) {
			const cmd = pdf2mdCommand(this.previewFolder());
			this.emptyState(
				"No preview files in preview folder.",
				"",
				"",
				null,
				cmd,
			);
			return;
		}
		if (filteredList.length === 0) {
			if (this.textFilter.length > 0) {
				this.emptyState(`No matches for "${this.searchInput.value.trim()}".`, "", "", null);
			} else {
				const emptyTexts: Record<StatusFilter, string> = {
					open: "Nothing open. All reviewed.",
					accepted: "Nothing accepted yet.",
					rejected: "Nothing rejected yet.",
					all: "No preview files.",
				};
				this.emptyState(emptyTexts[this.filter], "", "", null);
			}
			return;
		}

		const grouped = hasMixedStatuses(filteredList);
		let lastRank = -1;
		for (const item of filteredList) {
			if (grouped) {
				const rank = groupRank(item.entry.status);
				if (rank !== lastRank) {
					lastRank = rank;
					const count = filteredList.filter(
						(candidate) => candidate.entry.status === item.entry.status,
					).length;
					this.listEl.createDiv({
						cls: "ocr-gruppe",
						text: `${STATUS_GROUPS[rank]?.label ?? item.entry.status} · ${count}`,
					});
				}
			}
			this.buildRow(item);
		}
	}

	private updateProgress(): void {
		const total = this.inventory.length;
		if (total < PROGRESS_THRESHOLD) {
			this.progressEl.hide();
			return;
		}
		const reviewed = this.inventory.filter(
			(item) => item.entry.status === "accepted" || item.entry.status === "rejected",
		).length;
		this.progressEl.show();
		this.progressText.empty();
		this.progressText.createSpan({
			cls: "ocr-fortschritt-zahl",
			text: String(reviewed),
		});
		this.progressText.createSpan({ text: `of ${total} reviewed` });
		this.progressBar.setCssProps({
			"--ocr-fortschritt": `${Math.round((reviewed / total) * 100)}%`,
		});
	}

	private buildRow(b: InventoryEntry): void {
		const row = this.listEl.createDiv({ cls: "ocr-eintrag" });
		row.addClass(`ocr-status-${b.entry.status}`);
		row.toggleClass("ocr-selektiert", b.name === this.selected);
		row.dataset["name"] = b.name;
		row.setAttribute("title", b.file.path);
		row.addEventListener("click", () => {
			this.setSelected(b.name);
			this.onSelect?.(b.name);
		});

		const top = row.createDiv({ cls: "ocr-eintrag-ober" });
		if ((b.entry["pages-ocr"] ?? 0) > 0) {
			top.createSpan({
				cls: "ocr-warnpunkt",
				attr: { "aria-label": "Contains OCR pages — word errors possible", title: "Contains OCR pages — word errors possible" },
			});
		}
		top.createSpan({ cls: "ocr-eintrag-stem", text: b.file.basename });
		if (b.entry.status === "re-created") {
			const prev = b.entry.previous;
			const badge = top.createSpan({ cls: "ocr-badge ocr-badge-neu", text: "Re-created" });
			badge.setAttribute(
				"aria-label",
				prev !== null
					? `Previous ${prev.status} (${prev["ocr-date"] ?? "date unknown"})`
					: "Previous decision unknown",
			);
		}

		const sub = this.subline(b.entry);
		if (sub.length > 0) {
			row.createDiv({ cls: "ocr-eintrag-unter", text: sub });
		}
	}

	private subline(e: InventoryEntry["entry"]): string {
		const parts: string[] = [];
		if (e.pages !== null) parts.push(`${e.pages} p.`);
		if ((e["pages-ocr"] ?? 0) > 0) parts.push(`${e["pages-ocr"]} OCR`);
		if ((e["pages-diagram"] ?? 0) > 0) {
			parts.push(`${e["pages-diagram"]} Diagram`);
		}
		return parts.join(" · ");
	}

	private updateChips(): void {
		const count = (filter: StatusFilter) =>
			this.inventory.filter((b) => matchesFilter(b.entry.status, filter)).length;
		for (const opt of FILTERS) {
			const chip = this.filterRow.querySelector<HTMLElement>(
				`[data-filter="${opt.value}"]`,
			);
			if (chip === null) continue;
			chip.toggleClass("ocr-chip-aktiv", this.filter === opt.value);
			chip.setText(`${opt.label} (${count(opt.value)})`);
		}
	}

	private emptyState(
		title: string,
		subtitle: string,
		btnText: string,
		btnAction: (() => void) | null,
		command?: string,
	): void {
		this.emptyEl.show();
		this.emptyEl.createDiv({ cls: "ocr-leer-titel", text: title });
		if (subtitle.length > 0) {
			this.emptyEl.createDiv({ cls: "ocr-leer-untertitel", text: subtitle });
		}
		if (command !== undefined) {
			const pre = this.emptyEl.createEl("pre", { cls: "ocr-kopierbefehl" });
			pre.createEl("code", { text: command });
			pre.addEventListener("click", () => {
				navigator.clipboard
					.writeText(command)
					.then(() => new Notice("Command copied — insert PDF path."))
					.catch(() => new Notice("Copy failed."));
			});
		}
		if (btnText.length > 0 && btnAction !== null) {
			this.emptyEl.createEl("button", { cls: "ocr-knopf", text: btnText })
				.addEventListener("click", () => btnAction());
		}
	}

	private rowsMap(): Map<string, HTMLElement> {
		const m = new Map<string, HTMLElement>();
		for (const row of this.listEl.querySelectorAll<HTMLElement>(".ocr-eintrag")) {
			const name = row.dataset["name"];
			if (name !== undefined) m.set(name, row);
		}
		return m;
	}
}
