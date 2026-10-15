import { describe, expect, it } from "vitest";

import { wrappedDialogFocusIndex } from "./dialog-accessibility";

describe("dialog keyboard focus wrapping", () => {
  it("wraps Tab and Shift+Tab at both dialog edges", () => {
    expect(wrappedDialogFocusIndex(0, 3, true)).toBe(2);
    expect(wrappedDialogFocusIndex(2, 3, false)).toBe(0);
    expect(wrappedDialogFocusIndex(1, 3, false)).toBe(2);
    expect(wrappedDialogFocusIndex(-1, 3, false)).toBe(0);
    expect(wrappedDialogFocusIndex(0, 0, false)).toBe(-1);
  });
});
