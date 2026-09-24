// install-plugin.sh and setup.sh against the plugin id in manifest.json
// (Issue #104): the id lives in the manifest only, and a fresh install into a
// vault leaves the plugin copied and enabled under exactly that id.

import assert from "node:assert/strict";
import { test } from "node:test";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const PLUGIN_DIR = join(dirname(fileURLToPath(import.meta.url)), "..");
const REPO = join(PLUGIN_DIR, "..");
const SCRIPT = join(PLUGIN_DIR, "install-plugin.sh");
const MANIFEST_ID = (JSON.parse(readFileSync(join(PLUGIN_DIR, "manifest.json"), "utf8")) as { id: string }).id;
/** The id before the English rename (f220066); installs may still carry it. */
const LEGACY_ID = "ocr-vorschau";
/** The config folder install-plugin.sh and setup.sh write to. */
const CONFIG_DIR = ".obsidian";

function install(vault: string, ...args: string[]) {
	return spawnSync("bash", [SCRIPT, ...args], {
		env: { ...process.env, VAULT_ROOT: vault },
		encoding: "utf8",
	});
}

function withVault(fn: (vault: string) => void): void {
	const vault = mkdtempSync(join(tmpdir(), "ocr-vault-"));
	try {
		mkdirSync(join(vault, CONFIG_DIR));
		fn(vault);
	} finally {
		rmSync(vault, { recursive: true, force: true });
	}
}

function enabled(vault: string): unknown {
	return JSON.parse(readFileSync(join(vault, CONFIG_DIR, "community-plugins.json"), "utf8"));
}

test("--print-id reports the id from manifest.json", () => {
	const result = spawnSync("bash", [SCRIPT, "--print-id"], { encoding: "utf8" });
	assert.equal(result.status, 0, result.stderr);
	assert.equal(result.stdout.trim(), MANIFEST_ID);
});

test("--enable copies the plugin and enables it under the manifest id", () => {
	withVault((vault) => {
		const result = install(vault, "--enable");
		assert.equal(result.status, 0, result.stdout + result.stderr);
		for (const file of ["main.js", "manifest.json", "styles.css"]) {
			assert.ok(existsSync(join(vault, CONFIG_DIR, "plugins", MANIFEST_ID, file)), file);
		}
		assert.deepEqual(enabled(vault), [MANIFEST_ID]);
	});
});

test("--enable is idempotent and keeps other plugins", () => {
	withVault((vault) => {
		writeFileSync(join(vault, CONFIG_DIR, "community-plugins.json"), JSON.stringify(["other"]));
		assert.equal(install(vault, "--enable").status, 0);
		assert.equal(install(vault, "--enable").status, 0);
		assert.deepEqual(enabled(vault), ["other", MANIFEST_ID]);
	});
});

test("--enable disables the legacy id so two copies never run side by side", () => {
	withVault((vault) => {
		writeFileSync(
			join(vault, CONFIG_DIR, "community-plugins.json"),
			JSON.stringify(["other", LEGACY_ID]),
		);
		assert.equal(install(vault, "--enable").status, 0);
		assert.deepEqual(enabled(vault), ["other", MANIFEST_ID]);
	});
});

test("without --enable, community-plugins.json stays untouched", () => {
	withVault((vault) => {
		assert.equal(install(vault).status, 0);
		assert.equal(existsSync(join(vault, CONFIG_DIR, "community-plugins.json")), false);
	});
});

test("setup.sh and install-plugin.sh do not hard-code a plugin id", () => {
	for (const script of [join(REPO, "setup.sh"), SCRIPT]) {
		const text = readFileSync(script, "utf8");
		assert.equal(text.includes(`"${MANIFEST_ID}"`), false, `${script} hard-codes "${MANIFEST_ID}"`);
		assert.equal(/plugins\/ocr-/.test(text), false, `${script} hard-codes a plugin folder`);
	}
	assert.equal(readFileSync(join(REPO, "setup.sh"), "utf8").includes(LEGACY_ID), false);
});
