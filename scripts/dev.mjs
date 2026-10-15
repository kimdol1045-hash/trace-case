import { existsSync } from "node:fs";
import { spawn, spawnSync } from "node:child_process";
import { resolve } from "node:path";
import process from "node:process";

const root = resolve(import.meta.dirname, "..");
const pythonCandidates = process.env.TC_PYTHON
  ? [process.env.TC_PYTHON]
  : [
      resolve(root, ".venv/bin/python"),
      resolve(root, ".venv/Scripts/python.exe"),
    ];
const python = pythonCandidates.find((candidate) => existsSync(candidate));

if (!python || !existsSync(resolve(root, "node_modules"))) {
  console.error(
    "Development dependencies are missing. Run `python3 -m venv .venv && pnpm run setup` first.",
  );
  process.exit(1);
}

const env = {
  ...process.env,
  TC_DATABASE_URL:
    process.env.TC_DATABASE_URL ??
    "postgresql+psycopg://tc:tc_dev_only_change_me@127.0.0.1:55432/tc",
  TC_ENCRYPTION_MASTER_KEY:
    process.env.TC_ENCRYPTION_MASTER_KEY ??
    "local-development-key-not-for-production",
  VITE_API_BASE_URL: process.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000",
  TC_OBJECT_STORAGE_ENABLED: process.env.TC_OBJECT_STORAGE_ENABLED ?? "true",
  TC_OBJECT_STORAGE_PROVIDER: process.env.TC_OBJECT_STORAGE_PROVIDER ?? "MINIO",
  TC_OBJECT_STORAGE_ENDPOINT_URL:
    process.env.TC_OBJECT_STORAGE_ENDPOINT_URL ?? "http://127.0.0.1:59000",
  TC_OBJECT_STORAGE_BROWSER_ENDPOINT_URL:
    process.env.TC_OBJECT_STORAGE_BROWSER_ENDPOINT_URL ?? "http://127.0.0.1:59000",
  TC_OBJECT_STORAGE_BUCKET: process.env.TC_OBJECT_STORAGE_BUCKET ?? "tc-attachments",
  TC_OBJECT_STORAGE_REGION: process.env.TC_OBJECT_STORAGE_REGION ?? "us-east-1",
  TC_OBJECT_STORAGE_ACCESS_KEY_ID:
    process.env.TC_OBJECT_STORAGE_ACCESS_KEY_ID ?? "tc_local_access",
  TC_OBJECT_STORAGE_SECRET_ACCESS_KEY:
    process.env.TC_OBJECT_STORAGE_SECRET_ACCESS_KEY ?? "tc_local_secret_change_me",
  TC_OBJECT_STORAGE_CLAMAV_HOST:
    process.env.TC_OBJECT_STORAGE_CLAMAV_HOST ?? "127.0.0.1",
  TC_OBJECT_STORAGE_CLAMAV_PORT:
    process.env.TC_OBJECT_STORAGE_CLAMAV_PORT ?? "53310",
  TC_OBJECT_STORAGE_CLAMAV_TIMEOUT_SECONDS:
    process.env.TC_OBJECT_STORAGE_CLAMAV_TIMEOUT_SECONDS ?? "30",
};

function run(command, args, label) {
  console.log(`\n[dev] ${label}`);
  const result = spawnSync(command, args, { cwd: root, env, stdio: "inherit" });
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}

if (process.env.TC_SKIP_INFRA !== "1") {
  run(
    "docker",
    ["compose", "-f", "deploy/compose.dev.yml", "up", "-d", "--wait", "postgres", "minio", "clamav"],
    "starting PostgreSQL 16 + pgvector, private MinIO, and ClamAV",
  );
  run(
    "docker",
    ["compose", "-f", "deploy/compose.dev.yml", "run", "--rm", "minio-init"],
    "ensuring the private local attachment bucket exists",
  );
}
run(
  python,
  ["-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
  "applying Alembic migrations",
);

console.log("\n[dev] starting Web, API, and Worker (Ctrl-C stops all three)");
const child = spawn("pnpm", ["dev:apps"], {
  cwd: root,
  env,
  stdio: "inherit",
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}

child.on("error", (error) => {
  console.error(error.message);
  process.exit(1);
});
child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
  } else {
    process.exit(code ?? 1);
  }
});
