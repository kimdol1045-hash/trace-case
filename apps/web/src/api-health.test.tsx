import type { HealthResponse } from "@tc/api-client";
import { VIEW_STATE, type AsyncResource } from "@tc/ui";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ApiHealthIndicator, ApiHealthPanel } from "./api-health";

const healthy: AsyncResource<HealthResponse> = {
  status: VIEW_STATE.READY,
  data: { service: "api", status: "ok", version: "0.1.0" },
};

describe("API health UI", () => {
  it("renders generated-client response data", () => {
    const markup = renderToStaticMarkup(
      <ApiHealthPanel resource={healthy} onRetry={vi.fn()} />,
    );

    expect(markup).toContain("개발 환경이 연결되었습니다");
    expect(markup).toContain("0.1.0");
  });

  it("renders a retry control for connection errors", () => {
    const markup = renderToStaticMarkup(
      <ApiHealthIndicator
        resource={{ status: VIEW_STATE.ERROR, message: "offline" }}
        onRetry={vi.fn()}
      />,
    );

    expect(markup).toContain("API 재연결");
    expect(markup).toContain("button");
  });
});

