import { readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";


export const PROVIDER_KEYS = {
  openai: "OPENAI_API_KEY",
  anthropic: "ANTHROPIC_API_KEY",
  google: "GEMINI_API_KEY",
};


function parseValue(rawValue, source, lineNumber) {
  const value = rawValue.trim();
  if (!value) return "";
  if (value.startsWith("'")) {
    if (!value.endsWith("'") || value.length < 2) {
      throw new Error(`Malformed single-quoted value in ${source}:${lineNumber}`);
    }
    return value.slice(1, -1);
  }
  if (value.startsWith('"')) {
    if (!value.endsWith('"') || value.length < 2) {
      throw new Error(`Malformed double-quoted value in ${source}:${lineNumber}`);
    }
    try {
      return JSON.parse(value);
    } catch {
      throw new Error(`Malformed double-quoted value in ${source}:${lineNumber}`);
    }
  }
  return value;
}


export function parseEnvFile(contents, source = "<env>") {
  const values = {};
  const lines = contents.split(/\r?\n/);
  for (let index = 0; index < lines.length; index += 1) {
    const original = lines[index];
    const line = original.trim();
    if (!line || line.startsWith("#")) continue;
    const assignment = line.startsWith("export ") ? line.slice(7).trimStart() : line;
    const separator = assignment.indexOf("=");
    const key = separator === -1 ? "" : assignment.slice(0, separator).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
      throw new Error(`Malformed environment assignment in ${source}:${index + 1}`);
    }
    values[key] = parseValue(assignment.slice(separator + 1), source, index + 1);
  }
  return values;
}


export function cadbenchEnvPaths({
  cwd = process.cwd(),
  env = process.env,
  homeDir = os.homedir(),
} = {}) {
  const explicit = env.CADBENCH_ENV_FILE;
  const userPath = explicit
    ? path.resolve(cwd, explicit)
    : path.join(homeDir, ".config", "cadbench", "env");
  const workspacePath = path.join(path.resolve(cwd), ".env");
  return [
    { kind: explicit ? "explicit" : "user", path: userPath, required: Boolean(explicit) },
    { kind: "workspace", path: workspacePath, required: false },
  ].filter((item, index, items) => (
    items.findIndex((candidate) => candidate.path === item.path) === index
  ));
}


export async function loadCadbenchEnvironment({
  cwd = process.cwd(),
  env = process.env,
  homeDir = os.homedir(),
} = {}) {
  const ambientKeys = new Set(Object.keys(env));
  const files = [];
  for (const candidate of cadbenchEnvPaths({ cwd, env, homeDir })) {
    let contents;
    try {
      contents = await readFile(candidate.path, "utf8");
    } catch (error) {
      if (error.code === "ENOENT" && !candidate.required) {
        files.push({ kind: candidate.kind, path: candidate.path, status: "missing" });
        continue;
      }
      throw new Error(`Unable to read CADBench environment file ${candidate.path}: ${error.message}`);
    }
    const values = parseEnvFile(contents, candidate.path);
    for (const [key, value] of Object.entries(values)) {
      if (!ambientKeys.has(key)) env[key] = value;
    }
    files.push({
      kind: candidate.kind,
      path: candidate.path,
      status: "loaded",
      keys: Object.keys(values),
    });
  }
  return {
    files,
    providers: Object.fromEntries(
      Object.entries(PROVIDER_KEYS).map(([provider, key]) => [
        provider,
        { key, status: env[key] ? "set" : "missing" },
      ]),
    ),
  };
}
