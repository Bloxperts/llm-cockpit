"use client";

// Sprint 5 — Claude-style chat shell. Houses Features 1–8 of the UX
// runbook:
//   1. Copy button on fenced code blocks (delegated to <CodeBlock>).
//   2. Download button on html / md / txt / json blocks (CodeBlock).
//   3. Floating scroll-to-bottom button when not at bottom.
//   4. Thinking toggle — pipes `think: true` into the stream request.
//   5. Session token counter with progress bar.
//   6. Last-response time (frozen value of the live timer at 'done').
//   7. Live ⏱ timer while streaming.
//   8. Visual polish: dark sidebar, light main pane, bubbles, avatar
//      badge, syntax-highlighted code, redesigned compose card,
//      streaming cursor.

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { CodeBlock } from "@/components/CodeBlock";
import { ApiError, api, streamSse } from "@/lib/api";

const DEFAULT_CTX = 8192;

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
  num_ctx_default: number | null;
  messages: MessagePayload[];
}

interface ModelPickerEntry {
  name: string;
  tag: string | null;
  size_bytes: number;
}

// Sprint 6 — workspace file entry. Mirrors `cockpit/schemas.py::FileEntry`.
interface FileEntry {
  name: string;
  path: string;
  size_bytes: number;
  modified_at: string;
  is_dir: boolean;
}

export function ChatShell({ mode }: { mode: "chat" | "code" }) {
  const apiPrefix = `/api/${mode}`;
  const thinkingStorageKey = `cockpit_thinking_${mode}`;

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [models, setModels] = useState<ModelPickerEntry[]>([]);
  const [selected, setSelected] = useState<ConversationDetail | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [draft, setDraft] = useState("");
  const [streamingContent, setStreamingContent] = useState("");
  const [thinkingEnabled, setThinkingEnabled] = useState(false);

  // Features 6 + 7 — timers.
  const [sendStart, setSendStart] = useState<number | null>(null);
  const [liveElapsed, setLiveElapsed] = useState(0);
  const [lastResponseSeconds, setLastResponseSeconds] = useState<number | null>(null);

  // Feature 3 — scroll-to-bottom.
  const [atBottom, setAtBottom] = useState(true);

  // Sprint 6 — code workspace file panel (only meaningful when mode === 'code').
  const [files, setFiles] = useState<FileEntry[]>([]);
  const refreshFiles = useCallback(async () => {
    if (mode !== "code") return;
    try {
      const list = await api<FileEntry[]>("/api/code/files");
      setFiles(list);
    } catch {
      // Workspace may be empty or 401; surface elsewhere.
    }
  }, [mode]);

  // react-markdown component map — built per-mode so the Save button only
  // shows in code mode and its onSaved callback can refresh the file list.
  const markdownComponents = useMemo(
    () => buildMarkdownComponents(mode, refreshFiles),
    [mode, refreshFiles],
  );

  const messagesScrollRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  // Restore thinking toggle from localStorage on mount.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem(thinkingStorageKey);
    if (stored === "1") setThinkingEnabled(true);
  }, [thinkingStorageKey]);

  function toggleThinking() {
    setThinkingEnabled((prev) => {
      const next = !prev;
      if (typeof window !== "undefined") {
        window.localStorage.setItem(thinkingStorageKey, next ? "1" : "0");
      }
      return next;
    });
  }

  const refreshConversations = useCallback(async () => {
    const list = await api<ConversationSummary[]>(apiPrefix);
    setConversations(list);
  }, [apiPrefix]);

  const refreshSelected = useCallback(
    async (id: number) => {
      const detail = await api<ConversationDetail>(`${apiPrefix}/${id}`);
      setSelected(detail);
    },
    [apiPrefix],
  );

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
        /* picker stays empty if Ollama is unreachable */
      }
      // Sprint 6: prime the workspace file list when this is the Code page.
      if (mode === "code") void refreshFiles();
    })();
  }, [apiPrefix, mode, refreshFiles]);

  // Auto-scroll on new content (only when user is already at the bottom).
  useLayoutEffect(() => {
    if (atBottom) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [selected, streamingContent, atBottom]);

  // Feature 3 — IntersectionObserver to track whether the bottom anchor
  // is visible. When it isn't, the floating scroll-to-bottom button
  // shows up.
  useEffect(() => {
    const root = messagesScrollRef.current;
    const target = messagesEndRef.current;
    if (!root || !target) return;
    const observer = new IntersectionObserver(
      ([entry]) => setAtBottom(entry.isIntersecting),
      { root, threshold: 0.99 },
    );
    observer.observe(target);
    return () => observer.disconnect();
  }, [selected]);

  // Feature 7 — live timer while streaming.
  useEffect(() => {
    if (!streaming || sendStart === null) return;
    const id = window.setInterval(() => {
      setLiveElapsed((Date.now() - sendStart) / 1000);
    }, 100);
    return () => window.clearInterval(id);
  }, [streaming, sendStart]);

  async function newConversation() {
    const fallback = models[0]?.name ?? null;
    const created = await api<{ conversation_id: number; mode: string }>(apiPrefix, {
      method: "POST",
      body: JSON.stringify({ model: fallback }),
    });
    await refreshConversations();
    await refreshSelected(created.conversation_id);
    setLastResponseSeconds(null);
    composerRef.current?.focus();
  }

  async function selectConversation(id: number) {
    setStreamingContent("");
    setLastResponseSeconds(null);
    await refreshSelected(id);
  }

  async function consumeStream(url: string, body: object | undefined) {
    const start = Date.now();
    setSendStart(start);
    setLiveElapsed(0);
    setStreaming(true);
    setStreamingContent("");
    try {
      for await (const ev of streamSse(url, {
        method: "POST",
        body: body !== undefined ? JSON.stringify(body) : undefined,
      })) {
        if (ev.event === "token") {
          setStreamingContent((prev) => prev + ev.data);
        }
        if (ev.event === "done") {
          setLastResponseSeconds((Date.now() - start) / 1000);
          break;
        }
        if (ev.event === "error") {
          setLastResponseSeconds((Date.now() - start) / 1000);
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
      setSendStart(null);
      if (selected) {
        await refreshSelected(selected.id);
        await refreshConversations();
      }
    }
  }

  async function sendMessage() {
    if (!selected || !draft.trim() || streaming) return;
    const content = draft;
    setDraft("");
    await consumeStream(`${apiPrefix}/${selected.id}/stream`, {
      content,
      think: thinkingEnabled,
    });
  }

  async function regenerate() {
    if (!selected || streaming) return;
    await consumeStream(`${apiPrefix}/${selected.id}/regenerate`, undefined);
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

  // Feature 5 — token math.
  const tokenLimit = selected?.num_ctx_default ?? DEFAULT_CTX;
  const persistedTokens = useMemo(() => {
    if (!selected) return 0;
    let total = 0;
    for (const m of selected.messages) {
      if (m.role === "assistant") {
        total += (m.usage_in ?? 0) + (m.usage_out ?? 0);
      }
    }
    return total;
  }, [selected]);
  // Live estimate: ~4 chars/token (rough; accurate enough for a UI budget bar).
  const liveTokenEstimate = Math.ceil(streamingContent.length / 4);
  const sessionTokens = persistedTokens + liveTokenEstimate;
  const tokenPct = Math.min(100, (sessionTokens / Math.max(1, tokenLimit)) * 100);
  const tokenColor =
    tokenPct >= 95
      ? "bg-rose-500"
      : tokenPct >= 80
        ? "bg-amber-500"
        : "bg-neutral-400 dark:bg-neutral-500";

  return (
    <main className="flex-1 flex overflow-hidden">
      {/* Sidebar */}
      <aside className="w-64 flex-shrink-0 bg-neutral-900 text-neutral-100 overflow-y-auto flex flex-col">
        <div className="p-3">
          <button
            type="button"
            onClick={newConversation}
            className="w-full text-left px-3 py-2 rounded-lg bg-neutral-800 hover:bg-neutral-700 text-sm font-medium transition"
          >
            + New conversation
          </button>
        </div>
        <ul className="px-2 pb-3">
          {conversations.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                onClick={() => selectConversation(c.id)}
                className={`w-full text-left px-3 py-2 rounded-md text-sm transition ${
                  selected?.id === c.id
                    ? "bg-neutral-700"
                    : "hover:bg-neutral-800"
                }`}
              >
                <div className="font-medium truncate">
                  {c.title ?? `Conversation #${c.id}`}
                </div>
                <div className="text-xs text-neutral-400 truncate">
                  {c.model ?? "—"} · {c.message_count} msgs
                </div>
              </button>
            </li>
          ))}
        </ul>

        {mode === "code" ? (
          <FilesPanel files={files} onRefresh={refreshFiles} />
        ) : null}
      </aside>

      {/* Main pane */}
      <section className="flex-1 flex flex-col overflow-hidden bg-white dark:bg-neutral-950 relative">
        {selected ? (
          <>
            {/* Header */}
            <div className="px-4 py-2 flex items-center gap-3 text-sm border-b border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950">
              <span className="font-medium text-neutral-900 dark:text-neutral-100 truncate max-w-xs">
                {selected.title ?? `Conversation #${selected.id}`}
              </span>
              <select
                value={selected.model ?? ""}
                onChange={(e) => patchModel(e.target.value)}
                className="text-xs rounded-md border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 px-2 py-1 text-neutral-700 dark:text-neutral-200"
              >
                {models.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.name} · {m.tag ?? "—"}
                  </option>
                ))}
              </select>
              {lastResponseSeconds !== null ? (
                <span className="text-xs text-neutral-500 dark:text-neutral-400">
                  Last response: {lastResponseSeconds.toFixed(1)} s
                </span>
              ) : null}
              <button
                type="button"
                onClick={() => deleteConversation(selected.id)}
                className="ml-auto text-xs rounded-md border border-rose-300 dark:border-rose-800 text-rose-700 dark:text-rose-400 px-2 py-1 hover:bg-rose-50 dark:hover:bg-rose-950 transition"
              >
                Delete
              </button>
            </div>

            {/* Message list */}
            <div
              ref={messagesScrollRef}
              className="flex-1 overflow-y-auto px-6 py-8"
            >
              <div className="max-w-3xl mx-auto w-full flex flex-col gap-6">
                {selected.messages.map((m, idx) => {
                  const isLast = idx === selected.messages.length - 1;
                  return (
                    <MessageBubble
                      key={m.id}
                      m={m}
                      mode={mode}
                      streamingCursor={false}
                      // The persisted last-assistant has no streaming cursor
                      // — only the in-flight streaming bubble below does.
                      _isLast={isLast}
                      components={markdownComponents}
                    />
                  );
                })}
                {streaming && streamingContent ? (
                  <AssistantBubble
                    content={streamingContent}
                    mode={mode}
                    streamingCursor
                    error={null}
                    components={markdownComponents}
                  />
                ) : null}
                {selected.messages.length > 0 && !streaming ? (
                  <div className="flex justify-end">
                    <button
                      type="button"
                      onClick={regenerate}
                      className="text-xs rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1 hover:bg-neutral-50 dark:hover:bg-neutral-800 text-neutral-700 dark:text-neutral-300"
                    >
                      Regenerate
                    </button>
                  </div>
                ) : null}
                <div ref={messagesEndRef} />
              </div>
            </div>

            {/* Floating scroll-to-bottom button (Feature 3) */}
            {!atBottom && selected.messages.length > 0 ? (
              <button
                type="button"
                onClick={() =>
                  messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
                }
                aria-label="Scroll to latest"
                className="absolute right-6 bottom-44 z-10 rounded-full bg-neutral-900 text-white dark:bg-white dark:text-neutral-900 shadow-lg p-2 hover:opacity-90 transition"
              >
                <ArrowDownIcon />
              </button>
            ) : null}

            {/* Compose */}
            <div className="border-t border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 px-4 py-3">
              <div className="max-w-3xl mx-auto w-full">
                {/* Thinking toggle (left) + live timer (right) */}
                <div className="flex items-center justify-between mb-2 text-xs">
                  <button
                    type="button"
                    onClick={toggleThinking}
                    className={`px-2.5 py-1 rounded-full border transition ${
                      thinkingEnabled
                        ? "border-amber-400 bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
                        : "border-neutral-300 dark:border-neutral-700 text-neutral-500 dark:text-neutral-400 hover:bg-neutral-100 dark:hover:bg-neutral-800"
                    }`}
                  >
                    {thinkingEnabled ? "Think: on" : "Think: off"}
                  </button>
                  {streaming ? (
                    <span className="font-mono text-neutral-500 dark:text-neutral-400">
                      ⏱ {liveElapsed.toFixed(1)} s
                    </span>
                  ) : null}
                </div>

                {/* Compose box */}
                <div className="rounded-2xl border border-neutral-300 dark:border-neutral-700 bg-white dark:bg-neutral-900 shadow-sm p-2 flex items-end gap-2">
                  <textarea
                    ref={composerRef}
                    className={`flex-1 resize-none bg-transparent border-0 outline-0 px-2 py-1 max-h-48 ${
                      mode === "code" ? "font-mono text-sm" : "text-sm"
                    } text-neutral-900 dark:text-neutral-100 placeholder:text-neutral-400`}
                    rows={Math.min(8, Math.max(2, draft.split("\n").length))}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        void sendMessage();
                      }
                    }}
                    placeholder={
                      mode === "code"
                        ? "Ask a code question…  (Enter sends, Shift+Enter newline)"
                        : "Type a message…  (Enter sends, Shift+Enter newline)"
                    }
                    disabled={streaming}
                  />
                  <button
                    type="button"
                    onClick={sendMessage}
                    disabled={streaming || !draft.trim()}
                    aria-label={streaming ? "Streaming" : "Send"}
                    className="rounded-full p-2 bg-neutral-900 text-white dark:bg-white dark:text-neutral-900 disabled:opacity-40 hover:opacity-90 transition"
                  >
                    {streaming ? <LoadingIcon /> : <UpArrowIcon />}
                  </button>
                </div>

                {/* Token counter (Feature 5) */}
                <div className="mt-2 flex items-center gap-2 text-xs text-neutral-500 dark:text-neutral-400">
                  <div className="flex-1 h-1.5 rounded-full bg-neutral-200 dark:bg-neutral-800 overflow-hidden">
                    <div
                      className={`h-full ${tokenColor} transition-all`}
                      style={{ width: `${tokenPct}%` }}
                    />
                  </div>
                  <span className="font-mono">
                    {sessionTokens.toLocaleString()} / {tokenLimit.toLocaleString()} tokens
                  </span>
                </div>
              </div>
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-neutral-500 dark:text-neutral-400">
            Select a conversation or start a new one.
          </div>
        )}
      </section>
    </main>
  );
}

function AssistantBubble({
  content,
  mode,
  streamingCursor,
  error,
  components,
}: {
  content: string;
  mode: "chat" | "code";
  streamingCursor: boolean;
  error: string | null;
  components: Components;
}) {
  const _ = mode;
  return (
    <article className="relative pl-12">
      <div className="absolute left-0 top-0 w-8 h-8 rounded-full bg-orange-500 text-white flex items-center justify-center text-sm font-semibold select-none">
        C
      </div>
      <div className="prose prose-neutral dark:prose-invert max-w-none text-sm leading-relaxed">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
          {content || (streamingCursor ? "" : "*(empty)*")}
        </ReactMarkdown>
        {streamingCursor ? <span className="cockpit-cursor" /> : null}
      </div>
      {error ? (
        <div className="text-xs text-rose-600 dark:text-rose-400 mt-1">
          {error}
        </div>
      ) : null}
    </article>
  );
}

function MessageBubble({
  m,
  mode,
  streamingCursor,
  _isLast,
  components,
}: {
  m: MessagePayload;
  mode: "chat" | "code";
  streamingCursor: boolean;
  _isLast: boolean;
  components: Components;
}) {
  if (m.role === "user") {
    return (
      <article className="flex justify-end">
        <div className="max-w-[75%] rounded-2xl rounded-br-sm bg-neutral-100 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 px-4 py-3 text-sm">
          <div className="prose prose-neutral dark:prose-invert prose-sm max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
              {m.content}
            </ReactMarkdown>
          </div>
        </div>
      </article>
    );
  }
  return (
    <div>
      <AssistantBubble
        content={m.content}
        mode={mode}
        streamingCursor={streamingCursor}
        error={m.error}
        components={components}
      />
      {m.usage_out || m.gen_tps ? (
        <div className="ml-12 mt-1 text-xs text-neutral-500 dark:text-neutral-400 font-mono">
          {m.usage_in ? `prompt ${m.usage_in}` : ""}
          {m.usage_in && m.usage_out ? " · " : ""}
          {m.usage_out ? `out ${m.usage_out}` : ""}
          {m.gen_tps ? ` · ${m.gen_tps.toFixed(1)} tps` : ""}
          {m.latency_ms ? ` · ${(m.latency_ms / 1000).toFixed(1)} s` : ""}
        </div>
      ) : null}
    </div>
  );
}

// react-markdown v10 component overrides. The library calls `code` for
// both inline (`foo`) and block (```lang\n...\n```) code. v10 dropped the
// `inline` prop — we infer block-vs-inline from the language className +
// presence of newlines in the content.
//
// Sprint 6: built per-mode so the Save-to-workspace button only renders
// in code mode, and so its onSaved callback can refresh the Files panel.
function buildMarkdownComponents(
  mode: "chat" | "code",
  onSaved?: () => void,
): Components {
  return {
    code({ className, children, ...props }) {
      const text = String(children ?? "").replace(/\n$/, "");
      const match = /language-(\w+)/.exec(className ?? "");
      const isInline = !match && !text.includes("\n");
      if (isInline) {
        return (
          <code
            className="bg-neutral-100 dark:bg-neutral-800 px-1.5 py-0.5 rounded text-[0.85em] font-mono text-rose-600 dark:text-rose-400"
            {...props}
          >
            {children}
          </code>
        );
      }
      const language = match ? match[1] : null;
      return (
        <CodeBlock language={language} mode={mode} onSaved={onSaved}>
          {text}
        </CodeBlock>
      );
    },
    // The default <pre> wrapper would double-pad our <CodeBlock>. Strip it.
    pre({ children }) {
      return <>{children}</>;
    },
  };
}

function ArrowDownIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="12" y1="5" x2="12" y2="19" />
      <polyline points="5 12 12 19 19 12" />
    </svg>
  );
}

function UpArrowIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="12" y1="19" x2="12" y2="5" />
      <polyline points="5 12 12 5 19 12" />
    </svg>
  );
}

function LoadingIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="animate-spin">
      <path d="M21 12a9 9 0 1 1-6.22-8.56" />
    </svg>
  );
}

// Sprint 6 — workspace file drawer for the Code page sidebar.
function FilesPanel({
  files,
  onRefresh,
}: {
  files: FileEntry[];
  onRefresh: () => void;
}) {
  function fmtSize(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  }
  async function deleteFile(path: string) {
    if (!window.confirm(`Delete ${path} from your workspace?`)) return;
    try {
      await api(`/api/code/files?path=${encodeURIComponent(path)}`, {
        method: "DELETE",
      });
    } catch (e) {
      alert(`Delete failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      onRefresh();
    }
  }
  return (
    <div className="border-t border-neutral-800 mt-2">
      <div className="px-3 pt-3 pb-1 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-400">
          Workspace
        </h3>
        <button
          type="button"
          onClick={onRefresh}
          aria-label="Refresh file list"
          className="text-xs text-neutral-400 hover:text-white"
        >
          ↻
        </button>
      </div>
      {files.length === 0 ? (
        <div className="px-3 pb-3 text-xs text-neutral-500 italic">
          empty — use the Save button on a code block above
        </div>
      ) : (
        <ul className="px-2 pb-3">
          {files.map((f) => (
            <li
              key={f.path}
              className="px-2 py-1.5 rounded-md hover:bg-neutral-800 text-sm"
            >
              <div className="flex items-center gap-2">
                <span className="text-neutral-400 select-none" aria-hidden="true">
                  📄
                </span>
                <span className="font-mono text-xs truncate flex-1" title={f.path}>
                  {f.name}
                </span>
              </div>
              <div className="ml-7 mt-0.5 flex items-center gap-2 text-[10px] text-neutral-500">
                <span>{fmtSize(f.size_bytes)}</span>
                <a
                  href={`/api/code/files/download?path=${encodeURIComponent(f.path)}`}
                  download={f.name}
                  className="hover:text-white underline-offset-2 hover:underline"
                >
                  download
                </a>
                <button
                  type="button"
                  onClick={() => void deleteFile(f.path)}
                  className="hover:text-rose-400"
                >
                  delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
