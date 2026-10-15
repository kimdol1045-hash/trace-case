import {
  forwardRef,
  useImperativeHandle,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

export interface LabelEditorHandle {
  /** Commits text that has not yet been confirmed with Enter or blur. */
  commitPending: () => string[];
}

// eslint-disable-next-line react-refresh/only-export-components -- exported for deterministic label-save tests.
export function appendPendingLabel(labels: string[], raw: string) {
  const label = raw.trim().replace(/^,+|,+$/g, "").trim();
  if (!label) return labels;
  const normalized = label.normalize("NFKC").toLocaleLowerCase("ko-KR");
  const exists = labels.some(
    (candidate) => candidate.trim().normalize("NFKC").toLocaleLowerCase("ko-KR") === normalized,
  );
  return exists ? labels : [...labels, label];
}

export const LabelEditor = forwardRef<LabelEditorHandle, { labels: string[]; onChange: (labels: string[]) => void; ariaLabel?: string }>(function LabelEditor(
  { labels, onChange, ariaLabel = "라벨" },
  ref,
) {
  const [input, setInput] = useState("");
  const inputRef = useRef("");
  const labelsRef = useRef(labels);
  labelsRef.current = labels;

  const addLabel = (raw = input) => {
    const next = appendPendingLabel(labelsRef.current, raw);
    if (next !== labelsRef.current) {
      labelsRef.current = next;
      onChange(next);
    }
    inputRef.current = "";
    setInput("");
    return next;
  };

  useImperativeHandle(ref, () => ({
    commitPending: () => addLabel(inputRef.current),
  }));

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      addLabel(input);
    }
    if (event.key === "Backspace" && !input && labels.length > 0) {
      onChange(labels.slice(0, -1));
    }
  };

  return (
    <div className="tm-label-editor" aria-label={ariaLabel}>
      {[...new Set(labels)].map((label) => (
        <span className="tm-label-chip" key={label}>
          {label}
          <button type="button" aria-label={`${label} 라벨 삭제`} onClick={() => onChange(labels.filter((candidate) => candidate !== label))}>×</button>
        </span>
      ))}
      <input value={input} onChange={(event) => { inputRef.current = event.target.value; setInput(event.target.value); }} onKeyDown={handleKeyDown} onBlur={() => addLabel(inputRef.current)} placeholder="라벨 추가" aria-label={`${ariaLabel} 추가`} />
    </div>
  );
});
