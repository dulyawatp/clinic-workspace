const DEFAULT_PROXY_TARGET = "http://localhost:3000";

/** Local Fastify proxy. Override with CLINIC_PROXY_TARGET when it runs elsewhere. */
export function apiProxyTarget(env: NodeJS.ProcessEnv = process.env): string {
  return env.CLINIC_PROXY_TARGET?.trim() || DEFAULT_PROXY_TARGET;
}

/**
 * Same-origin dev forwarding for `/api`. `changeOrigin` stays false so the
 * browser Origin (`http://localhost:5173`) reaches the proxy unchanged and its
 * origin/CSRF checks apply; cookies pass through the same origin, so no CORS
 * relaxation is introduced.
 */
export function apiProxy(target: string) {
  return {
    "/api": {
      target,
      changeOrigin: false,
    },
  };
}
