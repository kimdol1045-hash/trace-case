type ObjectUrlApi = Pick<typeof URL, "createObjectURL" | "revokeObjectURL">;

export type LoadAttachmentBlob = (attachmentId: string, signal: AbortSignal) => Promise<Blob>;

export interface ObjectUrlLease {
  url: string;
  revoke: () => void;
}

export function isImageAttachmentMimeType(mimeType: string) {
  return mimeType.startsWith("image/");
}

export function isTextAttachmentPreview(filename: string, mimeType: string) {
  const normalizedName = filename.trim().toLocaleLowerCase();
  return mimeType === "text/plain"
    || mimeType === "text/markdown"
    || normalizedName.endsWith(".txt")
    || normalizedName.endsWith(".md")
    || normalizedName.endsWith(".markdown");
}

export function resolveAttachmentMimeType(file: Pick<File, "name" | "type">) {
  if (file.type) return file.type;
  const normalizedName = file.name.trim().toLocaleLowerCase();
  if (normalizedName.endsWith(".md") || normalizedName.endsWith(".markdown")) return "text/markdown";
  if (normalizedName.endsWith(".txt")) return "text/plain";
  return "";
}

export async function readAttachmentText(blob: Blob, maxBytes = 2 * 1024 * 1024) {
  const truncated = blob.size > maxBytes;
  const text = await blob.slice(0, maxBytes).text();
  return { text, truncated };
}

export function createObjectUrlLease(blob: Blob, urlApi: ObjectUrlApi = URL): ObjectUrlLease {
  const url = urlApi.createObjectURL(blob);
  let active = true;
  return {
    url,
    revoke: () => {
      if (!active) return;
      active = false;
      urlApi.revokeObjectURL(url);
    },
  };
}

export async function loadPrivateAttachmentPreview(
  attachmentId: string,
  loadBlob: LoadAttachmentBlob,
  signal: AbortSignal,
  urlApi: ObjectUrlApi = URL,
) {
  if (signal.aborted) return null;
  const blob = await loadBlob(attachmentId, signal);
  if (signal.aborted) return null;
  return createObjectUrlLease(blob, urlApi);
}
