import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import process from "node:process";

const root = resolve(import.meta.dirname, "..");
const candidates = process.env.TC_PYTHON
  ? [process.env.TC_PYTHON]
  : [
      resolve(root, ".venv/bin/python"),
      resolve(root, ".venv/Scripts/python.exe"),
      "python3",
    ];
const python = candidates.find((candidate) =>
  candidate === "python3" || existsSync(candidate),
);

if (!python) {
  console.error("Python 3.12+ was not found. Create .venv and run pnpm setup.");
  process.exit(1);
}

const result = spawnSync(python, process.argv.slice(2), {
  cwd: root,
  env: process.env,
  stdio: "inherit",
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);

