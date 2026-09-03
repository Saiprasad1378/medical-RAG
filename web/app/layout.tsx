import type { Metadata } from "next";
import { Fraunces, Geist } from "next/font/google";
import "./globals.css";

const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  weight: ["400","500","600","700"],
  display: "swap",
});

const geist = Geist({
  variable: "--font-geist",
  subsets: ["latin"],
  weight: ["400","500"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "MediAssist — your pdfs, answered.",
  description: "Safety-gated medical RAG. Ask your handbooks, get grounded answers with citations. Not medical advice.",
  openGraph: { title: "MediAssist", description: "Your PDFs, answered — with citations, not hallucinations." },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${fraunces.variable} ${geist.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col bg-[#fdfbf9] text-[#171717]">{children}</body>
    </html>
  );
}
