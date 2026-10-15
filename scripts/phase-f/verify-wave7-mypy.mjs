import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import process from "node:process";

const root = resolve(import.meta.dirname, "../..");
const environment = { ...process.env };
delete environment.MYPYPATH;

const result = spawnSync(
  process.execPath,
  [
    resolve(root, "scripts/python.mjs"),
    "-m",
    "mypy",
    "--no-incremental",
    "scripts/phase-f/object_storage_recovery.py",
    "scripts/phase-f/rehearse.py",
  ],
  {
    cwd: root,
    env: environment,
    stdio: "inherit",
  },
);

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);
