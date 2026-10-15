import { useEffect, useId } from "react";

import { useAccessibleDialog } from "./dialog-accessibility";

export function DeleteConfirmDialog({
  open,
  title,
  entityName,
  description,
  busy = false,
  onClose,
  onConfirm,
}: {
  open: boolean;
  title: string;
  entityName: string;
  description: string;
  busy?: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const { dialogRef, onDialogKeyDown } = useAccessibleDialog<HTMLElement>(open, onClose, busy);

  useEffect(() => {
    if (!open || typeof document === "undefined") return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  if (!open) return null;

  return <div className="tm-cycle-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target && !busy) onClose(); }}>
    <section ref={dialogRef} className="tm-cycle-modal tm-delete-confirm-modal" role="alertdialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} tabIndex={-1} onKeyDown={onDialogKeyDown}>
      <header><h2 id={titleId}>{title}</h2><button className="tm-modal-close" disabled={busy} type="button" aria-label={`${title} 닫기`} onClick={onClose}>×</button></header>
      <div className="tm-delete-confirm-modal__body"><span aria-hidden="true">!</span><div><strong>{entityName}</strong><p id={descriptionId}>{description}</p></div></div>
      <footer><button autoFocus className="tm-subtle-button" disabled={busy} type="button" onClick={onClose}>취소</button><button className="tm-primary-button tm-delete-confirm-button" disabled={busy} type="button" onClick={onConfirm}>{busy ? "삭제 중…" : "삭제"}</button></footer>
    </section>
  </div>;
}
