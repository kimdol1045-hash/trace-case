import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { CasePersistenceBanner } from "./case-persistence-banner";

describe("CasePersistenceBanner", () => {
  it("makes optimistic-lock conflicts actionable", () => {
    const markup = renderToStaticMarkup(
      <CasePersistenceBanner
        persistence={{
          status: "CONFLICT",
          source: "API",
          message: "현재 버전은 7입니다.",
          nextCursor: null,
          total: 1,
        }}
        onRetry={vi.fn()}
      />,
    );

    expect(markup).toContain('role="alert"');
    expect(markup).toContain("현재 버전은 7입니다.");
    expect(markup).toContain("다시 시도");
  });

  it("identifies the PostgreSQL API persistence boundary", () => {
    const markup = renderToStaticMarkup(
      <CasePersistenceBanner
        persistence={{ status: "ERROR", source: "API", nextCursor: null, total: 9, message: "offline" }}
      />,
    );

    expect(markup).toContain("PostgreSQL API-backed state");
    expect(markup).not.toContain("mock");
  });

  it("warns without blocking editing after a partial history failure", () => {
    const markup = renderToStaticMarkup(
      <CasePersistenceBanner
        persistence={{ status: "PARTIAL", source: "API", message: "일부 버전 diff 실패", nextCursor: null, total: 1 }}
      />,
    );

    expect(markup).toContain("일부 버전 diff 실패");
    expect(markup).toContain("PostgreSQL API-backed state");
  });
});
