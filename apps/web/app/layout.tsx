import type { Metadata } from "next";

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
        {children}
      </body>
    </html>
  );
}
