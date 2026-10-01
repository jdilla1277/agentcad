import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { classifyValidOutput, compactPiEvent, runTrial } from "../src/trial-worker.js";


test("Pi event logging omits streamed message bodies and records compact lifecycle events", () => {
  assert.equal(compactPiEvent({
    type: "message_update",
    message: { content: "a growing model response" },
  }), null);
  assert.deepEqual(compactPiEvent({
    type: "message_end",
    message: { content: "the complete model response" },
  }), {
    type: "pi_event",
    event_type: "message_end",
  });
});


test("valid output classification separates new, changed, and unchanged STEP files", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "pi-trial-classification-"));
  const input = path.join(root, "input.step");
  const unchanged = path.join(root, "unchanged.step");
  const changed = path.join(root, "changed.step");
  const created = path.join(root, "created.step");
  await writeFile(input, "ORIGINAL STEP");
  await writeFile(unchanged, "ORIGINAL STEP");
  await writeFile(changed, "CHANGED STEP");
  await writeFile(created, "NEW STEP");

  const unchangedResult = await classifyValidOutput(unchanged, [{ name: "input.step", path: input }]);
  const changedResult = await classifyValidOutput(changed, [{ name: "input.step", path: input }]);
  const newResult = await classifyValidOutput(created, []);
  const capturedInput = {
    name: "input.step",
    sha256: unchangedResult.comparison.input_steps[0].sha256,
  };
  await writeFile(input, "CANDIDATE MODIFIED ITS WORKING COPY");
  const capturedResult = await classifyValidOutput(unchanged, [capturedInput]);

  assert.equal(unchangedResult.classification, "unchanged_input");
  assert.equal(unchangedResult.comparison.input_steps[0].matches_output, true);
  assert.equal(changedResult.classification, "changed_valid_output");
  assert.equal(changedResult.comparison.input_steps[0].matches_output, false);
  assert.equal(capturedResult.classification, "unchanged_input");
  assert.equal(newResult.classification, "new_valid_output");
  assert.deepEqual(newResult.comparison.input_steps, []);
});


test("runTrial refuses to reuse or modify an existing bundle", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "pi-trial-bundle-"));
  const bundleDir = path.join(root, "existing");
  await mkdir(bundleDir);
  const sentinel = path.join(bundleDir, "trial-result.json");
  await writeFile(sentinel, "original result\n");

  const result = await runTrial({
    trial_id: "immutable-test",
    bundle_dir: bundleDir,
    fixture: { id: "101", prompt: "Build it" },
    model: { provider: "openai", id: "unused" },
    candidate: {
      id: "unused",
      toolset: "agentcad",
      agentcad_bin: "/unused/agentcad",
    },
  });

  assert.equal(result.harness.status, "failed");
  assert.equal(result.harness.stage, "bundle_allocation");
  assert.equal(result.harness.retryable, false);
  assert.equal(result.candidate, null);
  assert.equal(await readFile(sentinel, "utf8"), "original result\n");
});
