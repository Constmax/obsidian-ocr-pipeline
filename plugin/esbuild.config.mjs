import esbuild from "esbuild";
import process from "node:process";
import fs from "node:fs";
import path from "node:path";
import { builtinModules } from "node:module";

const produktion = process.argv[2] === "production";

// Obsidian laedt main.js per require() aus dem Plugin-Ordner. Alles, was die
// App selbst mitbringt, muss extern bleiben — sonst liegt eine zweite Kopie von
// CodeMirror im Bundle und die Editor-Instanzen sprechen nicht mehr miteinander.
const EXTERN = [
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

// Dev-Schleife: liegt OBSIDIAN_PLUGIN_DIR an, werden die drei Artefakte nach
// jedem Rebuild dorthin kopiert. Kein Symlink der Quellen in den Vault — siehe
// die Begruendung in install-plugin.sh.
const ZIEL = process.env.OBSIDIAN_PLUGIN_DIR;

const kopierPlugin = {
	name: "in-vault-kopieren",
	setup(build) {
		build.onEnd((ergebnis) => {
			if (!ZIEL || ergebnis.errors.length) return;
			fs.mkdirSync(ZIEL, { recursive: true });
			for (const datei of ["main.js", "manifest.json", "styles.css"]) {
				if (fs.existsSync(datei)) {
					fs.copyFileSync(datei, path.join(ZIEL, datei));
				}
			}
			console.log(`   → kopiert nach ${ZIEL}`);
		});
	},
};

const kontext = await esbuild.context({
	entryPoints: ["src/main.ts"],
	bundle: true,
	external: EXTERN,
	format: "cjs",
	target: "es2021",
	logLevel: "info",
	sourcemap: produktion ? false : "inline",
	treeShaking: true,
	minify: produktion,
	outfile: "main.js",
	plugins: [kopierPlugin],
});

if (produktion) {
	await kontext.rebuild();
	await kontext.dispose();
} else {
	await kontext.watch();
}
