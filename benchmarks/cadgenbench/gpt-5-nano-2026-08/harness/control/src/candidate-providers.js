import { createCandidateTools, runProcess } from "./tools.js";
import { runLoggedCommand } from "./command-log.js";


const AGENTCAD_PROVIDER_NAMES = new Set(["agentcad-cli", "agentcad"]);
const PYTHON_PROVIDER_NAMES = new Set(["build123d-python", "python-build123d"]);


function candidateSystemPrompt({ executionTool, executionGuidance }) {
  return `You are the candidate in an isolated CADGenBench trial.

Your job is to reproduce the requested CAD object as accurately as possible.
This is a fully autonomous benchmark: no user is available to answer questions or confirm details.
Never ask for clarification or permission. Make reasonable assumptions and begin using tools immediately.
Use build123d scripts and the provided candidate execution tool for CAD work.
The working directory is persistent for this trial. Read and revise your files as needed.

Available tools:
- read: inspect files in the trial workspace
- write: create or replace scripts in the trial workspace
- ${executionTool}: execute CAD work using the selected candidate
- submit_result: validate and submit a final STEP artifact

${executionGuidance}
Build the complete object, inspect the evidence available from your tools, improve it if needed,
then call submit_result with the best STEP path. If submission is rejected, fix the model and
submit again. A response without a successful submit_result call is a failed trial.
After a successful submission, stop. Never use placeholder geometry.`;
}


function normalizeAgentcadCandidate(candidate) {
  const executable = candidate.executable ?? candidate.agentcad_bin;
  if (!executable) throw new Error("agentcad-cli candidate needs executable");
  return {
    ...candidate,
    provider: "agentcad-cli",
    executable,
    runtime: candidate.runtime ?? "build123d",
  };
}


function normalizePythonCandidate(candidate) {
  const executable = candidate.executable ?? candidate.python_bin;
  if (!executable) throw new Error("build123d-python candidate needs executable");
  return {
    ...candidate,
    provider: "build123d-python",
    executable,
  };
}


function loggedSetup({ candidate, args, envOverrides = {}, workDir, commandTimeoutMs, recordCommand }) {
  return runLoggedCommand({
    runCommand: runProcess,
    executable: candidate.executable,
    args,
    options: { cwd: workDir, timeoutMs: commandTimeoutMs, env: envOverrides },
    command: {
      actor: "candidate",
      phase: "preflight",
      executable: candidate.executable,
      args,
      cwd: workDir,
      timeoutMs: commandTimeoutMs,
      envOverrides,
    },
    recordCommand,
  });
}


function agentcadProvider(candidate) {
  const normalized = normalizeAgentcadCandidate(candidate);
  const setupArgs = ["init", "--runtime", normalized.runtime];
  return {
    id: "agentcad-cli",
    candidate: normalized,
    systemPrompt: candidateSystemPrompt({
      executionTool: "agentcad",
      executionGuidance: "Use AgentCAD's own help, errors, and docs to discover its interface and capabilities.",
    }),
    toolNames: ["read", "write", "agentcad", "submit_result"],
    preflightFailureReason: "candidate_preflight_failed",
    preflightCommand: [normalized.executable, ...setupArgs],
    setup({ workDir, commandTimeoutMs, recordCommand }) {
      return loggedSetup({
        candidate: normalized,
        args: setupArgs,
        envOverrides: { AGENTCAD_DAEMON: "1" },
        workDir,
        commandTimeoutMs,
        recordCommand,
      });
    },
    createTools(options) {
      return createCandidateTools({
        ...options,
        candidateBin: normalized.executable,
        candidateProvider: normalized.provider,
      });
    },
  };
}


function pythonProvider(candidate) {
  const normalized = normalizePythonCandidate(candidate);
  const setupArgs = [
    "-c",
    "import build123d; print(getattr(build123d, '__version__', 'build123d-import-ok'))",
  ];
  return {
    id: "build123d-python",
    candidate: normalized,
    systemPrompt: candidateSystemPrompt({
      executionTool: "python_build123d",
      executionGuidance: "Use the selected Python environment directly; no AgentCAD product tools are available.",
    }),
    toolNames: ["read", "write", "python_build123d", "submit_result"],
    preflightFailureReason: "candidate_preflight_failed",
    preflightCommand: [normalized.executable, ...setupArgs],
    setup({ workDir, commandTimeoutMs, recordCommand }) {
      return loggedSetup({
        candidate: normalized,
        args: setupArgs,
        workDir,
        commandTimeoutMs,
        recordCommand,
      });
    },
    createTools(options) {
      return createCandidateTools({
        ...options,
        candidateBin: normalized.executable,
        candidateProvider: normalized.provider,
      });
    },
  };
}


export function loadCandidateProvider(candidate) {
  const providerName = candidate.provider ?? candidate.toolset;
  if (AGENTCAD_PROVIDER_NAMES.has(providerName)) return agentcadProvider(candidate);
  if (PYTHON_PROVIDER_NAMES.has(providerName)) return pythonProvider(candidate);
  throw new Error(`Unknown candidate provider '${providerName}'`);
}
