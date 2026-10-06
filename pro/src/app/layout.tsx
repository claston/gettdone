import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "OFX Simples PRO",
  description: "Workspace profissional para revisão de conversões bancárias.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
