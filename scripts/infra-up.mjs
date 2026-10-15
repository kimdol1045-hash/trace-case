import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");

function run(args) {
  const result = spawnSync("docker", args, { cwd: root, stdio: "inherit" });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

run(["compose", "-f", "deploy/compose.dev.yml", "up", "-d", "--wait", "postgres", "minio", "clamav"]);
run(["compose", "-f", "deploy/compose.dev.yml", "run", "--rm", "minio-init"]);
