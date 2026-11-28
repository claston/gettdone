import type { Metadata } from "next";
import { ProductAnalytics } from "@/components/product-analytics";
import "./globals.css";

export const metadata: Metadata = {
  title: "OFX Simples PRO",
  description: "Workspace profissional para revisão de conversões bancárias.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="pt-BR" data-ofx-surface="pro">
      <body>
        {children}
        <ProductAnalytics />
      </body>
    </html>
  );
}
