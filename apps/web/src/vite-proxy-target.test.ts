import { describe, expect, it } from "vitest";

import { resolveLocalApiProxyTarget } from "./vite-proxy-target";

describe("resolveLocalApiProxyTarget", () => {
  it("preserves the regular local API default", () => {
    expect(resolveLocalApiProxyTarget(undefined)).toBe("http://127.0.0.1:8000");
  });

  it("accepts a generated exact loopback port", () => {
    expect(resolveLocalApiProxyTarget("http://127.0.0.1:43127")).toBe("http://127.0.0.1:43127");
  });

  it.each([
    "https://127.0.0.1:43127",
    "http://localhost:43127",
    "http://127.0.0.1:43127/path",
    "http://user:secret@127.0.0.1:43127",
    "http://example.test:43127",
    "http://127.0.0.1:80",
  ])("rejects unsafe or noncanonical targets: %s", (value) => {
    expect(() => resolveLocalApiProxyTarget(value)).toThrow("exact loopback HTTP origin");
  });
});
