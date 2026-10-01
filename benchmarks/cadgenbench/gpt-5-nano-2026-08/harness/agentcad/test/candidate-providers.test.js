import assert from "node:assert/strict";
import test from "node:test";

import { loadCandidateProvider } from "../src/candidate-providers.js";


test("candidate providers share the trial contract without harness-authored AgentCAD syntax", () => {
  const agentcad = loadCandidateProvider({
    id: "published",
    provider: "agentcad-cli",
    executable: "/published/agentcad",
  });
  const control = loadCandidateProvider({
    id: "control",
    provider: "build123d-python",
    executable: "/control/python",
  });

  const sharedContract = [
    "Your job is to reproduce the requested CAD object as accurately as possible.",
    "Never ask for clarification or permission.",
    "A response without a successful submit_result call is a failed trial.",
    "Never use placeholder geometry.",
  ];
  for (const sentence of sharedContract) {
    assert.match(agentcad.systemPrompt, new RegExp(sentence.replaceAll(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    assert.match(control.systemPrompt, new RegExp(sentence.replaceAll(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.doesNotMatch(agentcad.systemPrompt, /--output|no 'build' subcommand|args=\[/);
  assert.deepEqual(agentcad.toolNames, ["read", "write", "agentcad", "submit_result"]);
  assert.deepEqual(control.toolNames, ["read", "write", "python_build123d", "submit_result"]);
  assert.deepEqual(agentcad.preflightCommand, ["/published/agentcad", "init", "--runtime", "build123d"]);
  assert.equal(control.preflightCommand[0], "/control/python");
  assert.equal(control.preflightCommand[1], "-c");
});


test("candidate provider aliases normalize legacy profiles", () => {
  const agentcad = loadCandidateProvider({ toolset: "agentcad", agentcad_bin: "/old/agentcad" });
  const control = loadCandidateProvider({ provider: "python-build123d", python_bin: "/venv/python" });

  assert.equal(agentcad.candidate.provider, "agentcad-cli");
  assert.equal(agentcad.candidate.executable, "/old/agentcad");
  assert.equal(control.candidate.provider, "build123d-python");
  assert.equal(control.candidate.executable, "/venv/python");
});
