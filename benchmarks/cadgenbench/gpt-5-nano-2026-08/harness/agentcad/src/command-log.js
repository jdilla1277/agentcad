import { appendFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";


function json(value) {
  return JSON.stringify(value, (_key, item) => {
    if (typeof item === "bigint") return item.toString();
    if (item instanceof Error) return { name: item.name, message: item.message, stack: item.stack };
    return item;
  });
}


export async function createCommandLogger(logsDir) {
  const resolvedLogsDir = path.resolve(logsDir);
  const commandDir = path.join(resolvedLogsDir, "commands");
  const indexPath = path.join(resolvedLogsDir, "commands.jsonl");
  await mkdir(commandDir, { recursive: true });
  await writeFile(indexPath, "", { flag: "wx" });
  let sequence = 0;
  let writes = Promise.resolve();

  const record = (command, execution) => {
    sequence += 1;
    const current = sequence;
    const stem = String(current).padStart(4, "0");
    const stdoutPath = path.join(commandDir, `${stem}.stdout.log`);
    const stderrPath = path.join(commandDir, `${stem}.stderr.log`);
    const finishedAt = new Date();
    const startedAt = new Date(finishedAt.getTime() - (execution.durationMs ?? 0));
    const entry = {
      sequence: current,
      actor: command.actor,
      phase: command.phase,
      tool_call_id: command.toolCallId ?? null,
      started_at: startedAt.toISOString(),
      finished_at: finishedAt.toISOString(),
      executable: command.executable,
      args: [...command.args],
      cwd: path.resolve(command.cwd),
      timeout_ms: command.timeoutMs,
      env_overrides: { ...(command.envOverrides ?? {}) },
      exit_code: execution.exitCode,
      signal: execution.signal ?? null,
      timed_out: Boolean(execution.timedOut),
      duration_ms: execution.durationMs,
      error: execution.error ?? null,
      stdout: path.relative(resolvedLogsDir, stdoutPath),
      stderr: path.relative(resolvedLogsDir, stderrPath),
    };
    writes = writes.then(async () => {
      await writeFile(stdoutPath, execution.stdout ?? "");
      await writeFile(stderrPath, execution.stderr ?? "");
      await appendFile(indexPath, `${json(entry)}\n`);
    });
    return writes;
  };

  return {
    commandDir,
    indexPath,
    record,
    flush: () => writes,
  };
}


export async function runLoggedCommand({
  runCommand,
  executable,
  args,
  options,
  command,
  recordCommand,
}) {
  let execution;
  const started = performance.now();
  try {
    execution = await runCommand(executable, args, options);
  } catch (error) {
    await recordCommand(command, {
      exitCode: -1,
      signal: null,
      timedOut: false,
      durationMs: Math.round(performance.now() - started),
      stdout: "",
      stderr: "",
      error: { name: error.name, message: error.message, stack: error.stack },
    });
    throw error;
  }
  await recordCommand(command, execution);
  return execution;
}
