// Minimal OIDC identity provider for the SSO end-to-end regression.
//
// Serves discovery / JWKS / authorize / token / userinfo endpoints so a real
// browser can complete the authorization-code + PKCE flow against the backend
// without a real IdP. The id_token is signed with a genuine RS256 keypair that
// this process generates at startup and publishes via its JWKS endpoint.

import { createServer } from "node:http";
import { createHash, generateKeyPairSync, randomBytes, sign } from "node:crypto";

const PORT = Number(process.env.MOCK_IDP_PORT ?? 4000);
const ISSUER = process.env.MOCK_IDP_ISSUER ?? `http://localhost:${PORT}`;
// Base URL the *backend* uses for the server-to-server endpoints (token,
// userinfo, JWKS). Defaults to ISSUER so the in-process e2e harness (which
// runs the IdP and backend on one host) is unchanged; the QA compose stack
// overrides it to the service DNS name because a container cannot reach
// `localhost` on the host.
const BACKEND_BASE = process.env.MOCK_IDP_BACKEND_BASE ?? ISSUER;
const CLIENT_ID = process.env.OIDC_CLIENT_ID ?? "e2e-client";

const { publicKey, privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const jwk = publicKey.export({ format: "jwk" });

const JWKS = {
  keys: [{ kty: "RSA", alg: "RS256", use: "sig", kid: "e2e-key", n: jwk.n, e: jwk.e }],
};

const CONFIG = {
  issuer: ISSUER,
  authorization_endpoint: `${ISSUER}/authorize`,
  token_endpoint: `${BACKEND_BASE}/token`,
  userinfo_endpoint: `${BACKEND_BASE}/userinfo`,
  jwks_uri: `${BACKEND_BASE}/jwks`,
};

// code -> { nonce, codeChallenge, codeChallengeMethod }, populated at
// authorize, consumed at token.
const codes = new Map();

function b64url(buf) {
  return Buffer.from(buf).toString("base64url");
}

// RFC 7636 §4.2: S256(code_verifier) == code_challenge.
function s256(verifier) {
  return createHash("sha256").update(verifier).digest("base64url");
}

function signIdToken(claims) {
  const header = b64url(JSON.stringify({ alg: "RS256", kid: "e2e-key", typ: "JWT" }));
  const payload = b64url(JSON.stringify(claims));
  const input = `${header}.${payload}`;
  const signature = sign("sha256", Buffer.from(input), privateKey);
  return `${input}.${b64url(signature)}`;
}

function json(res, status, body) {
  const data = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(data),
  });
  res.end(data);
}

function readBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
  });
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", ISSUER);
  const path = url.pathname;

  if (path === "/.well-known/openid-configuration") {
    return json(res, 200, CONFIG);
  }

  if (path === "/jwks") {
    return json(res, 200, JWKS);
  }

  if (path === "/authorize") {
    const state = url.searchParams.get("state");
    const nonce = url.searchParams.get("nonce");
    const redirectUri = url.searchParams.get("redirect_uri");
    const codeChallenge = url.searchParams.get("code_challenge");
    const codeChallengeMethod = url.searchParams.get("code_challenge_method");
    if (!state || !nonce || !redirectUri || !codeChallenge) {
      return json(res, 400, { error: "invalid_request" });
    }
    const code = randomBytes(16).toString("base64url");
    codes.set(code, { nonce, codeChallenge, codeChallengeMethod });
    const target = new URL(redirectUri);
    target.searchParams.set("code", code);
    target.searchParams.set("state", state);
    res.writeHead(302, { location: target.toString() });
    return res.end();
  }

  if (path === "/token") {
    const body = new URLSearchParams(await readBody(req));
    const code = body.get("code");
    const verifier = body.get("code_verifier");
    const entry = codes.get(code);
    if (!entry) {
      return json(res, 400, { error: "invalid_grant" });
    }
    codes.delete(code);
    // Honour PKCE so a wrong verifier fails here, not silently at the backend.
    if (entry.codeChallengeMethod === "S256" && s256(verifier ?? "") !== entry.codeChallenge) {
      return json(res, 400, { error: "invalid_grant" });
    }
    const now = Math.floor(Date.now() / 1000);
    const idToken = signIdToken({
      iss: ISSUER,
      aud: CLIENT_ID,
      sub: "user-1",
      nonce: entry.nonce,
      iat: now,
      exp: now + 300,
    });
    return json(res, 200, {
      id_token: idToken,
      access_token: "e2e-idp-access-token",
      token_type: "Bearer",
    });
  }

  if (path === "/userinfo") {
    return json(res, 200, { sub: "user-1", email: "alice@acme.com", name: "Alice" });
  }

  return json(res, 404, { error: "not_found" });
});

server.listen(PORT, () => {
  console.log(`mock-idp listening on http://localhost:${PORT}`);
});
