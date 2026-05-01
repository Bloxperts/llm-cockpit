// Thin fetch wrapper used across all pages. The cockpit serves the frontend
// at the same origin as the API, so no base URL configuration is needed —
// `fetch("/api/...", { credentials: "same-origin" })` is enough.

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(`HTTP ${status}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
    this.status = status;
    this.detail = detail;
  }
}

async function parseResponse(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  const ct = response.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) {
    try {
      return await response.json();
    } catch {
      return null;
    }
  }
  return await response.text();
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers,
  });
  const body = await parseResponse(response);
  if (!response.ok) {
    throw new ApiError(response.status, body);
  }
  return body as T;
}

// SSE streaming via fetch + ReadableStream. EventSource doesn't support POST,
// so we roll our own minimal parser. Yields { event, data } per SSE block.
export async function* streamSse(
  path: string,
  init: RequestInit = {},
): AsyncGenerator<{ event: string; data: string }> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: new Headers({
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers as Record<string, string> | undefined),
    }),
  });
  if (!response.ok) {
    const body = await parseResponse(response);
    throw new ApiError(response.status, body);
  }
  const reader = response.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  let buf = "";
  const nextBlock = () => {
    const lf = buf.indexOf("\n\n");
    const crlf = buf.indexOf("\r\n\r\n");
    if (lf === -1 && crlf === -1) return null;
    if (crlf !== -1 && (lf === -1 || crlf < lf)) {
      const block = buf.slice(0, crlf);
      buf = buf.slice(crlf + 4);
      return block;
    }
    const block = buf.slice(0, lf);
    buf = buf.slice(lf + 2);
    return block;
  };
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let block: string | null;
    while ((block = nextBlock()) !== null) {
      let event = "message";
      let data = "";
      for (const line of block.replace(/\r\n/g, "\n").split("\n")) {
        if (line.startsWith("event:")) {
          const body = line.slice("event:".length);
          event = body.startsWith(" ") ? body.slice(1) : body;
        } else if (line.startsWith("data:")) {
          const body = line.slice("data:".length);
          data += body.startsWith(" ") ? body.slice(1) : body;
        }
      }
      yield { event, data };
    }
  }
}
