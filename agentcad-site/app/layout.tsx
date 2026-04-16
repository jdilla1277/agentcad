import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "agentcad — CAD tool for AI agents",
  description:
    "Give your coding agent the ability to design 3D models. Execute CadQuery scripts, render PNGs, export STEP/STL/GLB, validate geometry, and diff versions. pip install agentcad.",
  metadataBase: new URL("https://agentcad.dev"),
  openGraph: {
    title: "agentcad — CAD tool for AI agents",
    description:
      "Give your coding agent the ability to design 3D models. Execute CadQuery scripts, render PNGs, export STEP/STL/GLB.",
    url: "https://agentcad.dev",
    siteName: "agentcad",
    type: "website",
    images: [
      {
        url: "/gallery-enclosure.png",
        width: 800,
        height: 600,
        alt: "Electronics enclosure designed by an AI agent using agentcad",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "agentcad — CAD tool for AI agents",
    description:
      "Give your coding agent the ability to design 3D models. pip install agentcad.",
    images: ["/gallery-enclosure.png"],
  },
  keywords: [
    "agentcad",
    "CAD",
    "AI agent",
    "CadQuery",
    "3D modeling",
    "STEP",
    "STL",
    "GLB",
    "MCP",
    "Claude Code",
    "Cursor",
  ],
  authors: [{ name: "James Dillard", url: "https://jdilla.xyz" }],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <head>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              "@context": "https://schema.org",
              "@type": "SoftwareApplication",
              name: "agentcad",
              description:
                "CAD tool for AI agents. Execute CadQuery scripts, render PNGs, export STEP/STL/GLB, validate geometry, and diff versions.",
              applicationCategory: "DeveloperApplication",
              operatingSystem: "macOS, Linux, Windows",
              url: "https://agentcad.dev",
              author: {
                "@type": "Person",
                name: "James Dillard",
                url: "https://jdilla.xyz",
              },
              offers: {
                "@type": "Offer",
                price: "0",
                priceCurrency: "USD",
              },
              softwareRequirements: "Python 3.10-3.12",
              installUrl: "https://pypi.org/project/agentcad/",
            }),
          }}
        />
      </head>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
