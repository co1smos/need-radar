import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, mkdir, writeFile, cp, access } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const sourceRoot = fileURLToPath(new URL("..", import.meta.url));
const mainPath = join(sourceRoot, ".sandcastle", "main.mts");
const tsxCli = fileURLToPath(import.meta.resolve("tsx/cli"));

async function exists(path) {
  try { await access(path); return true; } catch { return false; }
}

async function makeFixture({ existingLock = false } = {}) {
  const root = await mkdtemp(join(tmpdir(), "ralph-failure-"));
  await mkdir(join(root, ".sandcastle"), { recursive: true });
  await cp(join(sourceRoot, ".sandcastle", "implementer-prompt.md"), join(root, ".sandcastle", "implementer-prompt.md"));
  await cp(join(sourceRoot, ".sandcastle", "implementer-schema.json"), join(root, ".sandcastle", "implementer-schema.json"));
  await cp(join(sourceRoot, ".sandcastle", "reviewer-prompt.md"), join(root, ".sandcastle", "reviewer-prompt.md"));
  await cp(join(sourceRoot, ".sandcastle", "reviewer-schema.json"), join(root, ".sandcastle", "reviewer-schema.json"));
  await cp(join(sourceRoot, ".sandcastle", "merger-prompt.md"), join(root, ".sandcastle", "merger-prompt.md"));
  await cp(join(sourceRoot, ".sandcastle", "merger-schema.json"), join(root, ".sandcastle", "merger-schema.json"));
  await cp(join(sourceRoot, ".sandcastle", "workflow-core.mjs"), join(root, ".sandcastle", "workflow-core.mjs"));
  await writeFile(join(root, "package.json"), JSON.stringify({ type: "module" }));
  await writeFile(join(root, "node_modules"), "").catch(() => {});
  await mkdir(join(root, "bin"));
  await mkdir(join(root, "codex-home"));
  await writeFile(join(root, "codex-home", "config.toml"), 'model_provider = "fake"\n[model_providers.fake]\nenv_key = "FAKE_API_KEY"\n');
  const script = (name, body) => writeFile(join(root, "bin", name), `#!/bin/sh\n${body}\n`, { mode: 0o755 });
  await script("git", 'exit 0');
  await script("herdr", 'exit 0');
  await script("codex", 'exit 0');
  await script("unsnooze", 'exit 0');
  await script("npm", 'exit 0');
  await script("gh", `
if [ "$1 $2" = "repo view" ]; then echo 'owner/repo'; exit 0; fi
if [ "$1 $2" = "issue list" ]; then echo 'mock GitHub outage' >&2; exit 23; fi
exit 0`);
  if (existingLock) {
    await mkdir(join(root, ".sandcastle", "orchestrator.lock"));
    await writeFile(join(root, ".sandcastle", "orchestrator.lock", "owner.json"), '{"pid":999}\n');
  }
  return root;
}

function runMain(root) {
  return new Promise((resolveResult) => {
    const args = [
      tsxCli, mainPath,
      "--implementer-harness", "codex", "--implementer-model", "dummy", "--implementer-effort", "low",
      "--reviewer-harness", "codex", "--reviewer-model", "dummy", "--reviewer-effort", "low",
      "--merger-harness", "codex", "--merger-model", "dummy", "--merger-effort", "low",
    ];
    const child = spawn(process.execPath, args, {
      cwd: root,
      env: {
        ...process.env,
        PATH: `${join(root, "bin")}:${process.env.PATH}`,
        HERDR_ENV: "1",
        CODEX_HOME: join(root, "codex-home"),
        FAKE_API_KEY: "fake",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "", stderr = "";
    child.stdout.on("data", (d) => { stdout += d; });
    child.stderr.on("data", (d) => { stderr += d; });
    child.on("close", (code) => resolveResult({ code, stdout, stderr }));
  });
}

test("fatal orchestrator error exits nonzero and releases singleton lock", async () => {
  const root = await makeFixture();
  const result = await runMain(root);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /mock GitHub outage/);
  assert.equal(await exists(join(root, ".sandcastle", "orchestrator.lock")), false);
});

test("second orchestrator is rejected while singleton lock exists", async () => {
  const root = await makeFixture({ existingLock: true });
  const result = await runMain(root);
  assert.notEqual(result.code, 0);
  assert.match(result.stderr, /another Ralph orchestrator already owns this repository/);
  assert.equal(await exists(join(root, ".sandcastle", "orchestrator.lock", "owner.json")), true);
});
