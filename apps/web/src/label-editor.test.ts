import { describe, expect, it } from "vitest";

import { appendPendingLabel } from "./label-editor";

describe("label editor pending value", () => {
  it("includes an unconfirmed label in the value saved by the parent", () => {
    expect(appendPendingLabel(["smoke"], " regression ")).toEqual(["smoke", "regression"]);
  });

  it("does not duplicate labels after unicode and case normalization", () => {
    const labels = ["Regression"];
    expect(appendPendingLabel(labels, "regression")).toBe(labels);
  });

  it("ignores empty separator input", () => {
    const labels = ["smoke"];
    expect(appendPendingLabel(labels, ", ,")).toBe(labels);
  });
});
