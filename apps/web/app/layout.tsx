import type { Metadata } from "next";
import Link from "next/link";

import { BrokerConnectionBar } from "./broker-connection-bar";
import "./broker.css";
import "./globals.css";
import "./readiness.css";
import { SystemReadinessBar } from "./system-readiness-bar";

export const metadata: Metadata = {
  title: "India AI Financial Analyst",
  description: "India-first multimodal multi-agent equity research platform",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <BrokerConnectionBar />
        <SystemReadinessBar />
        <nav
          aria-label="Primary"
          style={{
            display: "flex",
            gap: 16,
            justifyContent: "flex-end",
            maxWidth: 1180,
            margin: "0 auto",
            padding: "14px 24px 0",
            fontSize: 14,
          }}
        >
          <Link href="/">Research terminal</Link>
          <Link href="/watchlists">Watchlists</Link>
        </nav>
        {children}
      </body>
    </html>
  );
}
