import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const baselinePath = join(repoRoot, "docs/phase-a/screenshots/baseline.json");
const manifest = JSON.parse(await readFile(baselinePath, "utf8"));

function jpegDimensions(bytes) {
  if (bytes[0] !== 0xff || bytes[1] !== 0xd8) throw new Error("not a JPEG");
  let offset = 2;
  while (offset + 8 < bytes.length) {
    if (bytes[offset] !== 0xff) { offset += 1; continue; }
    const marker = bytes[offset + 1];
    if (marker >= 0xc0 && marker <= 0xc3) {
      return { height: bytes.readUInt16BE(offset + 5), width: bytes.readUInt16BE(offset + 7) };
    }
    const length = bytes.readUInt16BE(offset + 2);
    if (length < 2) break;
    offset += length + 2;
  }
  throw new Error("JPEG dimensions not found");
}

for (const baseline of manifest.baselines) {
  const path = join(dirname(baselinePath), baseline.file);
  const bytes = await readFile(path);
  const { width, height } = jpegDimensions(bytes);
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  if (width !== baseline.pixelWidth || height !== baseline.pixelHeight) {
    throw new Error(`${baseline.file}: expected ${baseline.pixelWidth}x${baseline.pixelHeight}, got ${width}x${height}`);
  }
  if (sha256 !== baseline.sha256) throw new Error(`${baseline.file}: baseline checksum changed; review and recapture intentionally`);
  console.log(`verified ${baseline.route} ${baseline.viewport} (${width}x${height})`);
}
