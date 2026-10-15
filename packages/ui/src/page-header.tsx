import type { ReactNode } from "react";

export interface PageHeaderProps {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}

export function PageHeader({ eyebrow, title, description, action }: PageHeaderProps) {
  return (
    <header className="page-header">
      <div className="page-header__copy">
        <p className="page-header__eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action ? <div className="page-header__action">{action}</div> : null}
    </header>
  );
}

