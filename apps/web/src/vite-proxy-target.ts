const DEFAULT_LOCAL_API_PROXY_TARGET = "http://127.0.0.1:8000";

/**
 * Keep the development proxy loopback-only while allowing isolated local
 * rehearsals to allocate a collision-free API port.
 */
export function resolveLocalApiProxyTarget(value: string | undefined): string {
  const candidate = value ?? DEFAULT_LOCAL_API_PROXY_TARGET;
  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    throw new Error("TC_VITE_PROXY_TARGET must be an exact loopback HTTP origin");
  }
  const port = Number(parsed.port);
  if (
    parsed.protocol !== "http:"
    || parsed.hostname !== "127.0.0.1"
    || !Number.isInteger(port)
    || port < 1024
    || port > 65_535
    || parsed.username
    || parsed.password
    || parsed.pathname !== "/"
    || parsed.search
    || parsed.hash
    || candidate !== `http://127.0.0.1:${port}`
  ) {
    throw new Error("TC_VITE_PROXY_TARGET must be an exact loopback HTTP origin");
  }
  return candidate;
}
