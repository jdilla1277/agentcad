#!/usr/bin/env node

import { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { realpathSync } from "node:fs";
import { access, appendFile, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { parse as parseYaml } from "yaml";

import { runTrial } from "./trial-worker.js";


const CONFIG_NAME = "cadbench.json";
const KEEP_AWAKE_ENV = "CADBENCH_MACOS_CAFFEINATE";


function usage() {
  return `Usage:
  cadbench init --model PROVIDER/MODEL --data-dir PATH --evaluator-bin PATH
  cadbench candidate add NAME --provider agentcad-cli --agentcad-bin PATH [--source-type TYPE] [--source VALUE]
  cadbench candidate add NAME --provider build123d-python --python-bin PATH [--source-type TYPE] [--source VALUE]
  cadbench candidate list
  cadbench run FIXTURE --candidate NAME [--model PROVIDER/MODEL]
  cadbench run-all --candidate NAME [--model PROVIDER/MODEL] [--fixtures 101,102] [--concurrency 2]
`;
}


function parseArgs(args) {
  const positional = [];
  const options = {};
  for (let index = 0; index < args.length; index += 1) {
    const value = args[index];
    if (!value.startsWith("--")) {
      positional.push(value);
      continue;
    }
    const key = value.slice(2).replaceAll("-", "_");
    const next = args[index + 1];
    if (next == null || next.startsWith("--")) options[key] = true;
    else {
      options[key] = next;
      index += 1;
    }
  }
  return { positional, options };
}


function requireOption(options, name) {
  const value = options[name];
  if (typeof value !== "string" || !value) {
    throw new Error(`Missing required option --${name.replaceAll("_", "-")}`);
  }
  return value;
}


function modelConfig(value) {
  const separator = value.indexOf("/");
  if (separator < 1 || separator === value.length - 1) {
    throw new Error(`Model must be PROVIDER/MODEL, got '${value}'`);
  }
  return {
    provider: value.slice(0, separator),
    id: value.slice(separator + 1),
    thinking_level: "low",
  };
}


function safeName(value, label) {
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(value)) {
    throw new Error(`${label} may only contain letters, numbers, '.', '_' and '-'`);
  }
  return value;
}


function executablePath(value, cwd) {
  return value.includes(path.sep) ? path.resolve(cwd, value) : value;
}


export function keepAwakeEvidence({
  platform = process.platform,
  env = process.env,
} = {}) {
  if (platform !== "darwin") {
    return { status: "not_applicable", provider: null };
  }
  if (env[KEEP_AWAKE_ENV] === "1") {
    return {
      status: "active",
      provider: "macos-caffeinate",
      executable: "/usr/bin/caffeinate",
      assertions: ["idle_system_sleep", "system_sleep_on_ac", "disk_idle"],
    };
  }
  return { status: "inactive", provider: "macos-caffeinate" };
}


export function shouldUseKeepAwake(
  argv,
  { platform = process.platform, env = process.env } = {},
) {
  return platform === "darwin"
    && (argv[0] === "run" || argv[0] === "run-all")
    && env[KEEP_AWAKE_ENV] !== "1";
}


export function runWithKeepAwake(argv, {
  spawnCommand = spawn,
  nodeExecutable = process.execPath,
  cliPath = process.argv[1],
  env = process.env,
} = {}) {
  return new Promise((resolve, reject) => {
    const args = ["-ims", nodeExecutable, cliPath, ...argv];
    const child = spawnCommand("/usr/bin/caffeinate", args, {
      stdio: "inherit",
      env: { ...env, [KEEP_AWAKE_ENV]: "1" },
    });
    child.once("error", (error) => {
      reject(new Error(`Unable to establish the macOS keep-awake guard: ${error.message}`));
    });
    child.once("close", (code, signal) => {
      resolve(code ?? (signal ? 2 : 0));
    });
  });
}


function mimeType(fileName) {
  const extension = path.extname(fileName).toLowerCase();
  if (extension === ".png") return "image/png";
  if (extension === ".jpg" || extension === ".jpeg") return "image/jpeg";
  if (extension === ".webp") return "image/webp";
  if (extension === ".step" || extension === ".stp") return "model/step";
  return "application/octet-stream";
}


async function readJson(filePath) {
  return JSON.parse(await readFile(filePath, "utf8"));
}


async function requireWorkspace(cwd) {
  const configPath = path.join(cwd, CONFIG_NAME);
  try {
    return { config: await readJson(configPath), configPath };
  } catch (error) {
    if (error.code === "ENOENT") {
      throw new Error(`No ${CONFIG_NAME} found in ${cwd}; run 'cadbench init' first`);
    }
    throw error;
  }
}


async function initWorkspace(cwd, options) {
  const configPath = path.join(cwd, CONFIG_NAME);
  try {
    await access(configPath);
    throw new Error(`${CONFIG_NAME} already exists in ${cwd}`);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }

  const model = modelConfig(options.model ?? "openai/gpt-5-nano");
  const dataDir = path.resolve(cwd, requireOption(options, "data_dir"));
  const evaluator = executablePath(requireOption(options, "evaluator_bin"), cwd);
  await access(dataDir);
  const config = {
    schema_version: 1,
    model,
    data_dir: dataDir,
    evaluation: {
      validator: {
        provider: "agentcad-cli",
        executable: evaluator,
        provenance: { role: "fixed-neutral-evaluator" },
      },
    },
    limits: {
      trial_timeout_seconds: 900,
      command_timeout_seconds: 120,
      max_idle_nudges: 2,
    },
  };
  await mkdir(path.join(cwd, "candidates"), { recursive: true });
  await mkdir(path.join(cwd, "runs"), { recursive: true });
  await mkdir(path.join(cwd, "experiments"), { recursive: true });
  const ignorePath = path.join(cwd, ".gitignore");
  let ignore = "";
  try {
    ignore = await readFile(ignorePath, "utf8");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  const ignored = new Set(ignore.split(/\r?\n/));
  const additions = ["runs/", "experiments/"].filter((entry) => !ignored.has(entry));
  if (additions.length) {
    const separator = ignore && !ignore.endsWith("\n") ? "\n" : "";
    await writeFile(ignorePath, `${ignore}${separator}${additions.join("\n")}\n`);
  }
  await writeFile(configPath, `${JSON.stringify(config, null, 2)}\n`, { flag: "wx" });
  return config;
}


async function addCandidate(cwd, name, options) {
  await requireWorkspace(cwd);
  safeName(name, "Candidate name");
  const candidatePath = path.join(cwd, "candidates", `${name}.json`);
  try {
    await access(candidatePath);
    throw new Error(`Candidate '${name}' already exists`);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  const source = options.source;
  const provider = options.provider ?? "agentcad-cli";
  let executable;
  if (provider === "agentcad-cli" || provider === "agentcad") {
    executable = executablePath(
      options.agentcad_bin ?? requireOption(options, "executable"),
      cwd,
    );
  } else if (provider === "build123d-python" || provider === "python-build123d") {
    executable = executablePath(
      options.python_bin ?? requireOption(options, "executable"),
      cwd,
    );
  } else {
    throw new Error(`Unknown candidate provider '${provider}'`);
  }
  const candidate = {
    schema_version: 1,
    id: name,
    name,
    provider: provider === "agentcad" ? "agentcad-cli"
      : provider === "python-build123d" ? "build123d-python"
        : provider,
    executable,
    ...((provider === "agentcad-cli" || provider === "agentcad")
      ? { runtime: options.runtime ?? "build123d" }
      : {}),
    provenance: {
      type: options.source_type ?? "local-executable",
      ...(source ? { source } : {}),
    },
  };
  await writeFile(candidatePath, `${JSON.stringify(candidate, null, 2)}\n`, { flag: "wx" });
  return { candidate, candidatePath };
}


async function listCandidates(cwd) {
  await requireWorkspace(cwd);
  const entries = await readdir(path.join(cwd, "candidates"));
  return entries.filter((name) => name.endsWith(".json")).sort();
}


function trialId(fixtureId, candidateId) {
  const timestamp = new Date().toISOString().replaceAll(/[-:.]/g, "");
  return `${fixtureId}-${candidateId}-${timestamp}-${randomUUID().slice(0, 8)}`;
}


async function loadFixture(dataDir, fixtureId) {
  safeName(fixtureId, "Fixture ID");
  const fixtureDir = path.join(dataDir, fixtureId);
  let description;
  try {
    description = parseYaml(await readFile(path.join(fixtureDir, "description.yaml"), "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") throw new Error(`Fixture '${fixtureId}' was not found in ${dataDir}`);
    throw error;
  }
  if (typeof description?.description !== "string" || !description.description.trim()) {
    throw new Error(`Fixture '${fixtureId}' has no description`);
  }
  const inputFiles = (description.input_files ?? []).map((fileName) => ({
    path: path.resolve(fixtureDir, fileName),
    name: path.basename(fileName),
    mime_type: mimeType(fileName),
  }));
  for (const input of inputFiles) await access(input.path);
  return { id: fixtureId, prompt: description.description, input_files: inputFiles };
}


async function runFixture(cwd, fixtureId, options, trialRunner, environment) {
  const { config } = await requireWorkspace(cwd);
  const candidateName = safeName(requireOption(options, "candidate"), "Candidate name");
  let candidate;
  try {
    candidate = await readJson(path.join(cwd, "candidates", `${candidateName}.json`));
  } catch (error) {
    if (error.code === "ENOENT") throw new Error(`Unknown candidate '${candidateName}'`);
    throw error;
  }
  const fixture = await loadFixture(config.data_dir, fixtureId);
  const id = trialId(fixture.id, candidate.id);
  const spec = {
    trial_id: id,
    bundle_dir: path.join(cwd, "runs", id),
    fixture,
    model: options.model ? modelConfig(options.model) : config.model,
    candidate,
    evaluation: config.evaluation,
    limits: config.limits,
    environment,
  };
  return { spec, result: await trialRunner(spec) };
}


function positiveInteger(value, label, fallback) {
  if (value == null) return fallback;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 1) throw new Error(`${label} must be a positive integer`);
  return parsed;
}


async function fixtureIds(dataDir, requested) {
  if (requested) {
    const ids = requested.split(",").map((value) => value.trim()).filter(Boolean);
    if (!ids.length) throw new Error("--fixtures must contain at least one fixture ID");
    return ids.map((id) => safeName(id, "Fixture ID"));
  }
  const entries = await readdir(dataDir, { withFileTypes: true });
  const ids = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    try {
      await access(path.join(dataDir, entry.name, "description.yaml"));
      ids.push(entry.name);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
  return ids.sort((left, right) => left.localeCompare(right, undefined, { numeric: true }));
}


async function runPool(items, concurrency, worker) {
  const results = new Array(items.length);
  let next = 0;
  async function consume() {
    while (next < items.length) {
      const index = next;
      next += 1;
      results[index] = await worker(items[index], index);
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, () => consume()));
  return results;
}


async function runAll(cwd, options, trialRunner, progress, environment) {
  const { config } = await requireWorkspace(cwd);
  const candidateName = safeName(requireOption(options, "candidate"), "Candidate name");
  try {
    await access(path.join(cwd, "candidates", `${candidateName}.json`));
  } catch (error) {
    if (error.code === "ENOENT") throw new Error(`Unknown candidate '${candidateName}'`);
    throw error;
  }
  const ids = await fixtureIds(config.data_dir, options.fixtures);
  if (!ids.length) throw new Error(`No fixtures found in ${config.data_dir}`);
  const concurrency = positiveInteger(options.concurrency, "--concurrency", 2);
  const experimentId = `${candidateName}-${new Date().toISOString().replaceAll(/[-:.]/g, "")}-${randomUUID().slice(0, 8)}`;
  const experimentDir = path.join(cwd, "experiments", experimentId);
  await mkdir(experimentDir, { recursive: true });
  const startedAt = new Date().toISOString();
  const experimentSpec = {
    schema_version: 1,
    experiment_id: experimentId,
    candidate: candidateName,
    model: options.model ? modelConfig(options.model) : config.model,
    fixtures: ids,
    concurrency,
    started_at: startedAt,
    environment,
  };
  await writeFile(path.join(experimentDir, "run-spec.json"), `${JSON.stringify(experimentSpec, null, 2)}\n`, { flag: "wx" });
  const trialLedgerPath = path.join(experimentDir, "trials.jsonl");
  await writeFile(trialLedgerPath, "", { flag: "wx" });

  let finished = 0;
  let ledgerWrites = Promise.resolve();
  const trials = await runPool(ids, concurrency, async (fixtureId) => {
    let outcome;
    try {
      const { spec, result } = await runFixture(cwd, fixtureId, options, trialRunner, environment);
      outcome = {
        fixture_id: fixtureId,
        trial_id: result.trial_id,
        harness: result.harness,
        candidate: result.candidate,
        submission: result.submission ?? null,
        bundle_dir: spec.bundle_dir,
        usage: result.usage ?? null,
        elapsed_seconds: result.elapsed_seconds ?? null,
      };
    } catch (error) {
      outcome = {
        fixture_id: fixtureId,
        trial_id: null,
        harness: {
          status: "failed",
          stage: "trial_materialization",
          error: { name: error.name, message: error.message, stack: error.stack },
          retryable: true,
        },
        candidate: null,
        submission: null,
        bundle_dir: null,
      };
    }
    ledgerWrites = ledgerWrites.then(() => appendFile(trialLedgerPath, `${JSON.stringify(outcome)}\n`));
    await ledgerWrites;
    finished += 1;
    const classification = outcome.submission?.classification
      ? ` output=${outcome.submission.classification}`
      : "";
    progress.write(`[${finished}/${ids.length}] ${fixtureId}: harness=${outcome.harness.status} candidate=${outcome.candidate?.status ?? "not-run"}${classification}\n`);
    return outcome;
  });
  const harnessFailures = trials.filter((trial) => trial.harness.status !== "ok").length;
  const completed = trials.filter((trial) => (
    trial.harness.status === "ok" && trial.candidate?.status === "completed"
  )).length;
  const eligibleTrials = trials.length - harnessFailures;
  const validOutputsByClassification = {
    new_valid_output: 0,
    changed_valid_output: 0,
    unchanged_input: 0,
    unclassified_valid_output: 0,
  };
  for (const trial of trials) {
    if (trial.harness.status !== "ok" || trial.candidate?.status !== "completed") continue;
    const classification = trial.submission?.classification;
    if (Object.hasOwn(validOutputsByClassification, classification)) {
      validOutputsByClassification[classification] += 1;
    } else {
      validOutputsByClassification.unclassified_valid_output += 1;
    }
  }
  const meaningfulCompleted = validOutputsByClassification.new_valid_output
    + validOutputsByClassification.changed_valid_output;
  const candidateFailuresByReason = {};
  const harnessFailuresByStage = {};
  for (const trial of trials) {
    if (trial.harness.status !== "ok") {
      const stage = trial.harness.stage ?? "unknown";
      harnessFailuresByStage[stage] = (harnessFailuresByStage[stage] ?? 0) + 1;
    } else if (trial.candidate?.status !== "completed") {
      const reason = trial.candidate?.reason ?? "unknown";
      candidateFailuresByReason[reason] = (candidateFailuresByReason[reason] ?? 0) + 1;
    }
  }
  const totalCost = trials.reduce((sum, trial) => sum + (trial.usage?.cost ?? 0), 0);
  const summary = {
    schema_version: 1,
    experiment_id: experimentId,
    candidate: candidateName,
    model: experimentSpec.model,
    started_at: startedAt,
    finished_at: new Date().toISOString(),
    concurrency,
    counts: {
      attempted: trials.length,
      eligible_trials: eligibleTrials,
      completed,
      meaningful_completed: meaningfulCompleted,
      candidate_failures: eligibleTrials - completed,
      harness_failures: harnessFailures,
    },
    completion_rate: eligibleTrials ? completed / eligibleTrials : null,
    meaningful_completion_rate: eligibleTrials ? meaningfulCompleted / eligibleTrials : null,
    valid_outputs_by_classification: validOutputsByClassification,
    candidate_failures_by_reason: candidateFailuresByReason,
    harness_failures_by_stage: harnessFailuresByStage,
    usage: { total_cost: totalCost },
    trials,
    artifacts: { experiment_dir: experimentDir, trial_ledger: trialLedgerPath },
  };
  const resultPath = path.join(experimentDir, "run-result.json");
  await writeFile(resultPath, `${JSON.stringify(summary, null, 2)}\n`, { flag: "wx" });
  return { summary, resultPath };
}


export async function main(argv = process.argv.slice(2), dependencies = {}) {
  const cwd = path.resolve(dependencies.cwd ?? process.cwd());
  const stdout = dependencies.stdout ?? process.stdout;
  const stderr = dependencies.stderr ?? process.stderr;
  const trialRunner = dependencies.runTrial ?? runTrial;
  const environment = dependencies.environment ?? {
    keep_awake: keepAwakeEvidence(),
  };
  const [command, ...rest] = argv;
  if (
    (command === "run" || command === "run-all")
    && environment.keep_awake?.status === "active"
  ) {
    stderr.write("cadbench: macOS keep-awake guard is active for this run\n");
  }
  if (!command || command === "help" || command === "--help") {
    stdout.write(usage());
    return 0;
  }

  if (command === "init") {
    const { options } = parseArgs(rest);
    const config = await initWorkspace(cwd, options);
    stdout.write(`Initialized CADBench workspace: ${cwd}\n`);
    stdout.write(`Model: ${config.model.provider}/${config.model.id}\n`);
    stdout.write(`Fixed evaluator: ${config.evaluation.validator.executable}\n`);
    return 0;
  }

  if (command === "candidate") {
    const [subcommand, ...candidateArgs] = rest;
    if (subcommand === "add") {
      const { positional, options } = parseArgs(candidateArgs);
      const name = positional[0];
      if (!name) throw new Error("Usage: cadbench candidate add NAME --provider PROVIDER --executable PATH");
      const { candidate, candidatePath } = await addCandidate(cwd, name, options);
      stdout.write(`Added candidate '${candidate.id}': ${candidatePath}\n`);
      return 0;
    }
    if (subcommand === "list") {
      const candidates = await listCandidates(cwd);
      stdout.write(candidates.length ? `${candidates.map((name) => name.slice(0, -5)).join("\n")}\n` : "No candidates.\n");
      return 0;
    }
    throw new Error("Usage: cadbench candidate add|list");
  }

  if (command === "run") {
    const { positional, options } = parseArgs(rest);
    const fixtureId = positional[0];
    if (!fixtureId) throw new Error("Usage: cadbench run FIXTURE --candidate NAME");
    const { spec, result } = await runFixture(cwd, fixtureId, options, trialRunner, environment);
    stdout.write(`${JSON.stringify({
      trial_id: result.trial_id,
      harness: result.harness,
      candidate: result.candidate,
      submission: result.submission ?? null,
      bundle_dir: spec.bundle_dir,
      command_log: result.artifacts?.command_log ?? path.join(spec.bundle_dir, "logs", "commands.jsonl"),
    }, null, 2)}\n`);
    return result.harness.status === "ok" ? 0 : 2;
  }

  if (command === "run-all") {
    const { options } = parseArgs(rest);
    const { summary, resultPath } = await runAll(cwd, options, trialRunner, stderr, environment);
    stdout.write(`${JSON.stringify({ ...summary, result_path: resultPath }, null, 2)}\n`);
    return summary.counts.harness_failures === 0 ? 0 : 2;
  }

  throw new Error(`Unknown command '${command}'\n${usage()}`);
}


function isMainModule() {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(fileURLToPath(import.meta.url)) === realpathSync(process.argv[1]);
  } catch {
    return false;
  }
}


if (isMainModule()) {
  const argv = process.argv.slice(2);
  const execution = shouldUseKeepAwake(argv) ? runWithKeepAwake(argv) : main(argv);
  execution.then((exitCode) => {
    process.exitCode = exitCode;
  }).catch((error) => {
    console.error(`cadbench: ${error.message}`);
    process.exitCode = 2;
  });
}
