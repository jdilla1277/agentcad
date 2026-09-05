#!/usr/bin/env node

import {
  appendFile,
  copyFile,
  mkdir,
  readFile,
  writeFile,
} from "node:fs/promises";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  createAgentSession,
  createExtensionRuntime,
  ModelRuntime,
  SessionManager,
  SettingsManager,
  loadSkillsFromDir,
} from "@earendil-works/pi-coding-agent";

import { loadCandidateProvider } from "./candidate-providers.js";
import { createCommandLogger } from "./command-log.js";
import { resolveInside } from "./tools.js";


const API_KEY_ENV = {
  openai: "OPENAI_API_KEY",
  anthropic: "ANTHROPIC_API_KEY",
  google: "GEMINI_API_KEY",
  gemini: "GEMINI_API_KEY",
};


function isolatedResourceLoader(systemPrompt, skillResources = { skills: [], diagnostics: [] }) {
  return {
    getExtensions: () => ({ extensions: [], errors: [], runtime: createExtensionRuntime() }),
    getSkills: () => skillResources,
    getPrompts: () => ({ prompts: [], diagnostics: [] }),
    getThemes: () => ({ themes: [], diagnostics: [] }),
    getAgentsFiles: () => ({ agentsFiles: [] }),
    getSystemPrompt: () => systemPrompt,
    getSystemPromptSource: () => undefined,
    getAppendSystemPrompt: () => [],
    getAppendSystemPromptSources: () => [],
    extendResources: () => {},
    reload: async () => {},
  };
}


export async function materializeCandidateSkill(candidate, workDir) {
  if (!candidate.skill) return { skills: [], diagnostics: [], artifact: null };
  const sourcePath = path.resolve(candidate.skill.snapshot_path);
  const actualSha256 = await sha256File(sourcePath);
  if (actualSha256 !== candidate.skill.sha256) {
    throw new Error(
      `Candidate skill integrity check failed: expected ${candidate.skill.sha256}, got ${actualSha256}`,
    );
  }
  const skillsRoot = path.join(workDir, ".pi", "skills");
  const skillDir = path.join(skillsRoot, "agentcad");
  const skillPath = path.join(skillDir, "SKILL.md");
  await mkdir(skillDir, { recursive: true });
  await copyFile(sourcePath, skillPath);
  const loaded = loadSkillsFromDir({ dir: skillsRoot, source: "candidate" });
  if (loaded.skills.length !== 1 || loaded.diagnostics.length > 0) {
    const details = loaded.diagnostics.map((item) => item.message).join("; ") || "no skill found";
    throw new Error(`Candidate skill could not be loaded: ${details}`);
  }
  return {
    ...loaded,
    artifact: { path: skillPath, sha256: actualSha256, name: loaded.skills[0].name },
  };
}


function fixturePrompt(fixture, copiedInputs) {
  const inputNote = copiedInputs.length
    ? `\n\nInput files available in the working directory: ${copiedInputs.join(", ")}.`
    : "";
  return `${fixture.prompt.trim()}${inputNote}`;
}


export function candidatePrompt(fixture, copiedInputs, skills = []) {
  const prompt = fixturePrompt(fixture, copiedInputs);
  if (skills.length === 0) return prompt;
  if (skills.length !== 1) throw new Error("A candidate may select exactly one skill");
  return `/skill:${skills[0].name} ${prompt}`;
}


function json(value) {
  return JSON.stringify(value, (_key, item) => {
    if (typeof item === "bigint") return item.toString();
    if (item instanceof Error) return { name: item.name, message: item.message, stack: item.stack };
    return item;
  });
}


export function compactPiEvent(event) {
  // Pi emits a message_update for every streamed model delta. Each update can
  // contain the full message-so-far (including input images), which makes an
  // event log grow quadratically. The session JSONL is the canonical complete
  // transcript; events.jsonl only needs lifecycle ordering.
  if (event.type === "message_update") return null;
  return { type: "pi_event", event_type: event.type };
}


function isStepPath(filePath) {
  const extension = path.extname(filePath).toLowerCase();
  return extension === ".step" || extension === ".stp";
}


async function sha256File(filePath) {
  return createHash("sha256").update(await readFile(filePath)).digest("hex");
}


export async function classifyValidOutput(outputPath, inputSteps) {
  const outputSha256 = await sha256File(outputPath);
  const comparisons = [];
  for (const input of inputSteps) {
    const inputSha256 = input.sha256 ?? await sha256File(input.path);
    comparisons.push({
      name: input.name,
      sha256: inputSha256,
      matches_output: inputSha256 === outputSha256,
    });
  }
  const classification = comparisons.length === 0
    ? "new_valid_output"
    : comparisons.some((input) => input.matches_output)
      ? "unchanged_input"
      : "changed_valid_output";
  return {
    status: "valid",
    classification,
    comparison: {
      method: "sha256",
      output_sha256: outputSha256,
      input_steps: comparisons,
    },
  };
}


async function copyFixtureInputs(fixture, workDir) {
  const copied = [];
  const images = [];
  const stepInputs = [];
  for (const input of fixture.input_files ?? []) {
    const name = input.name ?? path.basename(input.path);
    const destination = resolveInside(workDir, name);
    await copyFile(input.path, destination);
    copied.push(name);
    if (input.mime_type === "model/step" || isStepPath(name)) {
      // Capture the fixture hash before the candidate can modify its working copy.
      stepInputs.push({ name, sha256: await sha256File(destination) });
    }
    if ((input.mime_type ?? "").startsWith("image/")) {
      images.push({
        type: "image",
        data: (await readFile(destination)).toString("base64"),
        mimeType: input.mime_type,
      });
    }
  }
  return { copied, images, stepInputs };
}


function validateSpec(spec) {
  for (const key of ["trial_id", "bundle_dir", "fixture", "model", "candidate"]) {
    if (spec[key] == null) throw new Error(`Trial spec is missing '${key}'`);
  }
  if (!spec.fixture.id || !spec.fixture.prompt) throw new Error("Fixture needs id and prompt");
  if (!spec.model.provider || !spec.model.id) throw new Error("Model needs provider and id");
  const provider = spec.candidate.provider ?? spec.candidate.toolset;
  if (!provider) throw new Error("Candidate needs provider");
  if (!spec.candidate.executable && !spec.candidate.agentcad_bin && !spec.candidate.python_bin) {
    throw new Error("Candidate needs executable");
  }
}


export async function runTrial(spec) {
  validateSpec(spec);
  const bundleDir = path.resolve(spec.bundle_dir);
  const workDir = path.join(bundleDir, "work");
  const resultDir = path.join(bundleDir, "result");
  const sessionDir = path.join(bundleDir, "pi-session");
  const eventsPath = path.join(bundleDir, "events.jsonl");
  const logsDir = path.join(bundleDir, "logs");
  const startedAt = new Date();
  const trialResult = {
    trial_id: spec.trial_id,
    fixture_id: spec.fixture.id,
    started_at: startedAt.toISOString(),
    finished_at: null,
    harness: { status: "running", stage: "setup", error: null },
    candidate: null,
    submission: null,
    orchestration: { idle_nudges_sent: 0 },
    usage: null,
    artifacts: {
      bundle_dir: bundleDir,
      work_dir: null,
      events: null,
      command_log: null,
      command_logs_dir: null,
      session: null,
      output_step: null,
      preview: null,
      validation: null,
      rejected_submissions: null,
      candidate_skill: null,
    },
  };
  const finishResult = () => {
    trialResult.finished_at = new Date().toISOString();
    trialResult.elapsed_seconds = (Date.parse(trialResult.finished_at) - startedAt.getTime()) / 1000;
  };

  await mkdir(path.dirname(bundleDir), { recursive: true });
  try {
    await mkdir(bundleDir);
  } catch (error) {
    trialResult.harness = {
      status: "failed",
      stage: "bundle_allocation",
      error: {
        name: error.code === "EEXIST" ? "TrialBundleExistsError" : error.name,
        message: error.code === "EEXIST"
          ? `Trial bundle already exists and will not be modified: ${bundleDir}`
          : error.message,
        code: error.code,
      },
      retryable: error.code !== "EEXIST",
    };
    finishResult();
    return trialResult;
  }

  await mkdir(workDir);
  await mkdir(resultDir);
  await mkdir(sessionDir);
  const commandLogger = await createCommandLogger(logsDir);
  trialResult.artifacts.work_dir = workDir;
  trialResult.artifacts.events = eventsPath;
  trialResult.artifacts.command_log = commandLogger.indexPath;
  trialResult.artifacts.command_logs_dir = commandLogger.commandDir;
  trialResult.artifacts.rejected_submissions = path.join(resultDir, "rejected");
  await writeFile(path.join(bundleDir, "trial-spec.json"), `${JSON.stringify(spec, null, 2)}\n`);

  let eventWrites = Promise.resolve();
  const recordEvent = (event) => {
    eventWrites = eventWrites.then(() => appendFile(eventsPath, `${json({
      recorded_at: new Date().toISOString(),
      ...event,
    })}\n`));
    return eventWrites;
  };

  const persistResult = async () => {
    finishResult();
    await writeFile(
      path.join(bundleDir, "trial-result.json"),
      `${JSON.stringify(trialResult, null, 2)}\n`,
    );
  };

  let session;
  try {
    const provider = loadCandidateProvider(spec.candidate);
    const evaluatorBin = spec.evaluation?.validator?.executable
      ?? provider.candidate.executable;
    const commandTimeoutMs = (spec.limits?.command_timeout_seconds ?? 120) * 1000;
    const { copied, images, stepInputs } = await copyFixtureInputs(spec.fixture, workDir);
    const skillResources = await materializeCandidateSkill(spec.candidate, workDir);
    trialResult.artifacts.candidate_skill = skillResources.artifact?.path ?? null;
    await recordEvent({ type: "trial_started", trial_id: spec.trial_id, fixture_id: spec.fixture.id });
    if (skillResources.artifact) {
      await recordEvent({ type: "candidate_skill_loaded", ...skillResources.artifact });
      await recordEvent({
        type: "candidate_skill_selected",
        name: skillResources.artifact.name,
        invocation: `/skill:${skillResources.artifact.name}`,
      });
    }

    const init = await provider.setup({
      workDir,
      commandTimeoutMs,
      recordCommand: commandLogger.record,
    });
    await recordEvent({
      type: "candidate_preflight",
      command: provider.preflightCommand,
      ...init,
    });
    if (init.exitCode !== 0 || init.timedOut) {
      trialResult.harness = { status: "ok", stage: null, error: null };
      trialResult.candidate = {
        status: "failed",
        reason: provider.preflightFailureReason,
        detail: init.stderr || init.stdout,
      };
      return trialResult;
    }

    trialResult.harness.stage = "model_setup";
    const modelRuntime = await ModelRuntime.create({ modelsPath: null });
    const apiKeyName = API_KEY_ENV[spec.model.provider];
    const apiKey = apiKeyName ? process.env[apiKeyName] : undefined;
    if (!apiKey) throw new Error(`Missing ${apiKeyName ?? "API key"} for ${spec.model.provider}`);
    await modelRuntime.setRuntimeApiKey(spec.model.provider, apiKey);
    const model = modelRuntime.getModel(spec.model.provider, spec.model.id);
    if (!model) throw new Error(`Pi does not know model ${spec.model.provider}/${spec.model.id}`);

    const { tools: candidateTools, state } = provider.createTools({
      workDir,
      resultDir,
      evaluatorBin,
      recordEvent,
      recordCommand: commandLogger.record,
      commandTimeoutMs,
    });
    const settingsManager = SettingsManager.inMemory({
      compaction: { enabled: false },
      retry: { enabled: true, maxRetries: 2 },
      images: { autoResize: true },
    });
    const loader = isolatedResourceLoader(provider.systemPrompt, skillResources);
    const sessionManager = SessionManager.create(workDir, sessionDir);
    ({ session } = await createAgentSession({
      cwd: workDir,
      agentDir: path.join(bundleDir, "pi-agent"),
      model,
      thinkingLevel: spec.model.thinking_level ?? "low",
      modelRuntime,
      tools: provider.toolNames,
      customTools: candidateTools,
      resourceLoader: loader,
      sessionManager,
      settingsManager,
    }));
    trialResult.artifacts.session = session.sessionFile;
    session.subscribe((event) => {
      const compactEvent = compactPiEvent(event);
      if (compactEvent) void recordEvent(compactEvent);
    });
    await recordEvent({
      type: "pi_session_ready",
      session_id: session.sessionId,
      model: `${model.provider}/${model.id}`,
      tools: session.getActiveToolNames(),
      skills: skillResources.skills.map((skill) => ({
        name: skill.name,
        description: skill.description,
        file_path: skill.filePath,
      })),
    });

    trialResult.harness.stage = "candidate_execution";
    let timedOut = false;
    const timeoutMs = (spec.limits?.trial_timeout_seconds ?? 900) * 1000;
    const timer = setTimeout(() => {
      timedOut = true;
      void recordEvent({ type: "trial_timeout", timeout_ms: timeoutMs });
      void session.abort();
    }, timeoutMs);
    try {
      await session.prompt(candidatePrompt(spec.fixture, copied, skillResources.skills), { images });
      const maxIdleNudges = spec.limits?.max_idle_nudges ?? 2;
      while (!state.submitted && !state.harnessFailure && !timedOut && trialResult.orchestration.idle_nudges_sent < maxIdleNudges) {
        trialResult.orchestration.idle_nudges_sent += 1;
        const priorRejection = state.invalidSubmissions.length > 0
          ? "Your previous STEP submission was rejected because it did not contain valid solid geometry. "
          : "";
        const nudge = `${priorRejection}No valid result has been submitted. Continue the autonomous trial now: use the provided tools, do not only describe a plan, and call submit_result with your best valid STEP file before stopping.`;
        await recordEvent({
          type: "harness_instruction",
          reason: "candidate_stopped_without_submission",
          sequence: trialResult.orchestration.idle_nudges_sent,
          message: nudge,
        });
        await session.prompt(nudge);
      }
    } finally {
      clearTimeout(timer);
    }

    trialResult.usage = session.getSessionStats();
    if (state.harnessFailure) {
      trialResult.harness = {
        status: "failed",
        stage: state.harnessFailure.stage,
        error: state.harnessFailure.error,
        retryable: true,
      };
      trialResult.candidate = null;
    } else if (state.submitted) {
      trialResult.harness = { status: "ok", stage: null, error: null };
      trialResult.candidate = { status: "completed", reason: "submitted" };
      trialResult.artifacts.output_step = path.join(resultDir, "output.step");
      trialResult.artifacts.preview = state.previewPath;
      trialResult.artifacts.validation = state.validationPath;
      trialResult.submission = await classifyValidOutput(
        trialResult.artifacts.output_step,
        stepInputs,
      );
    } else if (timedOut) {
      trialResult.harness = { status: "ok", stage: null, error: null };
      trialResult.candidate = { status: "failed", reason: "trial_timeout" };
    } else if (state.invalidSubmissions.length > 0) {
      trialResult.harness = { status: "ok", stage: null, error: null };
      trialResult.candidate = {
        status: "failed",
        reason: "invalid_submission",
        invalid_submission_count: state.invalidSubmissions.length,
      };
    } else {
      trialResult.harness = { status: "ok", stage: null, error: null };
      trialResult.candidate = { status: "failed", reason: "no_submission" };
    }
  } catch (error) {
    trialResult.harness = {
      status: "failed",
      stage: trialResult.harness.stage,
      error: { name: error.name, message: error.message, stack: error.stack },
      retryable: true,
    };
    trialResult.candidate = null;
    await recordEvent({ type: "harness_failure", error });
  } finally {
    if (session) session.dispose();
    await eventWrites;
    await commandLogger.flush();
    await persistResult();
  }
  return trialResult;
}


async function main() {
  const specPath = process.argv[2];
  if (!specPath) throw new Error("Usage: node src/trial-worker.js <trial-spec.json>");
  const spec = JSON.parse(await readFile(path.resolve(specPath), "utf8"));
  const result = await runTrial(spec);
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  process.exitCode = result.harness.status === "ok" ? 0 : 2;
}


if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 2;
  });
}
