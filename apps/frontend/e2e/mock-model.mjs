// Deterministic OpenAI-compatible model endpoint for the QA stack.
//
// The QA variant carries a real inference engine (Dify api/worker/plugin
// daemon), and that engine needs at least one configured model provider or its
// readiness probe stays negative and no agent can answer. A live provider would
// make the stack depend on an external service and a credential nobody may
// commit, so the stub IdP container also serves this minimal endpoint:
//
//   GET  /v1/models              list the deterministic model
//   POST /v1/chat/completions    JSON response or SSE stream
//   POST /v1/embeddings          deterministic vectors (knowledge-base paths)
//
// Answers are fixed strings derived from the request, so a browser assertion
// can be exact and a rerun produces the same bytes.

import { createHash } from "node:crypto";
import { createServer } from "node:http";

const DEFAULT_MODEL = "mock-gpt";
// One character per SSE frame: the agent-debug criterion is a per-character
// stream, and the backend forwards these frames as they arrive.
const STREAM_FRAME_DELAY_MS = 5;

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

/** Deterministic reply text: the prompt's hash selects a fixed sentence. */
function completionText(prompt) {
  const digest = createHash("sha256").update(prompt).digest("hex");
  return `确定性回答（${digest.slice(0, 8)}）：这是 QA 模型桩生成的固定文本。`;
}

/** A stable pseudo-random float vector for an embedding input. */
function embeddingVector(text, dimensions = 1536) {
  const digest = createHash("sha256").update(text).digest();
  const vector = new Array(dimensions);
  for (let index = 0; index < dimensions; index += 1) {
    vector[index] = (digest[index % digest.length] / 255) * 2 - 1;
  }
  return vector;
}

function chatPayload(body) {
  const model = body.model ?? DEFAULT_MODEL;
  const messages = Array.isArray(body.messages) ? body.messages : [];
  const prompt = messages
    .map((message) => `${message.role ?? "user"}:${String(message.content ?? "")}`)
    .join("\n");
  return { model, text: completionText(prompt || "empty") };
}

function chatCompletion(body) {
  const { model, text } = chatPayload(body);
  return {
    id: "chatcmpl-qa",
    object: "chat.completion",
    created: Math.floor(Date.now() / 1000),
    model,
    choices: [
      {
        index: 0,
        message: { role: "assistant", content: text },
        finish_reason: "stop",
      },
    ],
    usage: {
      prompt_tokens: 1,
      completion_tokens: [...text].length,
      total_tokens: [...text].length + 1,
    },
  };
}

function streamChatCompletion(res, body) {
  const { model, text } = chatPayload(body);
  const frames = [
    {
      id: "chatcmpl-qa",
      object: "chat.completion.chunk",
      created: Math.floor(Date.now() / 1000),
      model,
      choices: [{ index: 0, delta: { role: "assistant" }, finish_reason: null }],
    },
    ...[...text].map((char) => ({
      id: "chatcmpl-qa",
      object: "chat.completion.chunk",
      created: Math.floor(Date.now() / 1000),
      model,
      choices: [{ index: 0, delta: { content: char }, finish_reason: null }],
    })),
    {
      id: "chatcmpl-qa",
      object: "chat.completion.chunk",
      created: Math.floor(Date.now() / 1000),
      model,
      choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
    },
  ];

  res.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-store",
    connection: "keep-alive",
  });

  let index = 0;
  const writeNext = () => {
    if (index >= frames.length) {
      res.write("data: [DONE]\n\n");
      res.end();
      return;
    }
    res.write(`data: ${JSON.stringify(frames[index])}\n\n`);
    index += 1;
    setTimeout(writeNext, STREAM_FRAME_DELAY_MS);
  };
  writeNext();
}

function embeddings(body) {
  const inputs = Array.isArray(body.input) ? body.input : [body.input ?? ""];
  return {
    object: "list",
    data: inputs.map((input, index) => ({
      object: "embedding",
      index,
      embedding: embeddingVector(String(input)),
    })),
    model: body.model ?? "mock-embedding",
    usage: { prompt_tokens: 1, total_tokens: 1 },
  };
}

/**
 * Start the model stub on `port`; returns the listening server.
 *
 * The QA compose publishes it only inside the compose network — the engine's
 * provider credential points at `http://mock-idp:<port>/v1`.
 */
export function startMockModel({ port, host = "0.0.0.0", model = DEFAULT_MODEL }) {
  const server = createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", `http://localhost:${port}`);
    const path = url.pathname;

    if (req.method === "GET" && path === "/v1/models") {
      return json(res, 200, {
        object: "list",
        data: [
          {
            id: model,
            object: "model",
            created: 0,
            owned_by: "eap-qa",
          },
        ],
      });
    }

    if (req.method === "POST" && path === "/v1/chat/completions") {
      let body;
      try {
        body = JSON.parse((await readBody(req)) || "{}");
      } catch {
        return json(res, 400, { error: { message: "invalid JSON body", type: "invalid_request_error" } });
      }
      if (body.stream === true) {
        return streamChatCompletion(res, body);
      }
      return json(res, 200, chatCompletion(body));
    }

    if (req.method === "POST" && path === "/v1/embeddings") {
      let body;
      try {
        body = JSON.parse((await readBody(req)) || "{}");
      } catch {
        return json(res, 400, { error: { message: "invalid JSON body", type: "invalid_request_error" } });
      }
      return json(res, 200, embeddings(body));
    }

    if (req.method === "GET" && path === "/healthz") {
      return json(res, 200, { status: "ok", model });
    }

    return json(res, 404, { error: { message: "not_found", type: "invalid_request_error" } });
  });

  server.listen(port, host, () => {
    console.log(`mock-model listening on http://${host}:${port} (model ${model})`);
  });
  return server;
}
