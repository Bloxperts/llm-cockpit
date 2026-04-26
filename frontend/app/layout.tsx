// Root layout — minimal scaffold.

import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "llm-cockpit",
  description: "Local dashboard + chat UI for the Neuroforge LLM stack",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
