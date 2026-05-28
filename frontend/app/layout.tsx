import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "./components/Nav";

export const metadata: Metadata = {
  title: "Atlas",
  description: "Knowledge-gap-aware book recommendations for investing and trading.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <div className="min-h-screen bg-gray-50 flex flex-col items-center px-8">
          <Nav />
          {children}
        </div>
      </body>
    </html>
  );
}
