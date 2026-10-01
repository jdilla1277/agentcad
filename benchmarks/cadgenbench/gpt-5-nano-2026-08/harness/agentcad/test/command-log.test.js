import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { createCommandLogger, runLoggedCommand } from "../src/command-log.js";


test("command logger preserves exact invocation metadata and separate output", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "cadbench-command-log-"));
  const logger = await createCommandLogger(path.join(root, "logs"));
  await logger.record({
    actor: "candidate",
    phase: "tool",
    toolCallId: "tool-1",
    executable: "/candidate/agentcad",
    args: ["run", "model.py", "--output", "candidate"],
    cwd: root,
    timeoutMs: 12_000,
    envOverrides: { AGENTCAD_DAEMON: "1" },
  }, {
    exitCode: 7,
    signal: null,
    timedOut: false,
    durationMs: 42,
    stdout: "candidate stdout\n",
    stderr: "candidate stderr\n",
  });
  await logger.flush();

  const entries = (await readFile(logger.indexPath, "utf8")).trim().split("\n").map(JSON.parse);
  assert.equal(entries.length, 1);
  assert.deepEqual(entries[0].args, ["run", "model.py", "--output", "candidate"]);
  assert.equal(entries[0].actor, "candidate");
  assert.equal(entries[0].phase, "tool");
  assert.equal(entries[0].exit_code, 7);
  assert.deepEqual(entries[0].env_overrides, { AGENTCAD_DAEMON: "1" });
  assert.equal(await readFile(path.join(root, "logs", entries[0].stdout), "utf8"), "candidate stdout\n");
  assert.equal(await readFile(path.join(root, "logs", entries[0].stderr), "utf8"), "candidate stderr\n");
  assert.equal("env" in entries[0], false);
});


test("a command that cannot spawn is still preserved in the ledger", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "cadbench-command-log-"));
  const logger = await createCommandLogger(path.join(root, "logs"));
  await assert.rejects(runLoggedCommand({
    runCommand: async () => { throw new Error("spawn ENOENT"); },
    executable: "/missing/candidate",
    args: ["init"],
    options: { cwd: root, timeoutMs: 1000 },
    command: {
      actor: "candidate",
      phase: "preflight",
      executable: "/missing/candidate",
      args: ["init"],
      cwd: root,
      timeoutMs: 1000,
      envOverrides: {},
    },
    recordCommand: logger.record,
  }), /spawn ENOENT/);

  const entry = JSON.parse((await readFile(logger.indexPath, "utf8")).trim());
  assert.equal(entry.executable, "/missing/candidate");
  assert.equal(entry.exit_code, -1);
  assert.equal(entry.error.message, "spawn ENOENT");
});
