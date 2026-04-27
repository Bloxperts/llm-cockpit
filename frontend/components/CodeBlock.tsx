"use client";

// Sprint 5 UX Features 1, 2, 8 — code-block renderer with header, language
// label, copy + (conditional) download buttons, and syntax highlighting.
//
// Used by the react-markdown component overrides in ChatShell.

import { useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";

const DOWNLOADABLE: Record<string, { ext: string; mime: string }> = {
  html: { ext: "html", mime: "text/html" },
  markdown: { ext: "md", mime: "text/markdown" },
  md: { ext: "md", mime: "text/markdown" },
  txt: { ext: "txt", mime: "text/plain" },
  json: { ext: "json", mime: "application/json" },
};

export function CodeBlock({
  language,
  children,
}: {
  language: string | null;
  children: string;
}) {
  const [copied, setCopied] = useState(false);
  const downloadInfo = language ? DOWNLOADABLE[language.toLowerCase()] : undefined;

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

  return (
    <div className="my-3 rounded-xl overflow-hidden border border-neutral-200 dark:border-neutral-800">
      <div className="bg-neutral-800 text-neutral-300 px-4 py-1.5 flex items-center justify-between text-xs">
        <span className="font-mono">{language || "code"}</span>
        <div className="flex items-center gap-1">
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
