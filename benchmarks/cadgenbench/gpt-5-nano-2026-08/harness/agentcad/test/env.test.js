import assert from "node:assert/strict";
import { chmod, mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { mkdtemp } from "node:fs/promises";
import test from "node:test";

import {
  cadbenchEnvPaths,
  loadCadbenchEnvironment,
  parseEnvFile,
} from "../src/env.js";


test("dotenv parser accepts plain, quoted, and exported assignments", () => {
  assert.deepEqual(parseEnvFile([
    "# credentials",
    "OPENAI_API_KEY=example-key",
    "export ANTHROPIC_API_KEY='anthropic-key'",
    'GEMINI_API_KEY="gemini-key"',
    "",
  ].join("\n")), {
    OPENAI_API_KEY: "example-key",
    ANTHROPIC_API_KEY: "anthropic-key",
    GEMINI_API_KEY: "gemini-key",
  });
});


test("dotenv parser rejects shell syntax instead of executing it", () => {
  assert.throws(
    () => parseEnvFile("source another-file\n"),
    /Malformed environment assignment/,
  );
});


test("CADBench loads user and workspace env without overriding the shell", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "cadbench-env-"));
  const homeDir = path.join(root, "home");
  const cwd = path.join(root, "workspace");
  await mkdir(path.join(homeDir, ".config", "cadbench"), { recursive: true });
  await mkdir(cwd);
  const userEnv = path.join(homeDir, ".config", "cadbench", "env");
  await writeFile(userEnv, [
    "OPENAI_API_KEY=user-openai",
    "ANTHROPIC_API_KEY=user-anthropic",
    "",
  ].join("\n"));
  await chmod(userEnv, 0o600);
  await writeFile(path.join(cwd, ".env"), [
    "OPENAI_API_KEY=workspace-openai",
    "GEMINI_API_KEY=workspace-gemini",
    "",
  ].join("\n"));
  const env = { ANTHROPIC_API_KEY: "shell-anthropic" };

  const state = await loadCadbenchEnvironment({ cwd, env, homeDir });

  assert.equal(env.OPENAI_API_KEY, "workspace-openai");
  assert.equal(env.ANTHROPIC_API_KEY, "shell-anthropic");
  assert.equal(env.GEMINI_API_KEY, "workspace-gemini");
  assert.deepEqual(state.files.map(({ kind, status }) => ({ kind, status })), [
    { kind: "user", status: "loaded" },
    { kind: "workspace", status: "loaded" },
  ]);
  assert.equal(state.providers.openai.status, "set");
  assert.equal(state.providers.anthropic.status, "set");
  assert.equal(state.providers.google.status, "set");
});


test("an explicit env path replaces the default user path", () => {
  const paths = cadbenchEnvPaths({
    cwd: "/workspace",
    homeDir: "/home/person",
    env: { CADBENCH_ENV_FILE: "secrets/cadbench.env" },
  });

  assert.deepEqual(paths, [
    {
      kind: "explicit",
      path: "/workspace/secrets/cadbench.env",
      required: true,
    },
    {
      kind: "workspace",
      path: "/workspace/.env",
      required: false,
    },
  ]);
});
