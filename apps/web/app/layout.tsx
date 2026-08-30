import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "India AI Financial Analyst",
  description: "India-first multimodal multi-agent equity research platform",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
