import { spawn } from "node:child_process";
import { cp, mkdir, readFile, realpath, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";

import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

import { runLoggedCommand } from "./command-log.js";


const ALLOWED_AGENTCAD_COMMANDS = new Set([
  "run",
  "inspect",
  "render",
  "diff",
  "context",
  "docs",
  "measure",
  "check-spec",
]);


export function resolveInside(root, relativePath) {
  const resolvedRoot = path.resolve(root);
  const resolved = path.resolve(resolvedRoot, relativePath);
  if (resolved !== resolvedRoot && !resolved.startsWith(`${resolvedRoot}${path.sep}`)) {
    throw new Error(`Path is outside trial workspace: ${relativePath}`);
  }
  return resolved;
}


export function runProcess(executable, args, { cwd, timeoutMs = 120_000, env = {} } = {}) {
  return new Promise((resolve, reject) => {
    const started = performance.now();
    const ownsProcessGroup = process.platform !== "win32";
    const child = spawn(executable, args, {
      cwd,
      detached: ownsProcessGroup,
      env: { ...process.env, ...env },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    let forceKillTimer;
    const killProcessTree = (signal) => {
      try {
        if (ownsProcessGroup && child.pid) process.kill(-child.pid, signal);
        else child.kill(signal);
      } catch (error) {
        if (error.code !== "ESRCH") throw error;
      }
    };
    child.on("error", (error) => {
      clearTimeout(timer);
      clearTimeout(forceKillTimer);
      reject(error);
    });
    const timer = setTimeout(() => {
      timedOut = true;
      killProcessTree("SIGTERM");
      forceKillTimer = setTimeout(() => killProcessTree("SIGKILL"), 2_000);
      forceKillTimer.unref();
    }, timeoutMs);
    child.on("close", (code, signal) => {
      clearTimeout(timer);
      clearTimeout(forceKillTimer);
      resolve({
        exitCode: code ?? -1,
        signal,
        timedOut,
        stdout,
        stderr,
        durationMs: Math.round(performance.now() - started),
      });
    });
  });
}


async function imageContent(imagePath) {
  try {
    const bytes = await readFile(imagePath);
    return { type: "image", data: bytes.toString("base64"), mimeType: "image/png" };
  } catch {
    return null;
  }
}


function referencedPreview(stdout, workDir) {
  try {
    const payload = JSON.parse(stdout);
    const preview = payload.preview;
    return typeof preview === "string" ? resolveInside(workDir, preview) : null;
  } catch {
    return null;
  }
}


function errorRecord(error) {
  return { name: error.name, message: error.message, stack: error.stack };
}


function parseJson(stdout) {
  try {
    return JSON.parse(stdout);
  } catch {
    return null;
  }
}


export function createCandidateTools({
  workDir,
  resultDir,
  agentcadBin,
  candidateBin = agentcadBin,
  evaluatorBin = candidateBin,
  candidateProvider = "agentcad-cli",
  recordEvent,
  recordCommand = async () => {},
  runCommand = runProcess,
  commandTimeoutMs = 120_000,
}) {
  const state = {
    submitted: false,
    submittedPath: null,
    previewPath: null,
    validationPath: null,
    invalidSubmissions: [],
    harnessFailure: null,
  };

  const failHarness = async (stage, toolCallId, error) => {
    state.harnessFailure = { stage, error: errorRecord(error) };
    await recordEvent({
      type: "harness_failure",
      stage,
      tool_call_id: toolCallId,
      error,
    });
  };

  const agentcad = defineTool({
    name: "agentcad",
    label: "AgentCAD",
    description: "Run one allowlisted AgentCAD command in the isolated trial workspace.",
    promptSnippet: "agentcad: build, inspect, measure, and render CAD through AgentCAD",
    promptGuidelines: [
      "Use AgentCAD for all CAD execution; unrestricted shell and Python execution are unavailable.",
      "Use the AgentCAD CLI's own help, errors, and docs to discover its interface.",
    ],
    parameters: Type.Object({
      command: Type.String({ description: "One of run, inspect, render, diff, context, docs, measure, check-spec" }),
      args: Type.Optional(Type.Array(Type.String(), { description: "Arguments after the AgentCAD command" })),
    }),
    executionMode: "sequential",
    execute: async (toolCallId, params) => {
      if (!ALLOWED_AGENTCAD_COMMANDS.has(params.command)) {
        return {
          content: [{ type: "text", text: `AgentCAD command '${params.command}' is not allowed.` }],
          details: { allowed: [...ALLOWED_AGENTCAD_COMMANDS] },
        };
      }

      // Forward exactly what the candidate requested. Mutating these arguments here
      // makes a wrapper defect indistinguishable from a candidate command failure.
      const args = [params.command, ...(params.args ?? [])];
      let execution;
      try {
        const envOverrides = { AGENTCAD_DAEMON: "1" };
        execution = await runLoggedCommand({
          runCommand,
          executable: candidateBin,
          args,
          options: { cwd: workDir, timeoutMs: commandTimeoutMs, env: envOverrides },
          command: {
            actor: "candidate",
            phase: "tool",
            toolCallId,
            executable: candidateBin,
            args,
            cwd: workDir,
            timeoutMs: commandTimeoutMs,
            envOverrides,
          },
          recordCommand,
        });
      } catch (error) {
        await failHarness("candidate_tool_bridge", toolCallId, error);
        return {
          content: [{ type: "text", text: "The trial harness failed while invoking AgentCAD." }],
          details: { harnessFailure: true },
        };
      }
      await recordEvent({
        type: "candidate_command",
        tool_call_id: toolCallId,
        command: [candidateBin, ...args],
        ...execution,
      });

      const status = execution.exitCode === 0 && !execution.timedOut ? "ok" : "failed";
      const text = [
        `AgentCAD ${params.command}: ${status} (${(execution.durationMs / 1000).toFixed(1)}s)`,
        execution.stdout.trim() && `stdout:\n${execution.stdout.trim()}`,
        execution.stderr.trim() && `stderr:\n${execution.stderr.trim()}`,
      ].filter(Boolean).join("\n\n");
      const content = [{ type: "text", text }];
      const previewPath = referencedPreview(execution.stdout, workDir);
      if (previewPath) {
        const preview = await imageContent(previewPath);
        if (preview) content.push(preview);
      }
      return { content, details: execution };
    },
  });

  const pythonBuild123d = defineTool({
    name: "python_build123d",
    label: "Python build123d",
    description: "Execute one Python script from the isolated trial workspace with the candidate's build123d environment.",
    promptSnippet: "python_build123d: execute a workspace Python script that builds and exports CAD",
    promptGuidelines: [
      "Write build123d scripts in the trial workspace and execute them with python_build123d.",
      "The script must export the STEP file that you pass to submit_result.",
      "Unrestricted shell execution is unavailable.",
    ],
    parameters: Type.Object({
      script_path: Type.String({ description: "Python script path relative to the trial workspace" }),
      args: Type.Optional(Type.Array(Type.String(), { description: "Optional arguments passed after the script path" })),
    }),
    executionMode: "sequential",
    execute: async (toolCallId, params) => {
      let scriptPath;
      try {
        scriptPath = resolveInside(workDir, params.script_path);
        scriptPath = resolveInside(await realpath(workDir), await realpath(scriptPath));
        const scriptStat = await stat(scriptPath);
        if (!scriptStat.isFile() || path.extname(scriptPath).toLowerCase() !== ".py") {
          throw new Error("python_build123d requires a Python script in the trial workspace");
        }
      } catch (error) {
        return {
          content: [{ type: "text", text: `Python script rejected: ${error.message}` }],
          details: { accepted: false },
        };
      }

      const args = [scriptPath, ...(params.args ?? [])];
      let execution;
      try {
        execution = await runLoggedCommand({
          runCommand,
          executable: candidateBin,
          args,
          options: { cwd: workDir, timeoutMs: commandTimeoutMs },
          command: {
            actor: "candidate",
            phase: "tool",
            toolCallId,
            executable: candidateBin,
            args,
            cwd: workDir,
            timeoutMs: commandTimeoutMs,
            envOverrides: {},
          },
          recordCommand,
        });
      } catch (error) {
        await failHarness("candidate_tool_bridge", toolCallId, error);
        return {
          content: [{ type: "text", text: "The trial harness failed while invoking Python." }],
          details: { harnessFailure: true },
        };
      }
      await recordEvent({
        type: "candidate_command",
        tool_call_id: toolCallId,
        command: [candidateBin, ...args],
        ...execution,
      });

      const status = execution.exitCode === 0 && !execution.timedOut ? "ok" : "failed";
      const text = [
        `Python build123d: ${status} (${(execution.durationMs / 1000).toFixed(1)}s)`,
        execution.stdout.trim() && `stdout:\n${execution.stdout.trim()}`,
        execution.stderr.trim() && `stderr:\n${execution.stderr.trim()}`,
      ].filter(Boolean).join("\n\n");
      return { content: [{ type: "text", text }], details: execution };
    },
  });

  const submitResult = defineTool({
    name: "submit_result",
    label: "Submit result",
    description: "Validate and submit the final STEP file, preserving its complete candidate directory.",
    promptSnippet: "submit_result: finish the trial with the selected output.step",
    promptGuidelines: [
      "Call submit_result when the candidate is complete, then stop only if it is accepted.",
      "The harness validates STEP geometry. If submission is rejected, fix the model and submit again.",
    ],
    parameters: Type.Object({
      step_path: Type.String({ description: "STEP path relative to the trial workspace" }),
    }),
    executionMode: "sequential",
    execute: async (toolCallId, params) => {
      let source;
      try {
        source = resolveInside(workDir, params.step_path);
      } catch (error) {
        return { content: [{ type: "text", text: error.message }], details: { accepted: false } };
      }
      let sourceStat;
      try {
        source = resolveInside(await realpath(workDir), await realpath(source));
        sourceStat = await stat(source);
      } catch (error) {
        return {
          content: [{
            type: "text",
            text: error.message.includes("outside trial workspace")
              ? `Submission rejected: ${error.message}`
              : `Submission rejected: STEP file does not exist: ${params.step_path}`,
          }],
          details: { accepted: false },
        };
      }
      if (!sourceStat.isFile() || !source.toLowerCase().endsWith(".step")) {
        return {
          content: [{ type: "text", text: "Submission rejected: submitted path must be a STEP file" }],
          details: { accepted: false },
        };
      }

      let validationExecution;
      try {
        const args = ["inspect", source, "--summary"];
        const envOverrides = { AGENTCAD_DAEMON: "1" };
        validationExecution = await runLoggedCommand({
          runCommand,
          executable: evaluatorBin,
          args,
          options: { cwd: workDir, timeoutMs: commandTimeoutMs, env: envOverrides },
          command: {
            actor: "evaluator",
            phase: "submission_validation",
            toolCallId,
            executable: evaluatorBin,
            args,
            cwd: workDir,
            timeoutMs: commandTimeoutMs,
            envOverrides,
          },
          recordCommand,
        });
      } catch (error) {
        await failHarness("submission_validation", toolCallId, error);
        return {
          content: [{ type: "text", text: "The trial harness failed while validating the submitted STEP." }],
          details: { accepted: false, harnessFailure: true },
        };
      }

      const validation = parseJson(validationExecution.stdout);
      await recordEvent({
        type: "submission_validation",
        tool_call_id: toolCallId,
        source,
        command: [evaluatorBin, "inspect", source, "--summary"],
        ...validationExecution,
        validation,
      });

      if (validationExecution.timedOut || !validation) {
        const error = new Error(
          validationExecution.timedOut
            ? "AgentCAD STEP validation timed out"
            : "AgentCAD STEP validation did not return a JSON response",
        );
        await failHarness("submission_validation", toolCallId, error);
        return {
          content: [{ type: "text", text: "The trial harness could not validate the submitted STEP." }],
          details: { accepted: false, harnessFailure: true },
        };
      }

      const validGeometry = validation.status === "success"
        && validation.is_valid === true
        && Number.isInteger(validation.solid_count)
        && validation.solid_count >= 1;
      if (validation.status === "success" && validationExecution.exitCode !== 0) {
        const error = new Error("AgentCAD reported successful validation with a non-zero exit code");
        await failHarness("submission_validation", toolCallId, error);
        return {
          content: [{ type: "text", text: "The trial harness received an inconsistent STEP validation result." }],
          details: { accepted: false, harnessFailure: true },
        };
      }
      if (!validGeometry) {
        const sequence = state.invalidSubmissions.length + 1;
        const rejectedDir = path.join(resultDir, "rejected", String(sequence).padStart(3, "0"));
        const rejection = {
          sequence,
          source,
          reason: "invalid_step_geometry",
          validation,
        };
        try {
          await mkdir(rejectedDir, { recursive: true });
          await cp(source, path.join(rejectedDir, "output.step"));
          await writeFile(
            path.join(rejectedDir, "validation.json"),
            `${JSON.stringify(rejection, null, 2)}\n`,
          );
        } catch (error) {
          await failHarness("bundle_artifact_copy", toolCallId, error);
          return {
            content: [{ type: "text", text: "The trial harness failed while preserving a rejected submission." }],
            details: { accepted: false, harnessFailure: true },
          };
        }
        state.invalidSubmissions.push(rejection);
        await recordEvent({
          type: "candidate_submission_rejected",
          tool_call_id: toolCallId,
          ...rejection,
          preserved_at: rejectedDir,
        });
        return {
          content: [{
            type: "text",
            text: `Submission rejected: AgentCAD did not find valid solid geometry (${validation.message ?? validation.status}). Fix the model, run it successfully, and submit the resulting STEP.`,
          }],
          details: { accepted: false, reason: rejection.reason, validation },
        };
      }

      const canonicalOutput = path.join(resultDir, "output.step");
      const canonicalPreview = path.join(resultDir, "preview.png");
      const validationPath = path.join(resultDir, "validation.json");
      const stagingDir = path.join(resultDir, ".submission-staging");
      const stagedOutput = path.join(stagingDir, "output.step");
      const stagedPreview = path.join(stagingDir, "preview.png");
      const stagedValidation = path.join(stagingDir, "validation.json");
      try {
        await mkdir(stagingDir, { recursive: true });
        await cp(source, stagedOutput);
        await writeFile(stagedValidation, `${JSON.stringify(validation, null, 2)}\n`);
      } catch (error) {
        await failHarness("bundle_artifact_copy", toolCallId, error);
        return {
          content: [{ type: "text", text: "The trial harness failed while preserving the submitted artifact." }],
          details: { accepted: false, harnessFailure: true },
        };
      }

      let renderExecution;
      try {
        const args = ["render", stagedOutput, "--view", "iso", "--name", "preview"];
        const envOverrides = { AGENTCAD_DAEMON: "1" };
        renderExecution = await runLoggedCommand({
          runCommand,
          executable: evaluatorBin,
          args,
          options: { cwd: stagingDir, timeoutMs: commandTimeoutMs, env: envOverrides },
          command: {
            actor: "evaluator",
            phase: "canonical_preview",
            toolCallId,
            executable: evaluatorBin,
            args,
            cwd: stagingDir,
            timeoutMs: commandTimeoutMs,
            envOverrides,
          },
          recordCommand,
        });
      } catch (error) {
        await failHarness("canonical_preview", toolCallId, error);
        return {
          content: [{ type: "text", text: "The trial harness failed while rendering the canonical preview." }],
          details: { accepted: false, harnessFailure: true },
        };
      }
      const render = parseJson(renderExecution.stdout);
      await recordEvent({
        type: "canonical_preview",
        tool_call_id: toolCallId,
        command: [evaluatorBin, "render", stagedOutput, "--view", "iso", "--name", "preview"],
        ...renderExecution,
        render,
      });
      let previewStat;
      try {
        previewStat = await stat(stagedPreview);
      } catch {
        previewStat = null;
      }
      if (
        renderExecution.timedOut
        || renderExecution.exitCode !== 0
        || render?.status !== "success"
        || !previewStat?.isFile()
      ) {
        const error = new Error("AgentCAD did not produce the canonical preview");
        await failHarness("canonical_preview", toolCallId, error);
        return {
          content: [{ type: "text", text: "The trial harness could not render the canonical preview." }],
          details: { accepted: false, harnessFailure: true },
        };
      }

      try {
        await cp(stagedOutput, canonicalOutput);
        await cp(stagedPreview, canonicalPreview);
        await cp(stagedValidation, validationPath);
        await cp(path.dirname(source), path.join(resultDir, "candidate"), { recursive: true });
        await rm(stagingDir, { recursive: true, force: true });
      } catch (error) {
        await failHarness("bundle_artifact_copy", toolCallId, error);
        return {
          content: [{ type: "text", text: "The trial harness failed while publishing the accepted submission." }],
          details: { accepted: false, harnessFailure: true },
        };
      }

      try {
        state.submitted = true;
        state.submittedPath = source;
        state.previewPath = canonicalPreview;
        state.validationPath = validationPath;
        await recordEvent({
          type: "candidate_submission",
          tool_call_id: toolCallId,
          source,
          canonical_output: canonicalOutput,
          canonical_preview: canonicalPreview,
          validation: validationPath,
        });
        return {
          content: [{ type: "text", text: "Final STEP validated, rendered, and accepted into the trial bundle. Stop now." }],
          details: { accepted: true, source, validation, preview: canonicalPreview },
        };
      } catch (error) {
        await failHarness("bundle_event_recording", toolCallId, error);
        return {
          content: [{ type: "text", text: "The trial harness failed while recording the accepted submission." }],
          details: { accepted: false, harnessFailure: true },
        };
      }
    },
  });

  const executionTool = candidateProvider === "build123d-python" ? pythonBuild123d : agentcad;
  return { tools: [executionTool, submitResult], state };
}
