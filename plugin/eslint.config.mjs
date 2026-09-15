// Flat config for ESLint 9 with eslint-plugin-obsidianmd (official
// ruleset from Obsidian itself). Catches `innerHTML`, unregistered
// listeners, detached DOM, and API usage beyond minAppVersion.
import tsparser from "@typescript-eslint/parser";
import { defineConfig } from "eslint/config";
import obsidianmd from "eslint-plugin-obsidianmd";
// Not re-exported by the package entry point; the package has no exports map.
import { DEFAULT_ACRONYMS } from "eslint-plugin-obsidianmd/dist/lib/rules/ui/acronyms.js";

export default defineConfig([
	// Generated artifacts and runtime data are not source code.
	{
		ignores: ["main.js", "main.js.map", "data.json", "*.tsbuildinfo"],
	},
	...obsidianmd.configs.recommended,
	{
		files: ["**/*.ts"],
		languageOptions: {
			parser: tsparser,
			// Type-aware rules (e.g. no-unsupported-api) need the project.
			parserOptions: { project: "./tsconfig.json" },
		},
		rules: {
			"obsidianmd/ui/sentence-case": "off",
			// getSettingDefinitions() requires Obsidian 1.13+. minAppVersion is
			// 1.8.7 — display() is the correct API.
			"obsidianmd/settings-tab/prefer-setting-definitions": "off",
			// console.error/-warn are diagnostics; console.log belongs nowhere.
			"no-console": ["error", { allow: ["error", "warn"] }],
		},
	},
	{
		// Child process conversion runs under Node.js / spawn where window timers do not exist.
		files: ["src/conversion.ts"],
		rules: {
			"obsidianmd/prefer-window-timers": "off",
		},
	},
	{
		// The settings tab follows Obsidian's sentence case. The rule stays off
		// elsewhere until the older UI strings in other files are updated.
		// A custom acronym list replaces the defaults, so OCR is added to them.
		files: ["src/settings.ts"],
		rules: {
			"obsidianmd/ui/sentence-case": ["error", { acronyms: [...DEFAULT_ACRONYMS, "OCR"] }],
		},
	},
	{
		// Build script: console.log is output here, not noise.
		files: ["esbuild.config.mjs"],
		rules: { "obsidianmd/rule-custom-message": "off" },
	},
	{
		// Test files run under `node --test`, not in Obsidian.
		files: ["test/**/*.ts"],
		rules: {
			"obsidianmd/no-unsupported-api": "off",
			"obsidianmd/platform": "off",
			// node:test returns a Promise that doesn't need to be awaited at top level.
			"@typescript-eslint/no-floating-promises": "off",
			// sync.test.ts mocks `window` as a runtime shim.
			"obsidianmd/no-global-this": "off",
			"obsidianmd/prefer-window-timers": "off",
		},
	},
]);
