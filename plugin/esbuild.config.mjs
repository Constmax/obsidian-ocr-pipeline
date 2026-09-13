import esbuild from "esbuild";
import process from "node:process";
import fs from "node:fs";
import path from "node:path";
import { builtinModules } from "node:module";

const production = process.argv[2] === "production";

// Obsidian loads main.js via require() from the plugin folder. Anything supplied
// by the app itself must remain external — otherwise a second copy of CodeMirror
// enters the bundle and editor instances cannot interact.
const EXTERNAL = [
	"obsidian",
	"electron",
	"@codemirror/autocomplete",
	"@codemirror/collab",
	"@codemirror/commands",
	"@codemirror/language",
	"@codemirror/lint",
	"@codemirror/search",
	"@codemirror/state",
	"@codemirror/view",
	"@lezer/common",
	"@lezer/highlight",
	"@lezer/lr",
	...builtinModules,
];

// Dev loop: if OBSIDIAN_PLUGIN_DIR is set, copy artifacts there after each rebuild.
const TARGET = process.env.OBSIDIAN_PLUGIN_DIR;

const copyPlugin = {
	name: "copy-to-vault",
	setup(build) {
		build.onEnd((result) => {
			if (!TARGET || result.errors.length) return;
			fs.mkdirSync(TARGET, { recursive: true });
			for (const file of ["main.js", "manifest.json", "styles.css"]) {
				if (fs.existsSync(file)) {
					fs.copyFileSync(file, path.join(TARGET, file));
				}
			}
			console.log(`   → copied to ${TARGET}`);
		});
	},
};

const context = await esbuild.context({
	entryPoints: ["src/main.ts"],
	bundle: true,
	external: EXTERNAL,
	format: "cjs",
	target: "es2021",
	logLevel: "info",
	sourcemap: production ? false : "inline",
	treeShaking: true,
	minify: production,
	outfile: "main.js",
	plugins: [copyPlugin],
});

if (production) {
	await context.rebuild();
	await context.dispose();
} else {
	await context.watch();
}
