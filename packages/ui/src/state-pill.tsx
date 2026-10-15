import type { ReactNode } from "react";

export interface StatePillProps {
  children: ReactNode;
  muted?: boolean;
}

export function StatePill({ children, muted = false }: StatePillProps) {
  return <span className={`state-pill${muted ? " state-pill--muted" : ""}`}>{children}</span>;
}

