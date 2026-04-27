"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api, streamSse } from "@/lib/api";

interface ConversationSummary {
  id: number;
  mode: string;
  title: string | null;
  model: string | null;
  system_prompt: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
}

interface MessagePayload {
  id: number;
  role: string;
  content: string;
  model: string | null;
  usage_in: number | null;
  usage_out: number | null;
  gen_tps: number | null;
  latency_ms: number | null;
  ts: string;
  error: string | null;
}

interface ConversationDetail {
  id: number;
  mode: string;
  title: string | null;
  model: string | null;
  system_prompt: string | null;
  created_at: string;
  updated_at: string;
  messages: MessagePayload[];
}

interface ModelPickerEntry {
  name: string;
  tag: string | null;
  size_bytes: number;
}

export function ChatShell({ mode }: { mode: "chat" | "code" }) {
  const apiPrefix = `/api/${mode}`;
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [models, setModels] = useState<ModelPickerEntry[]>([]);
  const [selected, setSelected] = useState<ConversationDetail | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [draft, setDraft] = useState("");
  const [streamingContent, setStreamingContent] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const refreshConversations = useCallback(async () => {
    const list = await api<ConversationSummary[]>(apiPrefix);
    setConversations(list);
  }, [apiPrefix]);

  const refreshSelected = useCallback(async (id: number) => {
    const detail = await api<ConversationDetail>(`${apiPrefix}/${id}`);
    setSelected(detail);
  }, [apiPrefix]);

  // Initial load.
  useEffect(() => {
    void (async () => {
      try {
        const list = await api<ConversationSummary[]>(apiPrefix);
        setConversations(list);
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) {
          window.location.replace("/login/");
          return;
        }
        if (e instanceof ApiError && e.status === 409) {
          window.location.replace("/change-password/");
          return;
        }
      }
      try {
        const ms = await api<ModelPickerEntry[]>(`/api/models?tag=${mode}`);
        setModels(ms);
      } catch {
        // Ollama might be unreachable — leave the picker empty.
      }
    })();
  }, [apiPrefix, mode]);

  // Auto-scroll on new content.
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [selected, streamingContent]);

  async function newConversation() {
    const fallback = models[0]?.name ?? null;
    const created = await api<{ conversation_id: number; mode: string }>(apiPrefix, {
      method: "POST",
      body: JSON.stringify({ model: fallback }),
    });
    await refreshConversations();
    await refreshSelected(created.conversation_id);
  }

  async function selectConversation(id: number) {
    setStreamingContent("");
    await refreshSelected(id);
  }

  async function sendMessage() {
    if (!selected || !draft.trim() || streaming) return;
    const content = draft;
    setDraft("");
    setStreaming(true);
    setStreamingContent("");
    try {
      for await (const ev of streamSse(`${apiPrefix}/${selected.id}/stream`, {
        method: "POST",
        body: JSON.stringify({ content }),
      })) {
        if (ev.event === "token") {
          setStreamingContent((prev) => prev + ev.data);
        }
        if (ev.event === "done" || ev.event === "error") {
          break;
        }
      }
    } catch (e) {
      if (e instanceof ApiError) {
        alert(`Stream failed: ${e.status}`);
      }
    } finally {
      setStreaming(false);
      setStreamingContent("");
      await refreshSelected(selected.id);
      await refreshConversations();
    }
  }

  async function regenerate() {
    if (!selected || streaming) return;
    setStreaming(true);
    setStreamingContent("");
    try {
      for await (const ev of streamSse(`${apiPrefix}/${selected.id}/regenerate`, {
        method: "POST",
      })) {
        if (ev.event === "token") {
          setStreamingContent((prev) => prev + ev.data);
        }
        if (ev.event === "done" || ev.event === "error") break;
      }
    } catch (e) {
      if (e instanceof ApiError) {
        alert(`Regenerate failed: ${e.status}`);
      }
    } finally {
      setStreaming(false);
      setStreamingContent("");
      await refreshSelected(selected.id);
    }
  }

  async function patchModel(modelName: string) {
    if (!selected) return;
    await api(`${apiPrefix}/${selected.id}`, {
      method: "PATCH",
      body: JSON.stringify({ model: modelName }),
    });
    await refreshSelected(selected.id);
  }

  async function deleteConversation(id: number) {
    if (!window.confirm("Delete this conversation? This cannot be undone.")) return;
    await api(`${apiPrefix}/${id}`, { method: "DELETE" });
    if (selected?.id === id) setSelected(null);
    await refreshConversations();
  }

  return (
    <main className="flex-1 grid grid-cols-[280px_1fr] overflow-hidden">
      <aside className="border-r border-neutral-200 dark:border-neutral-800 overflow-y-auto bg-white dark:bg-neutral-950">
        <button
          type="button"
          onClick={newConversation}
          className="w-full text-left px-4 py-3 border-b border-neutral-200 dark:border-neutral-800 font-medium hover:bg-neutral-50 dark:hover:bg-neutral-900"
        >
          + New conversation
        </button>
        <ul>
          {conversations.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                onClick={() => selectConversation(c.id)}
                className={`w-full text-left px-4 py-2 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-900 ${
                  selected?.id === c.id ? "bg-neutral-100 dark:bg-neutral-800" : ""
                }`}
              >
                <div className="font-medium truncate">
                  {c.title ?? `Conversation #${c.id}`}
                </div>
                <div className="text-xs text-neutral-500 truncate">
                  {c.model ?? "—"} · {c.message_count} msgs
                </div>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="flex flex-col overflow-hidden">
        {selected ? (
          <>
            <div className="border-b border-neutral-200 dark:border-neutral-800 px-4 py-2 flex items-center gap-3 text-sm bg-white dark:bg-neutral-950">
              <span className="font-medium truncate">
                {selected.title ?? `Conversation #${selected.id}`}
              </span>
              <select
                value={selected.model ?? ""}
                onChange={(e) => patchModel(e.target.value)}
                className="text-xs rounded border border-neutral-300 dark:border-neutral-700 bg-transparent px-2 py-1"
              >
                {models.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.name} · {m.tag ?? "—"}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => deleteConversation(selected.id)}
                className="ml-auto text-xs rounded border border-rose-300 dark:border-rose-800 text-rose-700 dark:text-rose-300 px-2 py-1 hover:bg-rose-50 dark:hover:bg-rose-950"
              >
                Delete
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4 max-w-4xl mx-auto w-full">
              {selected.messages.map((m) => (
                <MessageBubble key={m.id} m={m} mode={mode} />
              ))}
              {streaming && streamingContent ? (
                <article className="rounded-lg p-3 bg-neutral-100 dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 prose prose-sm dark:prose-invert max-w-none">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{streamingContent}</ReactMarkdown>
                </article>
              ) : null}
              {selected.messages.length > 0 ? (
                <div className="flex gap-2 justify-end text-sm">
                  <button
                    type="button"
                    onClick={regenerate}
                    disabled={streaming}
                    className="rounded border border-neutral-300 dark:border-neutral-700 px-3 py-1 text-xs disabled:opacity-50"
                  >
                    Regenerate
                  </button>
                </div>
              ) : null}
              <div ref={messagesEndRef} />
            </div>

            <div className="border-t border-neutral-200 dark:border-neutral-800 px-4 py-3 bg-white dark:bg-neutral-950">
              <div className="flex gap-2 max-w-4xl mx-auto w-full">
                <textarea
                  className={`flex-1 resize-none rounded-md border border-neutral-300 dark:border-neutral-700 bg-transparent px-3 py-2 ${
                    mode === "code" ? "font-mono text-sm" : "text-sm"
                  }`}
                  rows={3}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void sendMessage();
                    }
                  }}
                  placeholder={mode === "code" ? "Ask a code question…" : "Type a message…"}
                  disabled={streaming}
                />
                <button
                  type="button"
                  onClick={sendMessage}
                  disabled={streaming || !draft.trim()}
                  className="self-end rounded-md bg-neutral-900 dark:bg-neutral-100 dark:text-neutral-900 text-white px-4 py-2 text-sm font-medium disabled:opacity-50"
                >
                  {streaming ? "Streaming…" : "Send"}
                </button>
              </div>
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-neutral-500">
            Select a conversation or start a new one.
          </div>
        )}
      </section>
    </main>
  );
}

function MessageBubble({ m, mode }: { m: MessagePayload; mode: "chat" | "code" }) {
  const isUser = m.role === "user";
  const align = isUser ? "ml-auto bg-emerald-50 dark:bg-emerald-950 border-emerald-200 dark:border-emerald-900" : "mr-auto bg-neutral-100 dark:bg-neutral-900 border-neutral-200 dark:border-neutral-800";
  return (
    <article className={`max-w-3xl rounded-lg border p-3 ${align}`}>
      <div className="text-xs text-neutral-500 mb-1">{m.role}{m.error ? ` · ${m.error}` : ""}</div>
      <div className={mode === "code" ? "prose prose-sm prose-pre:font-mono dark:prose-invert max-w-none" : "prose prose-sm dark:prose-invert max-w-none"}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content || "*(empty)*"}</ReactMarkdown>
      </div>
      {m.role === "assistant" && (m.usage_out || m.gen_tps) ? (
        <div className="text-xs text-neutral-500 mt-2 font-mono">
          {m.usage_in ? `prompt ${m.usage_in}` : ""}
          {m.usage_in && m.usage_out ? " · " : ""}
          {m.usage_out ? `out ${m.usage_out}` : ""}
          {m.gen_tps ? ` · ${m.gen_tps.toFixed(1)} tps` : ""}
        </div>
      ) : null}
    </article>
  );
}
