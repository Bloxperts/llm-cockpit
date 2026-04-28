"use client";

// Sprint 5 UX Features 1, 2, 8 — code-block renderer with header, language
// label, copy + (conditional) download buttons, and syntax highlighting.
//
// Sprint 6 (UC-06b) adds a Save-to-workspace button visible only in code
// mode. The Save button POSTs to /api/code/files/save; on 409 it prompts
// the user with an alternative filename.
//
// Used by the react-markdown component overrides in ChatShell.

import { useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";

import { ApiError, api } from "@/lib/api";

const DOWNLOADABLE: Record<string, { ext: string; mime: string }> = {
  html: { ext: "html", mime: "text/html" },
  markdown: { ext: "md", mime: "text/markdown" },
  md: { ext: "md", mime: "text/markdown" },
  txt: { ext: "txt", mime: "text/plain" },
  json: { ext: "json", mime: "application/json" },
};

// Map common Prism language tags to a sensible file extension for the
// Save-to-workspace button. Anything unmapped saves as `.txt`.
const LANG_TO_EXT: Record<string, string> = {
  python: "py",
  py: "py",
  javascript: "js",
  js: "js",
  typescript: "ts",
  ts: "ts",
  tsx: "tsx",
  jsx: "jsx",
  bash: "sh",
  shell: "sh",
  sh: "sh",
  html: "html",
  css: "css",
  json: "json",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  sql: "sql",
  go: "go",
  rust: "rs",
  rs: "rs",
  c: "c",
  cpp: "cpp",
  java: "java",
  ruby: "rb",
  rb: "rb",
  markdown: "md",
  md: "md",
  txt: "txt",
};

export function CodeBlock({
  language,
  children,
  mode,
  onSaved,
}: {
  language: string | null;
  children: string;
  // Sprint 6: when `mode === 'code'`, render the Save button. Default
  // 'chat' / undefined hides it (chat page doesn't have a workspace).
  mode?: "chat" | "code";
  onSaved?: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const downloadInfo = language ? DOWNLOADABLE[language.toLowerCase()] : undefined;
  const ext = (language && LANG_TO_EXT[language.toLowerCase()]) || "txt";

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(children);
    } catch {
      // Some browsers block clipboard without HTTPS — silently degrade.
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  function onDownload() {
    if (!downloadInfo) return;
    const blob = new Blob([children], { type: downloadInfo.mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `artifact.${downloadInfo.ext}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  async function onSaveToWorkspace() {
    let filename = window.prompt(
      "Save artifact as (relative to your workspace):",
      `artifact.${ext}`,
    );
    if (filename === null) return;
    filename = filename.trim();
    if (!filename) return;
    setSaveStatus("saving");
    try {
      await api("/api/code/files/save", {
        method: "POST",
        body: JSON.stringify({ path: filename, content: children, overwrite: false }),
      });
      setSaveStatus("saved");
      onSaved?.();
      setTimeout(() => setSaveStatus("idle"), 1500);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        if (window.confirm(`${filename} already exists in your workspace. Overwrite?`)) {
          try {
            await api("/api/code/files/save", {
              method: "POST",
              body: JSON.stringify({ path: filename, content: children, overwrite: true }),
            });
            setSaveStatus("saved");
            onSaved?.();
            setTimeout(() => setSaveStatus("idle"), 1500);
            return;
          } catch (e2) {
            setSaveStatus("error");
            alert(`Save failed: ${e2 instanceof Error ? e2.message : String(e2)}`);
            return;
          }
        }
        setSaveStatus("idle");
        return;
      }
      setSaveStatus("error");
      alert(`Save failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  return (
    <div className="my-3 rounded-xl overflow-hidden border border-neutral-200 dark:border-neutral-800">
      <div className="bg-neutral-800 text-neutral-300 px-4 py-1.5 flex items-center justify-between text-xs">
        <span className="font-mono">{language || "code"}</span>
        <div className="flex items-center gap-1">
          {mode === "code" ? (
            <button
              type="button"
              onClick={onSaveToWorkspace}
              disabled={saveStatus === "saving"}
              aria-label={
                saveStatus === "saved"
                  ? "Saved to workspace"
                  : saveStatus === "saving"
                    ? "Saving…"
                    : "Save to workspace"
              }
              title="Save to your workspace"
              className="rounded px-2 py-0.5 hover:bg-neutral-700 text-neutral-300 hover:text-white disabled:opacity-50"
            >
              {saveStatus === "saved" ? <CheckIcon /> : <SaveIcon />}
            </button>
          ) : null}
          {downloadInfo ? (
            <button
              type="button"
              onClick={onDownload}
              aria-label={`Download as .${downloadInfo.ext}`}
              className="rounded px-2 py-0.5 hover:bg-neutral-700 text-neutral-300 hover:text-white"
            >
              <DownloadIcon />
            </button>
          ) : null}
          <button
            type="button"
            onClick={onCopy}
            aria-label={copied ? "Copied" : "Copy code"}
            className="rounded px-2 py-0.5 hover:bg-neutral-700 text-neutral-300 hover:text-white"
          >
            {copied ? <CheckIcon /> : <CopyIcon />}
          </button>
        </div>
      </div>
      <SyntaxHighlighter
        language={language || "text"}
        style={oneDark}
        customStyle={{ margin: 0, padding: "1rem", fontSize: "0.875rem" }}
        wrapLongLines={false}
      >
        {children}
      </SyntaxHighlighter>
    </div>
  );
}

function CopyIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  );
}

function SaveIcon() {
  // Cloud-upload icon — distinct from "download" so the two are easy to
  // tell apart.
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M16 16l-4-4-4 4" />
      <path d="M12 12v9" />
      <path d="M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3" />
      <path d="M16 16l-4-4-4 4" />
    </svg>
  );
}
