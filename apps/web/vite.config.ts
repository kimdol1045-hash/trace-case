import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

import { resolveLocalApiProxyTarget } from "./src/vite-proxy-target";

declare const process: { env: Record<string, string | undefined> };

const apiProxyTarget = resolveLocalApiProxyTarget(process.env.TC_VITE_PROXY_TARGET);

export default defineConfig({
  plugins: [react()],
  server: {
    allowedHosts: [".trycloudflare.com"],
    proxy: {
      "/api": apiProxyTarget,
    },
    strictPort: true,
  },
  preview: {
    allowedHosts: [".trycloudflare.com"],
    proxy: {
      "/api": apiProxyTarget,
    },
    strictPort: true,
  },
});
