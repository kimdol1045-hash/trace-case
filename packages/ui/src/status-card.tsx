import type { ReactNode } from "react";

export interface StatusCardProps {
  label: string;
  status: "ready" | "pending" | "error";
  children?: ReactNode;
}

export function StatusCard({ label, status, children }: StatusCardProps) {
  return (
    <section className={`status-card status-card--${status}`} aria-live="polite">
      <div className="status-card__heading">
        <span aria-hidden="true" className="status-card__dot" />
        <h2>{label}</h2>
      </div>
      {children}
    </section>
  );
}

