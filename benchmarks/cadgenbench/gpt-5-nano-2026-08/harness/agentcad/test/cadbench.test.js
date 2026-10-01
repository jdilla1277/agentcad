import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { EventEmitter } from "node:events";
import { mkdtemp, mkdir, readFile, symlink, writeFile } from "node:fs/promises";
import { promisify } from "node:util";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  keepAwakeEvidence,
  main,
  runWithKeepAwake,
  shouldUseKeepAwake,
} from "../src/cadbench.js";


const execFileAsync = promisify(execFile);


function outputBuffer() {
  let value = "";
  return {
    stream: { write: (chunk) => { value += chunk; } },
    read: () => value,
  };
}


async function fixtureWorkspace() {
  const root = await mkdtemp(path.join(os.tmpdir(), "cadbench-workspace-"));
  const dataDir = path.join(root, "fixture-data");
  const fixtureDir = path.join(dataDir, "101");
  await mkdir(fixtureDir, { recursive: true });
  await writeFile(path.join(fixtureDir, "description.yaml"), [
    "description: Reproduce this object.",
    "input_files:",
    "  - input.png",
    "",
  ].join("\n"));
  await writeFile(path.join(fixtureDir, "input.png"), "image bytes");
  return { root, dataDir };
}


async function initialize(root, dataDir) {
  const output = outputBuffer();
  const exitCode = await main([
    "init",
    "--model", "openai/gpt-5-nano",
    "--data-dir", dataDir,
    "--evaluator-bin", "/fixed/evaluator/agentcad",
  ], { cwd: root, stdout: output.stream });
  assert.equal(exitCode, 0);
  return output.read();
}


test("cadbench init creates a minimal fresh workspace", async () => {
  const { root, dataDir } = await fixtureWorkspace();
  const output = await initialize(root, dataDir);
  const config = JSON.parse(await readFile(path.join(root, "cadbench.json"), "utf8"));

  assert.match(output, /Initialized CADBench workspace/);
  assert.equal(config.model.id, "gpt-5-nano");
  assert.equal(config.data_dir, dataDir);
  assert.equal(config.evaluation.validator.executable, "/fixed/evaluator/agentcad");
  assert.equal(
    await readFile(path.join(root, ".gitignore"), "utf8"),
    ".env\nruns/\nexperiments/\ncandidate-resources/\n",
  );
});


test("cadbench runs when invoked through an npm-style symlink", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "cadbench-symlink-"));
  const executable = path.join(root, "cadbench");
  await symlink(path.resolve("src/cadbench.js"), executable);

  const { stdout } = await execFileAsync(executable, ["--help"]);

  assert.match(stdout, /cadbench init/);
  assert.match(stdout, /cadbench run/);
});


test("macOS CAD runs automatically request a keep-awake wrapper", () => {
  assert.equal(shouldUseKeepAwake(["run", "101"], { platform: "darwin", env: {} }), true);
  assert.equal(shouldUseKeepAwake(["run-all"], { platform: "darwin", env: {} }), true);
  assert.equal(shouldUseKeepAwake(["candidate", "list"], { platform: "darwin", env: {} }), false);
  assert.equal(
    shouldUseKeepAwake(["run-all"], {
      platform: "darwin",
      env: { CADBENCH_MACOS_CAFFEINATE: "1" },
    }),
    false,
  );
  assert.equal(shouldUseKeepAwake(["run-all"], { platform: "linux", env: {} }), false);
});


test("keep-awake wrapper launches caffeinate and preserves the child exit code", async () => {
  let invocation;
  const exitCode = await runWithKeepAwake(["run-all", "--candidate", "control"], {
    nodeExecutable: "/node-22/bin/node",
    cliPath: "/worker/cadbench.js",
    env: { OPENAI_API_KEY: "secret" },
    spawnCommand: (executable, args, options) => {
      invocation = { executable, args, options };
      const child = new EventEmitter();
      queueMicrotask(() => child.emit("close", 7, null));
      return child;
    },
  });

  assert.equal(exitCode, 7);
  assert.equal(invocation.executable, "/usr/bin/caffeinate");
  assert.deepEqual(invocation.args, [
    "-ims",
    "/node-22/bin/node",
    "/worker/cadbench.js",
    "run-all",
    "--candidate",
    "control",
  ]);
  assert.equal(invocation.options.stdio, "inherit");
  assert.equal(invocation.options.env.OPENAI_API_KEY, "secret");
  assert.equal(invocation.options.env.CADBENCH_MACOS_CAFFEINATE, "1");
});


test("keep-awake evidence records whether the guard is active", () => {
  assert.deepEqual(
    keepAwakeEvidence({ platform: "darwin", env: { CADBENCH_MACOS_CAFFEINATE: "1" } }),
    {
      status: "active",
      provider: "macos-caffeinate",
      executable: "/usr/bin/caffeinate",
      assertions: ["idle_system_sleep", "system_sleep_on_ac", "disk_idle"],
    },
  );
  assert.deepEqual(
    keepAwakeEvidence({ platform: "darwin", env: {} }),
    { status: "inactive", provider: "macos-caffeinate" },
  );
});


test("cadbench init preserves an existing gitignore", async () => {
  const { root, dataDir } = await fixtureWorkspace();
  await writeFile(path.join(root, ".gitignore"), "notes.txt\n");
  await initialize(root, dataDir);

  assert.equal(
    await readFile(path.join(root, ".gitignore"), "utf8"),
    "notes.txt\n.env\nruns/\nexperiments/\ncandidate-resources/\n",
  );
});


test("cadbench candidate add and list store a loadable candidate", async () => {
  const { root, dataDir } = await fixtureWorkspace();
  await initialize(root, dataDir);
  const added = outputBuffer();
  await main([
    "candidate", "add", "public-040",
    "--agentcad-bin", "/candidate/public/agentcad",
    "--source-type", "pypi",
    "--source", "agentcad==0.4.0",
  ], { cwd: root, stdout: added.stream });
  const listed = outputBuffer();
  await main(["candidate", "list"], { cwd: root, stdout: listed.stream });
  const candidate = JSON.parse(
    await readFile(path.join(root, "candidates", "public-040.json"), "utf8"),
  );

  assert.equal(candidate.provider, "agentcad-cli");
  assert.equal(candidate.executable, "/candidate/public/agentcad");
  assert.deepEqual(candidate.provenance, { type: "pypi", source: "agentcad==0.4.0" });
  assert.equal(listed.read(), "public-040\n");
});


test("cadbench snapshots a candidate skill with integrity metadata", async () => {
  const { root, dataDir } = await fixtureWorkspace();
  await initialize(root, dataDir);
  const skillSource = path.join(root, "distributed-SKILL.md");
  const skillContent = [
    "---",
    "name: agentcad",
    "description: Use AgentCAD for CAD work.",
    "---",
    "Run AgentCAD commands.",
    "",
  ].join("\n");
  await writeFile(skillSource, skillContent);

  await main([
    "candidate", "add", "published-with-skill",
    "--agentcad-bin", "/candidate/public/agentcad",
    "--skill-file", skillSource,
  ], { cwd: root, stdout: outputBuffer().stream });
  const candidate = JSON.parse(
    await readFile(path.join(root, "candidates", "published-with-skill.json"), "utf8"),
  );

  assert.equal(candidate.skill.source_path, skillSource);
  assert.match(candidate.skill.sha256, /^[a-f0-9]{64}$/);
  assert.equal(await readFile(candidate.skill.snapshot_path, "utf8"), skillContent);
});


test("cadbench stores a pure build123d control candidate", async () => {
  const { root, dataDir } = await fixtureWorkspace();
  await initialize(root, dataDir);
  await main([
    "candidate", "add", "control",
    "--provider", "build123d-python",
    "--python-bin", "/control/venv/bin/python",
    "--source-type", "venv",
    "--source", "build123d-only",
  ], { cwd: root, stdout: outputBuffer().stream });
  const candidate = JSON.parse(
    await readFile(path.join(root, "candidates", "control.json"), "utf8"),
  );

  assert.deepEqual(candidate, {
    schema_version: 1,
    id: "control",
    name: "control",
    provider: "build123d-python",
    executable: "/control/venv/bin/python",
    provenance: { type: "venv", source: "build123d-only" },
  });
});


test("cadbench run materializes one fixture with separate candidate and evaluator", async () => {
  const { root, dataDir } = await fixtureWorkspace();
  await initialize(root, dataDir);
  await main([
    "candidate", "add", "internal",
    "--agentcad-bin", "/candidate/internal/agentcad",
  ], { cwd: root, stdout: outputBuffer().stream });
  let receivedSpec;
  const output = outputBuffer();
  const diagnostics = outputBuffer();
  const exitCode = await main([
    "run", "101", "--candidate", "internal",
  ], {
    cwd: root,
    stdout: output.stream,
    stderr: diagnostics.stream,
    environment: { keep_awake: { status: "active", provider: "test" } },
    runTrial: async (spec) => {
      receivedSpec = spec;
      return {
        trial_id: spec.trial_id,
        harness: { status: "ok", stage: null, error: null },
        candidate: { status: "completed", reason: "submitted" },
        submission: { status: "valid", classification: "new_valid_output" },
        artifacts: { command_log: path.join(spec.bundle_dir, "logs", "commands.jsonl") },
      };
    },
  });

  assert.equal(exitCode, 0);
  assert.equal(receivedSpec.fixture.prompt, "Reproduce this object.");
  assert.equal(receivedSpec.fixture.input_files[0].mime_type, "image/png");
  assert.equal(receivedSpec.candidate.executable, "/candidate/internal/agentcad");
  assert.equal(receivedSpec.evaluation.validator.executable, "/fixed/evaluator/agentcad");
  assert.deepEqual(receivedSpec.environment.keep_awake, { status: "active", provider: "test" });
  assert.match(receivedSpec.bundle_dir, /runs\/101-internal-/);
  assert.match(output.read(), /commands\.jsonl/);
  assert.match(diagnostics.read(), /keep-awake guard is active/);
});


test("cadbench run rejects an unknown candidate before allocating a bundle", async () => {
  const { root, dataDir } = await fixtureWorkspace();
  await initialize(root, dataDir);

  await assert.rejects(
    main(["run", "101", "--candidate", "missing"], {
      cwd: root,
      stdout: outputBuffer().stream,
      runTrial: async () => { throw new Error("should not run"); },
    }),
    /Unknown candidate 'missing'/,
  );
});


test("cadbench run-all bounds concurrency and separates harness failures from completion", async () => {
  const { root, dataDir } = await fixtureWorkspace();
  for (const fixtureId of ["102", "103"]) {
    const fixtureDir = path.join(dataDir, fixtureId);
    await mkdir(fixtureDir);
    await writeFile(path.join(fixtureDir, "description.yaml"), `description: Fixture ${fixtureId}.\n`);
  }
  await initialize(root, dataDir);
  await main([
    "candidate", "add", "control",
    "--provider", "build123d-python",
    "--python-bin", "/control/python",
  ], { cwd: root, stdout: outputBuffer().stream });

  let active = 0;
  let maxActive = 0;
  const output = outputBuffer();
  const progress = outputBuffer();
  const exitCode = await main([
    "run-all", "--candidate", "control", "--concurrency", "2",
  ], {
    cwd: root,
    stdout: output.stream,
    stderr: progress.stream,
    environment: { keep_awake: { status: "active", provider: "test" } },
    runTrial: async (spec) => {
      active += 1;
      maxActive = Math.max(maxActive, active);
      await new Promise((resolve) => setTimeout(resolve, 10));
      active -= 1;
      if (spec.fixture.id === "103") throw new Error("worker exploded");
      return {
        trial_id: spec.trial_id,
        harness: { status: "ok", stage: null, error: null },
        candidate: spec.fixture.id === "101"
          ? { status: "completed", reason: "submitted" }
          : { status: "failed", reason: "no_submission" },
        submission: spec.fixture.id === "101"
          ? { status: "valid", classification: "unchanged_input" }
          : null,
      };
    },
  });
  const summary = JSON.parse(output.read());
  const persisted = JSON.parse(await readFile(summary.result_path, "utf8"));
  const runSpec = JSON.parse(
    await readFile(path.join(summary.artifacts.experiment_dir, "run-spec.json"), "utf8"),
  );
  const ledger = (await readFile(summary.artifacts.trial_ledger, "utf8")).trim().split("\n");

  assert.equal(exitCode, 2);
  assert.equal(maxActive, 2);
  assert.deepEqual(summary.counts, {
    attempted: 3,
    eligible_trials: 2,
    completed: 1,
    meaningful_completed: 0,
    candidate_failures: 1,
    harness_failures: 1,
  });
  assert.equal(summary.completion_rate, 0.5);
  assert.equal(summary.meaningful_completion_rate, 0);
  assert.deepEqual(summary.valid_outputs_by_classification, {
    new_valid_output: 0,
    changed_valid_output: 0,
    unchanged_input: 1,
    unclassified_valid_output: 0,
  });
  assert.equal(summary.trials[2].harness.stage, "trial_materialization");
  assert.deepEqual(summary.candidate_failures_by_reason, { no_submission: 1 });
  assert.deepEqual(summary.harness_failures_by_stage, { trial_materialization: 1 });
  assert.equal(summary.usage.total_cost, 0);
  assert.deepEqual(runSpec.environment.keep_awake, { status: "active", provider: "test" });
  assert.equal(persisted.completion_rate, 0.5);
  assert.equal(ledger.length, 3);
  assert.match(progress.read(), /\[3\/3\]/);
});
