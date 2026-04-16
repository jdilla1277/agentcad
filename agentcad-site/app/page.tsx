"use client";

import { useState } from "react";

function CopyPrompt({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      className="w-full text-left p-4 text-sm text-gray-400 italic hover:bg-gray-800 transition-colors cursor-pointer group"
      title="Click to copy prompt"
    >
      &ldquo;{text}&rdquo;
      <span className="block text-xs text-gray-600 mt-1 group-hover:text-gray-400 transition-colors">
        {copied ? "Copied!" : "Click to copy"}
      </span>
    </button>
  );
}

const gallery = [
  {
    image: "/gallery-enclosure.png",
    alt: "Rounded electronics enclosure with lid, rendered in agentcad",
    prompt: "Design a rounded electronics enclosure with a snap-fit lid. 60x40x25mm interior, 2mm walls, 3mm fillet on vertical edges.",
  },
  {
    image: "/gallery-vase.png",
    alt: "Elegant vase with curved profile, rendered in agentcad",
    prompt: "Make an elegant vase — wide base, narrow waist, flared rim with a lip. About 128mm tall, hollow inside.",
  },
  {
    image: "/gallery-rook.png",
    alt: "Chess rook piece, rendered in agentcad",
    prompt: "Model a chess rook. Lathe-revolved profile with a wide base, tapered shaft, and a crown with battlements.",
  },
  {
    image: "/gallery-stand.png",
    alt: "Phone stand rendered in agentcad",
    prompt: "Design a phone stand — 80mm wide, 50mm deep, with a back support and a front lip to keep the phone from sliding.",
  },
];

export default function Home() {
  return (
    <main className="min-h-screen bg-black text-white">
      <div className="max-w-3xl mx-auto px-6 py-20">
        {/* Hero */}
        <h1 className="text-5xl font-bold mb-1">agentcad</h1>
        <p className="text-sm text-gray-500 mb-6">
          by{" "}
          <a href="https://jdilla.xyz" className="underline hover:text-gray-300">
            James Dillard
          </a>
        </p>
        <p className="text-xl text-gray-400 mb-16">
          CAD tool for AI agents. Give your coding agent the ability to design
          3D models.
        </p>

        {/* Gallery */}
        <section className="mb-20" aria-label="Models made with agentcad">
          <h2 className="text-2xl font-semibold mb-2">Made with agentcad</h2>
          <p className="text-gray-400 mb-8">
            Each of these was designed by an AI agent using a single prompt.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
            {gallery.map((item) => (
              <figure
                key={item.image}
                className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden"
              >
                <img
                  src={item.image}
                  alt={item.alt}
                  width={800}
                  height={600}
                  className="w-full aspect-[4/3] object-cover"
                />
                <figcaption>
                  <CopyPrompt text={item.prompt} />
                </figcaption>
              </figure>
            ))}
          </div>
        </section>

        {/* What it does */}
        <section className="mb-16" aria-label="Features">
          <h2 className="text-2xl font-semibold mb-4">What it does</h2>
          <p className="text-gray-400 mb-4">
            Your agent writes CadQuery Python scripts. agentcad handles
            everything else:
          </p>
          <dl className="space-y-2 text-gray-300">
            <div>
              <dt className="inline font-semibold">Execute</dt>
              <dd className="inline">
                {" "}— run scripts, produce versioned STEP files + geometric
                metrics
              </dd>
            </div>
            <div>
              <dt className="inline font-semibold">Render</dt>
              <dd className="inline">
                {" "}— PNG views from any angle for visual verification
              </dd>
            </div>
            <div>
              <dt className="inline font-semibold">Export</dt>
              <dd className="inline">
                {" "}— STL, GLB, OBJ for 3D printing and web viewers
              </dd>
            </div>
            <div>
              <dt className="inline font-semibold">Validate</dt>
              <dd className="inline">
                {" "}— pre-execution checks catch errors in &lt;100ms
              </dd>
            </div>
            <div>
              <dt className="inline font-semibold">Inspect</dt>
              <dd className="inline">
                {" "}— topology report for debugging geometry issues
              </dd>
            </div>
            <div>
              <dt className="inline font-semibold">Diff</dt>
              <dd className="inline">
                {" "}— compare versions to track design iteration
              </dd>
            </div>
          </dl>
        </section>

        {/* Try it */}
        <section className="mb-16" aria-label="Try it">
          <h2 className="text-2xl font-semibold mb-4">Try it</h2>
          <p className="text-gray-400 mb-4">
            Install agentcad, then paste this prompt into{" "}
            <strong className="text-gray-300">Claude Code</strong>,{" "}
            <strong className="text-gray-300">Cursor</strong>, or any coding
            agent:
          </p>
          <pre
            className="bg-gray-900 border border-gray-800 rounded-lg p-6 text-sm overflow-x-auto whitespace-pre-wrap"
            aria-label="Onboarding prompt for AI agents"
          >
{`Create a Python 3.12 virtual environment, then:

pip install agentcad
agentcad skill install
agentcad --help

Read the --help output — it's your operational briefing.

Then design me a phone stand: a simple angled cradle
that holds a phone at 60 degrees. About 80mm wide,
50mm deep, with a 5mm lip at the bottom to keep the
phone from sliding. Show me a preview when you're done.`}
          </pre>
        </section>

        {/* No boilerplate */}
        <section className="mb-16" aria-label="No boilerplate">
          <h2 className="text-2xl font-semibold mb-4">No boilerplate</h2>
          <p className="text-gray-400 mb-4">
            Scripts need zero imports.{" "}
            <code className="bg-gray-900 px-1 rounded">cq</code>,{" "}
            <code className="bg-gray-900 px-1 rounded">show_object</code>, and
            16 geometry helpers are pre-injected:
          </p>
          <pre className="bg-gray-900 border border-gray-800 rounded-lg p-6 text-sm overflow-x-auto">
{`box = cq.Workplane('XY').box(10, 20, 5)
show_object(box)`}
          </pre>
        </section>

        {/* Install */}
        <section className="mb-16" aria-label="Installation">
          <h2 className="text-2xl font-semibold mb-4">Install</h2>
          <pre className="bg-gray-900 border border-gray-800 rounded-lg p-6 text-sm">
            pip install agentcad
          </pre>
          <p className="text-gray-400 text-sm mt-4">
            Requires Python 3.10–3.12. CadQuery/OpenCascade does not support
            3.13+.
          </p>
        </section>

        {/* MCP */}
        <section className="mb-16" aria-label="MCP integration">
          <h2 className="text-2xl font-semibold mb-4">MCP integration</h2>
          <p className="text-gray-400 mb-4">
            For native tool integration with Claude Code, Cursor, or Windsurf:
          </p>
          <pre className="bg-gray-900 border border-gray-800 rounded-lg p-6 text-sm mb-4">
            pip install agentcad[mcp]
          </pre>
          <p className="text-gray-400 text-sm mb-4">
            Add to{" "}
            <code className="bg-gray-900 px-1 rounded">.mcp.json</code>:
          </p>
          <pre className="bg-gray-900 border border-gray-800 rounded-lg p-6 text-sm overflow-x-auto">
{`{
  "agentcad": {
    "command": "python",
    "args": ["-m", "agentcad.mcp"]
  }
}`}
          </pre>
        </section>

        {/* Footer */}
        <footer className="border-t border-gray-800 pt-8 text-gray-500 text-sm flex items-center gap-4">
          <a
            href="https://pypi.org/project/agentcad/"
            className="hover:text-white"
          >
            PyPI
          </a>
          <span className="text-gray-700">·</span>
          <a href="https://jdilla.xyz" className="hover:text-white">
            jdilla.xyz
          </a>
        </footer>
      </div>
    </main>
  );
}
