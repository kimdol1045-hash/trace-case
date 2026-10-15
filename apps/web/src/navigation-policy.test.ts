import { describe, expect, it } from "vitest";

import { appNavigationOptions } from "./navigation-policy";

describe("screen navigation policy", () => {
  it("pushes entry screens so browser back returns to the source list", () => {
    expect(appNavigationOptions.openCreate.replace).toBe(false);
    expect(appNavigationOptions.openDetail.replace).toBe(false);
    expect(appNavigationOptions.openExecution.replace).toBe(false);
  });

  it("replaces transient or invalid screens after successful mutations", () => {
    expect(appNavigationOptions.cancelCreate.replace).toBe(true);
    expect(appNavigationOptions.createSuccess.replace).toBe(true);
    expect(appNavigationOptions.deleteSuccess.replace).toBe(true);
    expect(appNavigationOptions.returnToList.replace).toBe(true);
    expect(appNavigationOptions.canonicalRedirect.replace).toBe(true);
  });
});
