// Single-origin reverse proxy for the SSO end-to-end regression.
//
// Routes /api/* to the FastAPI backend (:3001) and everything else to the
// Next.js frontend (:3000), mirroring the production nginx split so the
// browser sees one origin and the refresh cookie (path=/api/auth) is sent
// correctly on same-origin API calls.

import { createServer, request as proxyRequest } from "node:http";

const PORT = Number(process.env.PROXY_PORT ?? 8090);
const BACKEND = process.env.E2E_BACKEND_URL ?? "http://127.0.0.1:3001";
const FRONTEND = process.env.E2E_FRONTEND_URL ?? "http://127.0.0.1:3000";

// Hop-by-hop headers must not be forwarded between the two HTTP hops.
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

const server = createServer((req, res) => {
  const upstream = (req.url ?? "/").startsWith("/api/") ? BACKEND : FRONTEND;
  const target = new URL(req.url ?? "/", upstream);

  const headers = {};
  for (const [name, value] of Object.entries(req.headers)) {
    if (value !== undefined && !HOP_BY_HOP.has(name)) headers[name] = value;
  }
  // Keep the browser-visible Host so the frontend generates correct URLs.
  headers.host = "localhost:" + PORT;

  const proxy = proxyRequest(
    target,
    { method: req.method, headers },
    (upstreamRes) => {
      res.writeHead(upstreamRes.statusCode ?? 502, upstreamRes.headers);
      upstreamRes.pipe(res);
    },
  );
  proxy.on("error", () => {
    res.writeHead(502, { "content-type": "text/plain" });
    res.end("upstream unavailable");
  });
  req.pipe(proxy);
});

server.listen(PORT, () => {
  console.log(`reverse-proxy listening on http://localhost:${PORT}`);
});
