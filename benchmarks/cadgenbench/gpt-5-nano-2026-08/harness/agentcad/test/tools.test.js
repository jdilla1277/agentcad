import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, realpath, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { createCandidateTools, resolveInside, runProcess } from "../src/tools.js";


test("resolveInside rejects paths outside the trial workspace", () => {
  const root = path.resolve("/tmp/trial-work");
  assert.equal(resolveInside(root, "part.step"), path.join(root, "part.step"));
  assert.throws(() => resolveInside(root, "../outside.step"), /outside trial workspace/);
  assert.throws(() => resolveInside(root, "/tmp/outside.step"), /outside trial workspace/);
});


test("runProcess terminates a timed-out command", async () => {
  const result = await runProcess(
    process.execPath,
    ["-e", "setInterval(() => {}, 1000)"],
    { timeoutMs: 25 },
  );

  assert.equal(result.timedOut, true);
  assert.notEqual(result.exitCode, 0);
});


test("agentcad tool rejects commands outside the candidate allowlist", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "pi-trial-tools-"));
  const calls = [];
  const { tools } = createCandidateTools({
    workDir: root,
    resultDir: path.join(root, "result"),
    agentcadBin: "/fake/agentcad",
    recordEvent: async () => {},
    runCommand: async (...args) => {
      calls.push(args);
      return { exitCode: 0, stdout: "{}", stderr: "", durationMs: 1 };
    },
  });

  const agentcad = tools.find((tool) => tool.name === "agentcad");
  const result = await agentcad.execute("call-1", { command: "feedback", args: [] });

  assert.equal(calls.length, 0);
  assert.match(result.content[0].text, /not allowed/);
});


test("agentcad tool forwards candidate arguments without wrapper mutation", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "pi-trial-tools-"));
  const calls = [];
  const { tools } = createCandidateTools({
    workDir: root,
    resultDir: path.join(root, "result"),
    agentcadBin: "/fake/agentcad",
    recordEvent: async () => {},
    runCommand: async (...args) => {
      calls.push(args);
      return { exitCode: 0, stdout: "{}", stderr: "", durationMs: 1 };
    },
  });

  const agentcad = tools.find((tool) => tool.name === "agentcad");
  await agentcad.execute("call-2", {
    command: "run",
    args: ["model.py", "--output", "candidate"],
  });

  assert.equal(calls[0][0], "/fake/agentcad");
  assert.deepEqual(calls[0][1], ["run", "model.py", "--output", "candidate"]);
});


test("agentcad bridge exceptions are isolated as harness failures", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "pi-trial-tools-"));
  const { tools, state } = createCandidateTools({
    workDir: root,
    resultDir: path.join(root, "result"),
    agentcadBin: "/missing/agentcad",
    recordEvent: async () => {},
    runCommand: async () => { throw new Error("spawn failed"); },
  });

  const agentcad = tools.find((tool) => tool.name === "agentcad");
  const response = await agentcad.execute("call-3", { command: "docs", args: [] });

  assert.equal(state.harnessFailure.stage, "candidate_tool_bridge");
  assert.equal(response.details.harnessFailure, true);
});


test("pure build123d tool runs only workspace Python scripts with the selected interpreter", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "pi-trial-python-"));
  const script = path.join(root, "build.py");
  await writeFile(script, "print('build')\n");
  const calls = [];
  const { tools } = createCandidateTools({
    workDir: root,
    resultDir: path.join(root, "result"),
    candidateBin: "/control/venv/bin/python",
    evaluatorBin: "/fixed/evaluator/agentcad",
    candidateProvider: "build123d-python",
    recordEvent: async () => {},
    runCommand: async (...args) => {
      calls.push(args);
      return { exitCode: 0, stdout: "build\n", stderr: "", timedOut: false, durationMs: 1 };
    },
  });

  assert.deepEqual(tools.map((tool) => tool.name), ["python_build123d", "submit_result"]);
  const result = await tools[0].execute("python-1", { script_path: "build.py", args: ["--quality", "high"] });
  assert.equal(calls[0][0], "/control/venv/bin/python");
  assert.deepEqual(calls[0][1], [await realpath(script), "--quality", "high"]);
  assert.match(result.content[0].text, /Python build123d: ok/);
});


test("pure build123d tool rejects scripts outside the workspace", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "pi-trial-python-path-"));
  const outside = path.join(path.dirname(root), "outside.py");
  await writeFile(outside, "print('outside')\n");
  let invoked = false;
  const { tools } = createCandidateTools({
    workDir: root,
    resultDir: path.join(root, "result"),
    candidateBin: "/control/python",
    candidateProvider: "build123d-python",
    recordEvent: async () => {},
    runCommand: async () => { invoked = true; },
  });

  const result = await tools[0].execute("python-path", { script_path: outside });
  assert.equal(invoked, false);
  assert.match(result.content[0].text, /outside trial workspace/);
});


test("submit_result preserves the candidate directory and canonical STEP", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "pi-trial-submit-"));
  const versionDir = path.join(root, "v1_candidate");
  const resultDir = path.join(root, "bundle-result");
  await mkdir(versionDir);
  await writeFile(path.join(versionDir, "output.step"), "STEP DATA");
  await writeFile(path.join(versionDir, "preview.png"), "PNG DATA");

  const executables = [];
  const { tools, state } = createCandidateTools({
    workDir: root,
    resultDir,
    candidateBin: "/candidate/agentcad",
    evaluatorBin: "/fixed/evaluator/agentcad",
    recordEvent: async () => {},
    runCommand: async (executable, args, options) => {
      executables.push(executable);
      if (args[0] === "inspect") {
        return {
          exitCode: 0,
          stdout: JSON.stringify({ status: "success", is_valid: true, solid_count: 1 }),
          stderr: "",
          timedOut: false,
          durationMs: 1,
        };
      }
      if (args[0] === "render") {
        const preview = path.join(options.cwd, "preview.png");
        await writeFile(preview, "CANONICAL PNG");
        return {
          exitCode: 0,
          stdout: JSON.stringify({ status: "success", renders: { preview } }),
          stderr: "",
          timedOut: false,
          durationMs: 1,
        };
      }
      throw new Error(`Unexpected command: ${args.join(" ")}`);
    },
  });
  const submit = tools.find((tool) => tool.name === "submit_result");
  const response = await submit.execute("call-2", { step_path: "v1_candidate/output.step" });

  assert.equal(state.submitted, true);
  assert.deepEqual(executables, ["/fixed/evaluator/agentcad", "/fixed/evaluator/agentcad"]);
  assert.equal(await readFile(path.join(resultDir, "preview.png"), "utf8"), "CANONICAL PNG");
  assert.deepEqual(
    JSON.parse(await readFile(path.join(resultDir, "validation.json"), "utf8")),
    { status: "success", is_valid: true, solid_count: 1 },
  );
  assert.equal(await readFile(path.join(resultDir, "output.step"), "utf8"), "STEP DATA");
  assert.equal(
    await readFile(path.join(resultDir, "candidate", "preview.png"), "utf8"),
    "PNG DATA",
  );
  assert.match(response.content[0].text, /accepted/);
});


test("submit_result rejects malformed STEP content and preserves the evidence", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "pi-trial-invalid-"));
  const resultDir = path.join(root, "bundle-result");
  await writeFile(path.join(root, "output.step"), "(-1)");

  const { tools, state } = createCandidateTools({
    workDir: root,
    resultDir,
    agentcadBin: "/fake/agentcad",
    recordEvent: async () => {},
    runCommand: async () => ({
      exitCode: 1,
      stdout: JSON.stringify({ status: "malformed", message: "STEP file could not be loaded" }),
      stderr: "",
      timedOut: false,
      durationMs: 1,
    }),
  });
  const submit = tools.find((tool) => tool.name === "submit_result");
  const response = await submit.execute("call-4", { step_path: "output.step" });

  assert.equal(state.submitted, false);
  assert.equal(state.invalidSubmissions.length, 1);
  assert.equal(
    await readFile(path.join(resultDir, "rejected", "001", "output.step"), "utf8"),
    "(-1)",
  );
  await assert.rejects(readFile(path.join(resultDir, "output.step")), /ENOENT/);
  assert.match(response.content[0].text, /rejected/i);
  assert.equal(response.details.reason, "invalid_step_geometry");
});


test("submit_result rejects a symlink that escapes the trial workspace", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "pi-trial-symlink-"));
  const workDir = path.join(root, "work");
  await mkdir(workDir);
  const outside = path.join(root, "outside.step");
  await writeFile(outside, "EXTERNAL STEP DATA");
  await symlink(outside, path.join(workDir, "output.step"));
  let invoked = false;

  const { tools, state } = createCandidateTools({
    workDir,
    resultDir: path.join(root, "result"),
    agentcadBin: "/fake/agentcad",
    recordEvent: async () => {},
    runCommand: async () => {
      invoked = true;
      throw new Error("must not be called");
    },
  });
  const submit = tools.find((tool) => tool.name === "submit_result");
  const response = await submit.execute("call-symlink", { step_path: "output.step" });

  assert.equal(response.details.accepted, false);
  assert.match(response.content[0].text, /outside trial workspace/);
  assert.equal(invoked, false);
  assert.equal(state.submitted, false);
});


test("an invalid submission can be replaced by a valid one without losing rejection evidence", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "pi-trial-recovery-"));
  const workDir = path.join(root, "work");
  const resultDir = path.join(root, "bundle-result");
  await mkdir(workDir);
  const source = path.join(workDir, "output.step");
  await writeFile(source, "(-1)");
  let inspectCount = 0;

  const { tools, state } = createCandidateTools({
    workDir,
    resultDir,
    agentcadBin: "/fake/agentcad",
    recordEvent: async () => {},
    runCommand: async (_executable, args, options) => {
      if (args[0] === "inspect") {
        inspectCount += 1;
        const payload = inspectCount === 1
          ? { status: "malformed", message: "STEP file could not be loaded" }
          : { status: "success", is_valid: true, solid_count: 1 };
        return {
          exitCode: inspectCount === 1 ? 1 : 0,
          stdout: JSON.stringify(payload),
          stderr: "",
          timedOut: false,
          durationMs: 1,
        };
      }
      if (args[0] === "render") {
        await writeFile(path.join(options.cwd, "preview.png"), "PNG");
        return {
          exitCode: 0,
          stdout: JSON.stringify({ status: "success" }),
          stderr: "",
          timedOut: false,
          durationMs: 1,
        };
      }
      throw new Error(`Unexpected command: ${args.join(" ")}`);
    },
  });
  const submit = tools.find((tool) => tool.name === "submit_result");

  const rejected = await submit.execute("call-invalid", { step_path: "output.step" });
  await writeFile(source, "VALID STEP DATA");
  const accepted = await submit.execute("call-valid", { step_path: "output.step" });

  assert.equal(rejected.details.accepted, false);
  assert.equal(accepted.details.accepted, true);
  assert.equal(state.invalidSubmissions.length, 1);
  assert.equal(state.submitted, true);
  assert.equal(
    await readFile(path.join(resultDir, "rejected", "001", "output.step"), "utf8"),
    "(-1)",
  );
  assert.equal(await readFile(path.join(resultDir, "output.step"), "utf8"), "VALID STEP DATA");
});


test("canonical preview failure is a harness failure and publishes no canonical output", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "pi-trial-preview-failure-"));
  const resultDir = path.join(root, "bundle-result");
  await writeFile(path.join(root, "output.step"), "VALID STEP DATA");

  const { tools, state } = createCandidateTools({
    workDir: root,
    resultDir,
    agentcadBin: "/fake/agentcad",
    recordEvent: async () => {},
    runCommand: async (_executable, args) => {
      if (args[0] === "inspect") {
        return {
          exitCode: 0,
          stdout: JSON.stringify({ status: "success", is_valid: true, solid_count: 1 }),
          stderr: "",
          timedOut: false,
          durationMs: 1,
        };
      }
      return {
        exitCode: 1,
        stdout: JSON.stringify({ status: "error", message: "renderer unavailable" }),
        stderr: "",
        timedOut: false,
        durationMs: 1,
      };
    },
  });
  const submit = tools.find((tool) => tool.name === "submit_result");
  const response = await submit.execute("call-preview-failure", { step_path: "output.step" });

  assert.equal(response.details.harnessFailure, true);
  assert.equal(state.submitted, false);
  assert.equal(state.harnessFailure.stage, "canonical_preview");
  await assert.rejects(readFile(path.join(resultDir, "output.step")), /ENOENT/);
  await assert.rejects(readFile(path.join(resultDir, "preview.png")), /ENOENT/);
});


test("missing validator JSON is a harness failure rather than a candidate verdict", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "pi-trial-validator-failure-"));
  await writeFile(path.join(root, "output.step"), "STEP DATA");

  const { tools, state } = createCandidateTools({
    workDir: root,
    resultDir: path.join(root, "bundle-result"),
    agentcadBin: "/fake/agentcad",
    recordEvent: async () => {},
    runCommand: async () => ({
      exitCode: 2,
      stdout: "not json",
      stderr: "validator crashed",
      timedOut: false,
      durationMs: 1,
    }),
  });
  const submit = tools.find((tool) => tool.name === "submit_result");
  const response = await submit.execute("call-validator-failure", { step_path: "output.step" });

  assert.equal(response.details.harnessFailure, true);
  assert.equal(state.invalidSubmissions.length, 0);
  assert.equal(state.harnessFailure.stage, "submission_validation");
});
