import type { ReactNode } from "react";

export interface AppShellProps {
  brand: ReactNode;
  navigation: ReactNode;
  children: ReactNode;
}

export function AppShell({ brand, navigation, children }: AppShellProps) {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        본문으로 건너뛰기
      </a>
      <aside className="app-sidebar" aria-label="주 탐색">
        <div className="app-brand">{brand}</div>
        <nav className="app-navigation">{navigation}</nav>
      </aside>
      <div className="app-workspace">
        <main className="app-content" id="main-content">
          {children}
        </main>
      </div>
    </div>
  );
}
