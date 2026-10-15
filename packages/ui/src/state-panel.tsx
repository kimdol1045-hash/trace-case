import type { ReactNode } from "react";

import { VIEW_STATE, type PendingViewState } from "./view-state";

export interface StatePanelProps {
  state: PendingViewState;
  title: string;
  description: string;
  action?: ReactNode;
  compact?: boolean;
}

export function StatePanel({
  state,
  title,
  description,
  action,
  compact = false,
}: StatePanelProps) {
  return (
    <section
      className={`state-panel state-panel--${state}${compact ? " state-panel--compact" : ""}`}
      aria-live={state === VIEW_STATE.ERROR ? "assertive" : "polite"}
      aria-busy={state === VIEW_STATE.LOADING}
    >
      <span className="state-panel__visual" aria-hidden="true">
        {state === VIEW_STATE.LOADING ? <span className="state-panel__spinner" /> : null}
        {state === VIEW_STATE.ERROR ? "!" : null}
        {state === VIEW_STATE.EMPTY ? "＋" : null}
      </span>
      <div className="state-panel__copy">
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      {action ? <div className="state-panel__action">{action}</div> : null}
    </section>
  );
}
