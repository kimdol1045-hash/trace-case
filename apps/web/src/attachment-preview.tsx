import type { AttachmentResource } from "@tc/api-client";
import { useEffect, useState } from "react";

import {
  createObjectUrlLease,
  isImageAttachmentMimeType,
  isTextAttachmentPreview,
  loadPrivateAttachmentPreview,
  resolveAttachmentMimeType,
  type LoadAttachmentBlob,
  type ObjectUrlLease,
} from "./attachment-preview-utils";
import { AttachmentImagePreview } from "./attachment-image-lightbox";
import { AttachmentTextPreview } from "./attachment-text-preview";

type AttachmentPreviewResource = Pick<AttachmentResource, "id" | "filename" | "mimeType" | "resourceVersion">;

type PreviewState =
  | { phase: "file" }
  | { phase: "loading" }
  | { phase: "ready"; url: string }
  | { phase: "error" };

function AttachmentPreviewFrame({ filename, state, source }: { filename: string; state: PreviewState; source: "local" | "private" }) {
  if (state.phase === "file") {
    return <span className="tm-attachment-preview is-file" aria-hidden="true">▧</span>;
  }
  if (state.phase === "ready") {
    return <AttachmentImagePreview filename={filename} url={state.url} />;
  }
  if (state.phase === "error") {
    return <span className="tm-attachment-preview is-error" role="img" aria-label={`${filename} 미리보기를 불러오지 못했습니다`}>!</span>;
  }
  return <span className="tm-attachment-preview is-loading" role="status" aria-label={`${filename} ${source === "local" ? "로컬" : "비공개"} 미리보기 불러오는 중`}>…</span>;
}

export function LocalAttachmentPreview({ file }: { file: File }) {
  const mimeType = resolveAttachmentMimeType(file);
  const isImage = isImageAttachmentMimeType(mimeType);
  const isText = isTextAttachmentPreview(file.name, mimeType);
  const [state, setState] = useState<PreviewState>(() => isImage ? { phase: "loading" } : { phase: "file" });

  useEffect(() => {
    if (!isImage) {
      setState({ phase: "file" });
      return;
    }
    let lease: ObjectUrlLease | null = null;
    try {
      lease = createObjectUrlLease(file);
      setState({ phase: "ready", url: lease.url });
    } catch {
      setState({ phase: "error" });
    }
    return () => lease?.revoke();
  }, [file, isImage]);

  if (isText) return <AttachmentTextPreview filename={file.name} loadBlob={async () => file} />;
  return <AttachmentPreviewFrame filename={file.name} state={state} source="local" />;
}

export function PrivateAttachmentPreview({ attachment, loadBlob }: { attachment: AttachmentPreviewResource; loadBlob: LoadAttachmentBlob }) {
  const isImage = isImageAttachmentMimeType(attachment.mimeType);
  const isText = isTextAttachmentPreview(attachment.filename, attachment.mimeType);
  const [state, setState] = useState<PreviewState>(() => isImage ? { phase: "loading" } : { phase: "file" });

  useEffect(() => {
    if (!isImage) {
      setState({ phase: "file" });
      return;
    }
    const controller = new AbortController();
    let lease: ObjectUrlLease | null = null;
    setState({ phase: "loading" });
    void loadPrivateAttachmentPreview(attachment.id, loadBlob, controller.signal)
      .then((nextLease) => {
        if (!nextLease) return;
        if (controller.signal.aborted) {
          nextLease.revoke();
          return;
        }
        lease = nextLease;
        setState({ phase: "ready", url: nextLease.url });
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ phase: "error" });
      });
    return () => {
      controller.abort();
      lease?.revoke();
    };
  }, [attachment.id, attachment.mimeType, attachment.resourceVersion, isImage, loadBlob]);

  if (isText) return <AttachmentTextPreview filename={attachment.filename} loadBlob={(signal) => loadBlob(attachment.id, signal)} />;
  return <AttachmentPreviewFrame filename={attachment.filename} state={state} source="private" />;
}

export function LocalAttachmentFilenamePreview({ file }: { file: File }) {
  const mimeType = resolveAttachmentMimeType(file);
  if (!isTextAttachmentPreview(file.name, mimeType)) return <strong>{file.name}</strong>;
  return <AttachmentTextPreview filename={file.name} loadBlob={async () => file} variant="filename" />;
}

export function PrivateAttachmentFilenamePreview({ attachment, loadBlob }: { attachment: AttachmentPreviewResource; loadBlob: LoadAttachmentBlob }) {
  if (!isTextAttachmentPreview(attachment.filename, attachment.mimeType)) return <strong>{attachment.filename}</strong>;
  return <AttachmentTextPreview filename={attachment.filename} loadBlob={(signal) => loadBlob(attachment.id, signal)} variant="filename" />;
}
