import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StatePanel } from "./state-panel";
import { VIEW_STATE } from "./view-state";

describe("StatePanel", () => {
  it.each([
    [VIEW_STATE.LOADING, "aria-busy=\"true\""],
    [VIEW_STATE.ERROR, "aria-live=\"assertive\""],
    [VIEW_STATE.EMPTY, "state-panel--empty"],
  ] as const)("renders the %s state accessibly", (state, marker) => {
    const markup = renderToStaticMarkup(
      <StatePanel state={state} title="상태 제목" description="상태 설명" />,
    );

    expect(markup).toContain(marker);
    expect(markup).toContain("상태 제목");
  });
});
