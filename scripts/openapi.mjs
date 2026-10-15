import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import process from "node:process";

const mode = process.argv[2];
if (mode !== "generate" && mode !== "check") {
  console.error("Usage: node scripts/openapi.mjs <generate|check>");
  process.exit(2);
}

const root = resolve(import.meta.dirname, "..");
const pythonCandidates = process.env.TC_PYTHON
  ? [process.env.TC_PYTHON]
  : [
      resolve(root, ".venv/bin/python"),
      resolve(root, ".venv/Scripts/python.exe"),
      "python3",
    ];
const python = pythonCandidates.find((candidate) =>
  candidate === "python3" || existsSync(candidate),
);
const committedSchema = resolve(root, "packages/api-client/openapi.json");
const committedClient = resolve(root, "packages/api-client/src/schema.gen.ts");
const tempRoot = mode === "check" ? mkdtempSync(join(tmpdir(), "tc-openapi-")) : null;
const schemaOutput = tempRoot ? join(tempRoot, "openapi.json") : committedSchema;
const clientOutput = tempRoot ? join(tempRoot, "schema.gen.ts") : committedClient;

function run(command, args) {
  const result = spawnSync(command, args, { cwd: root, stdio: "inherit" });
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

try {
  run(python, ["scripts/export_openapi.py", "--output", schemaOutput]);
  run("pnpm", [
    "--filter",
    "@tc/api-client",
    "exec",
    "openapi-typescript",
    schemaOutput,
    "--output",
    clientOutput,
  ]);

  if (mode === "check") {
    const drifted = [
      [schemaOutput, committedSchema],
      [clientOutput, committedClient],
    ].filter(
      ([generated, committed]) =>
        !existsSync(committed) ||
        !readFileSync(generated).equals(readFileSync(committed)),
    );
    if (drifted.length > 0) {
      console.error(
        `OpenAPI artifacts are stale: ${drifted
          .map(([, committed]) => committed.replace(`${root}/`, ""))
          .join(", ")}`,
      );
      console.error("Run `pnpm openapi:generate` and include both generated files.");
      process.exit(1);
    }
    console.log("OpenAPI schema and TypeScript client are up to date.");
  }
} finally {
  if (tempRoot) {
    rmSync(tempRoot, { recursive: true, force: true });
  }
}

