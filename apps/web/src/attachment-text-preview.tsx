import { useEffect, useId, useState } from "react";

import { useAccessibleDialog } from "./dialog-accessibility";
import { readAttachmentText } from "./attachment-preview-utils";

type TextState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "ready"; text: string; truncated: boolean }
  | { phase: "error" };

export function AttachmentTextPreviewDialog({
  filename,
  open,
  state,
  onClose,
}: {
  filename: string;
  open: boolean;
  state: TextState;
  onClose: () => void;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const { dialogRef, onDialogKeyDown } = useAccessibleDialog<HTMLElement>(open, onClose);

  useEffect(() => {
    if (!open || typeof document === "undefined") return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="tm-attachment-lightbox-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target) onClose();
      }}
    >
      <section
        ref={dialogRef}
        className="tm-attachment-lightbox tm-attachment-text-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        tabIndex={-1}
        onKeyDown={onDialogKeyDown}
      >
        <header>
          <div>
            <span id={descriptionId}>텍스트 미리보기</span>
            <h2 id={titleId}>{filename}</h2>
          </div>
          <button autoFocus className="tm-modal-close" type="button" aria-label="텍스트 미리보기 닫기" onClick={onClose}>×</button>
        </header>
        <div className="tm-attachment-lightbox__document">
          {state.phase === "loading" || state.phase === "idle" ? <span className="tm-text-preview-message" role="status">내용을 불러오는 중…</span> : null}
          {state.phase === "error" ? <div className="tm-text-preview-message is-error" role="alert">파일 내용을 불러오지 못했습니다. 다운로드로 확인해 주세요.</div> : null}
          {state.phase === "ready" ? <>
            <pre>{state.text || "(빈 파일)"}</pre>
            {state.truncated ? <p className="tm-text-preview-truncated">파일이 커서 앞부분 2 MB만 표시합니다.</p> : null}
          </> : null}
        </div>
      </section>
    </div>
  );
}

export function AttachmentTextPreview({
  filename,
  loadBlob,
  variant = "thumbnail",
}: {
  filename: string;
  loadBlob: (signal: AbortSignal) => Promise<Blob>;
  variant?: "thumbnail" | "filename";
}) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<TextState>({ phase: "idle" });

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setState({ phase: "loading" });
    void loadBlob(controller.signal)
      .then((blob) => readAttachmentText(blob))
      .then((result) => {
        if (!controller.signal.aborted) setState({ phase: "ready", ...result });
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ phase: "error" });
      });
    return () => controller.abort();
  }, [loadBlob, open]);

  return <>
    <button
      className={variant === "filename" ? "tm-attachment-filename-preview" : "tm-attachment-preview is-file is-text"}
      type="button"
      aria-label={`${filename} 내용 보기`}
      aria-haspopup="dialog"
      onClick={() => setOpen(true)}
    >
      {variant === "filename" ? filename : filename.toLocaleLowerCase().endsWith(".txt") ? "TXT" : "MD"}
    </button>
    <AttachmentTextPreviewDialog filename={filename} open={open} state={state} onClose={() => setOpen(false)} />
  </>;
}
