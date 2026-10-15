import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StatusCard } from "./status-card";

describe("StatusCard", () => {
  it("announces and styles its state", () => {
    const markup = renderToStaticMarkup(
      <StatusCard label="API" status="ready">
        connected
      </StatusCard>,
    );

    expect(markup).toContain("aria-live=\"polite\"");
    expect(markup).toContain("status-card--ready");
    expect(markup).toContain("connected");
  });
});

