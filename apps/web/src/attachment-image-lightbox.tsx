import { useEffect, useId, useState } from "react";

import { useAccessibleDialog } from "./dialog-accessibility";

function safeObjectUrl(value: string) {
  try {
    return new URL(value).protocol === "blob:" ? value : null;
  } catch {
    return null;
  }
}

export function AttachmentLightboxDialog({
  filename,
  url,
  open,
  onClose,
}: {
  filename: string;
  url: string;
  open: boolean;
  onClose: () => void;
}) {
  const titleId = useId();
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
  const safeUrl = safeObjectUrl(url);
  if (safeUrl === null) return null;

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
        className="tm-attachment-lightbox"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={onDialogKeyDown}
      >
        <header>
          <div>
            <span>이미지 미리보기</span>
            <h2 id={titleId}>{filename}</h2>
          </div>
          <button autoFocus className="tm-modal-close" type="button" aria-label="이미지 미리보기 닫기" onClick={onClose}>×</button>
        </header>
        <div className="tm-attachment-lightbox__canvas">
          <img alt={`${filename} 확대 이미지`} src={safeUrl} />
        </div>
      </section>
    </div>
  );
}

export function AttachmentImagePreview({ filename, url }: { filename: string; url: string }) {
  const [open, setOpen] = useState(false);
  const safeUrl = safeObjectUrl(url);

  if (safeUrl === null) {
    return <span className="tm-attachment-preview is-error" role="img" aria-label={`${filename} 미리보기 주소가 올바르지 않습니다`}>!</span>;
  }

  return (
    <>
      <button
        className="tm-attachment-preview is-ready"
        type="button"
        aria-label={`${filename} 크게 보기`}
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
      >
        <img alt={`${filename} 미리보기`} src={safeUrl} />
      </button>
      <AttachmentLightboxDialog filename={filename} url={safeUrl} open={open} onClose={() => setOpen(false)} />
    </>
  );
}
