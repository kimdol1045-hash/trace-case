import { useEffect, useRef, type KeyboardEvent as ReactKeyboardEvent } from "react";

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function wrappedDialogFocusIndex(current: number, count: number, backwards: boolean) {
  if (count <= 0) return -1;
  if (current < 0) return backwards ? count - 1 : 0;
  if (backwards && current === 0) return count - 1;
  if (!backwards && current === count - 1) return 0;
  return current + (backwards ? -1 : 1);
}

/** Adds Escape, focus containment, initial focus and focus restoration to a dialog. */
export function useAccessibleDialog<T extends HTMLElement>(
  open: boolean,
  onClose: () => void,
  closeDisabled = false,
) {
  const dialogRef = useRef<T>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const wasOpenRef = useRef(false);
  if (open && !wasOpenRef.current && typeof document !== "undefined") {
    restoreFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  }
  wasOpenRef.current = open;
  const onDialogKeyDown = (event: ReactKeyboardEvent<T>) => {
    if (event.key === "Escape" && !closeDisabled) {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(focusableSelector) ?? [])
      .filter((element) => !element.hidden && element.getAttribute("aria-hidden") !== "true");
    if (!focusable.length) { event.preventDefault(); dialogRef.current?.focus(); return; }
    const current = focusable.indexOf(document.activeElement as HTMLElement);
    const next = wrappedDialogFocusIndex(current, focusable.length, event.shiftKey);
    if (current < 0 || next === 0 || next === focusable.length - 1) {
      event.preventDefault();
      focusable[next]?.focus();
    }
  };

  useEffect(() => {
    if (!open) return;
    const previous = restoreFocusRef.current;
    const timeout = window.setTimeout(() => {
      const initial = dialogRef.current?.querySelector<HTMLElement>("[autofocus]")
        ?? dialogRef.current?.querySelector<HTMLElement>(focusableSelector);
      initial?.focus({ preventScroll: true });
    });
    return () => {
      window.clearTimeout(timeout);
      window.setTimeout(() => {
        if (previous?.isConnected) previous.focus({ preventScroll: true });
      });
    };
  }, [open]);

  return { dialogRef, onDialogKeyDown };
}
