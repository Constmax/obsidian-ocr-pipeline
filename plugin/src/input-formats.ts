// Input formats Stage 2 (`pdf2md.py`) accepts.
//
// An image is normalized into a one-page PDF at the pipeline's input boundary
// (Issue #100), so the plugin only widens its file filters — there is no
// second conversion path here and none in the CLI either.
//
// Kept in sync with `INPUT_SUFFIXES` in `pdf2md/conversion.py`. WebP and HEIC
// are missing on purpose: fitz does not open them.
//
// Stage 1 (`bin/`, the searchable copy) stays PDF-only — its column split and
// text-layer checks all assume PDF input.
//
// Pure module: no imports from `obsidian` so it stays testable without a
// running app.

export const CONVERTIBLE_IMAGE_EXTENSIONS: readonly string[] = [
	"png",
	"jpg",
	"jpeg",
	"tif",
	"tiff",
	"bmp",
];

export const CONVERTIBLE_EXTENSIONS: ReadonlySet<string> = new Set([
	"pdf",
	...CONVERTIBLE_IMAGE_EXTENSIONS,
]);

/** Can Stage 2 convert this file to Markdown? */
export function isConvertible(file: { extension: string }): boolean {
	return CONVERTIBLE_EXTENSIONS.has(file.extension.toLowerCase());
}

/** Is this file an image, i.e. exactly one page and not renderable by pdf.js? */
export function isImageSource(file: { extension: string }): boolean {
	return CONVERTIBLE_IMAGE_EXTENSIONS.includes(file.extension.toLowerCase());
}
