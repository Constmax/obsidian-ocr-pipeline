// Flat-Config für ESLint 9 mit eslint-plugin-obsidianmd (offizielles
// Regelwerk von Obsidian selbst). Fängt `innerHTML`, nicht abgemeldete
// Listener, abgelöstes DOM und API-Nutzung jenseits der minAppVersion.
import tsparser from "@typescript-eslint/parser";
import { defineConfig } from "eslint/config";
import obsidianmd from "eslint-plugin-obsidianmd";

export default defineConfig([
	// Generierte Artefakte und Laufzeitdaten sind kein Quellcode.
	{
		ignores: ["main.js", "main.js.map", "data.json", "*.tsbuildinfo"],
	},
	...obsidianmd.configs.recommended,
	{
		files: ["**/*.ts"],
		languageOptions: {
			parser: tsparser,
			// Typbewusste Regeln (z.B. no-unsupported-api) brauchen das Projekt.
			parserOptions: { project: "./tsconfig.json" },
		},
		rules: {
			// Die UI ist Deutsch; die englische Satzfall-Regel greift auf
			// deutschen Strings regelmässig daneben.
			"obsidianmd/ui/sentence-case": "off",
			// getSettingDefinitions() braucht Obsidian 1.13+. minAppVersion ist
			// 1.5.7 — dort ist display() die richtige API (und sogar Pflicht).
			"obsidianmd/settings-tab/prefer-setting-definitions": "off",
			// console.error/-warn sind Diagnose; console.log gehört nirgends hin.
			"no-console": ["error", { allow: ["error", "warn"] }],
		},
	},
	{
		// Build-Skript: console.log ist hier die Ausgabe, kein Störgeräusch.
		files: ["esbuild.config.mjs"],
		rules: { "obsidianmd/rule-custom-message": "off" },
	},
	{
		// Die Testdateien laufen unter `node --test`, nicht in Obsidian.
		files: ["test/**/*.ts"],
		rules: {
			"obsidianmd/no-unsupported-api": "off",
			"obsidianmd/platform": "off",
			// node:test() liefert ein Promise, das auf Top-Level nicht
			// abgewartet werden muss — die Registrierung ist der Effekt.
			"@typescript-eslint/no-floating-promises": "off",
			// sync.test.ts baut `window` als Laufzeit-Shim nach — node kennt
			// das Global nicht, genau darum sind die Fensterregeln hier falsch.
			"obsidianmd/no-global-this": "off",
			"obsidianmd/prefer-window-timers": "off",
		},
	},
]);
