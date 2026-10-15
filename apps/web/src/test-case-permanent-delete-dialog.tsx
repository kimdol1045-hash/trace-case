import { useEffect, useId, useState, type FormEvent } from "react";

import { useAccessibleDialog } from "./dialog-accessibility";
import { isPermanentDeleteConfirmationValid } from "./test-case-permanent-delete-confirmation";

export function TestCasePermanentDeleteDialog({
  open,
  caseKey,
  summary,
  busy = false,
  error,
  onClose,
  onConfirm,
}: {
  open: boolean;
  caseKey: string;
  summary: string;
  busy?: boolean;
  error?: string | null;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const [confirmation, setConfirmation] = useState("");
  const confirmed = isPermanentDeleteConfirmationValid(caseKey, confirmation);
  const { dialogRef, onDialogKeyDown } = useAccessibleDialog<HTMLFormElement>(open, onClose, busy);

  useEffect(() => {
    if (!open) setConfirmation("");
  }, [open]);

  useEffect(() => {
    if (!open || typeof document === "undefined") return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  if (!open) return null;

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (confirmed && !busy) onConfirm();
  };

  return (
    <div className="tm-cycle-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target && !busy) onClose(); }}>
      <form ref={dialogRef} className="tm-cycle-modal tm-delete-confirm-modal" role="alertdialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} onKeyDown={onDialogKeyDown} onSubmit={submit}>
        <header><h2 id={titleId}>테스트케이스 완전 삭제</h2><button className="tm-modal-close" disabled={busy} type="button" aria-label="테스트케이스 완전 삭제 닫기" onClick={onClose}>×</button></header>
        <div className="tm-delete-confirm-modal__body">
          <span aria-hidden="true">!</span>
          <div>
            <strong>{caseKey} · {summary}</strong>
            <p id={descriptionId}>테스트케이스와 모든 버전이 제거되어 복구할 수 없습니다. 사이클·이슈·복사 이력이 참조 중이면 삭제되지 않으며 감사 기록은 보존됩니다. 계속하려면 아래에 <b>{caseKey}</b>를 정확히 입력하세요.</p>
            <label className="tm-field">
              <span>확인용 테스트케이스 키</span>
              <input autoFocus autoComplete="off" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} aria-label="완전 삭제 확인용 테스트케이스 키" placeholder={caseKey} disabled={busy} />
            </label>
            {error ? <p className="tm-field-error" role="alert">{error}</p> : null}
          </div>
        </div>
        <footer><button className="tm-subtle-button" disabled={busy} type="button" onClick={onClose}>취소</button><button className="tm-primary-button tm-delete-confirm-button" disabled={!confirmed || busy} type="submit">{busy ? "완전 삭제 중…" : "완전 삭제"}</button></footer>
      </form>
    </div>
  );
}
