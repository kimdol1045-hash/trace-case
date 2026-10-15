import { NavLink } from "react-router-dom";

const navigationItems = [
  { to: "/", label: "Overview", end: true },
  { to: "/test-cases", label: "Test Library" },
  { to: "/cycles", label: "Cycles" },
  { to: "/issues", label: "Issues" },
  { to: "/reports", label: "Reports" },
  { to: "/slack-work-items", label: "Slack Work Items" },
  { to: "/generation/failures", label: "자동 생성 실패" },
  { to: "/generation/quality-review", label: "품질 후보 검토" },
  { to: "/knowledge", label: "Knowledge" },
  { to: "/integrations", label: "Integrations" },
] as const;

export function AppBrand() {
  return (
    <NavLink className="brand-lockup" to="/" aria-label="TC overview">
      <span className="brand-lockup__mark">TC</span>
      <span>
        <strong>Trace Case</strong>
        <small>Test intelligence</small>
      </span>
    </NavLink>
  );
}

export function AppNavigation() {
  return (
    <ul className="navigation-list">
      {navigationItems.map((item) => (
        <li key={item.to}>
          <NavLink
            className={({ isActive }) =>
              `navigation-link${isActive ? " navigation-link--active" : ""}`
            }
            end={"end" in item ? item.end : false}
            to={item.to}
          >
            <span>{item.label}</span>
          </NavLink>
        </li>
      ))}
    </ul>
  );
}
