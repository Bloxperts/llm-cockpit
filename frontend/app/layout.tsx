import type { Metadata } from "next";
import "./globals.css";

import { Providers } from "@/components/Providers";

export const metadata: Metadata = {
  title: "LLM Cockpit",
  description: "Multi-user dashboard + chat for Ollama",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col bg-neutral-50 text-neutral-900 dark:bg-neutral-950 dark:text-neutral-100">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
