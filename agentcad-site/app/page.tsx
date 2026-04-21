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

function CopyableBlock({
  text,
  label,
  className,
}: {
  text: string;
  label?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  return (
    <div className="relative group">
      <pre
        className={
          className ??
          "bg-gray-900 border border-gray-800 rounded-lg p-6 text-sm overflow-x-auto whitespace-pre-wrap"
        }
        aria-label={label}
      >
        {text}
      </pre>
      <button
        onClick={() => {
          navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
        className="absolute top-3 right-3 text-xs text-gray-500 hover:text-gray-200 bg-gray-800 border border-gray-700 rounded px-2 py-1 opacity-80 hover:opacity-100 transition"
        aria-label="Copy to clipboard"
      >
        {copied ? "Copied!" : "Copy"}
      </button>
    </div>
  );
}

const TRY_IT_PROMPT = `Create a Python 3.12 virtual environment, then:

pip install agentcad[mcp]
agentcad skill install
agentcad --help

Read the --help output — it's your operational briefing.

Then design me a phone stand: a simple angled cradle
that holds a phone at 60 degrees. About 80mm wide,
50mm deep, with a 5mm lip at the bottom to keep the
phone from sliding. Show me a preview when you're done.`;

const gallery = [
  {
    image: "/gallery-enclosure.png",
    alt: "3D render of a two-piece rectangular box with filleted vertical edges and a snap-fit lid with alignment lip, light gray on dark background, approximately 64x44x46mm overall",
    prompt: "Design a rounded electronics enclosure with a snap-fit lid. 60x40x25mm interior, 2mm walls, 3mm fillet on vertical edges.",
    metrics: "30 faces, 72 edges, 39cm\u00b3 volume, valid geometry",
  },
  {
    image: "/gallery-vase.png",
    alt: "3D render of a hollow revolved vase with a wide base, narrow waist, flared body, and lipped rim, dark gray on dark background, approximately 60x60x128mm",
    prompt: "Make an elegant vase \u2014 wide base, narrow waist, flared rim with a lip. About 128mm tall, hollow inside.",
    metrics: "15 faces, 28 edges, 107cm\u00b3 volume, valid geometry",
  },
  {
    image: "/gallery-rook.png",
    alt: "3D render of a chess rook with a stepped circular base, hourglass-tapered shaft, and crenellated crown with four battlements, light gray on dark background, approximately 30x30x64mm",
    prompt: "Model a chess rook. Lathe-revolved profile with a wide base, tapered shaft, and a crown with battlements.",
    metrics: "59 faces, 140 edges, 19.2cm\u00b3 volume, valid geometry",
  },
  {
    image: "/gallery-stand.png",
    alt: "3D render of a phone stand with flat base, vertical back support, and small front lip, light gray on dark background, approximately 80x50x69mm",
    prompt: "Design a phone stand \u2014 80mm wide, 50mm deep, with a back support and a front lip to keep the phone from sliding.",
    metrics: "10 faces, 24 edges, 42cm\u00b3 volume, valid geometry",
  },
];

export default function Home() {
  return (
    <main className="min-h-screen bg-black text-white">
      <div className="max-w-3xl mx-auto px-6 py-20">
        {/* Hero */}
        <h1 className="text-5xl font-bold mb-4">agentcad</h1>
        <p className="text-xl text-gray-400 mb-16">
          An MCP server and CLI that lets your coding agent design, render, and
          export 3D models.
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
                  <p className="px-4 pb-3 text-xs text-gray-600 font-mono">
                    {item.metrics}
                  </p>
                </figcaption>
              </figure>
            ))}
          </div>
        </section>

        {/* What it does */}
        <section className="mb-16" aria-label="Features">
          <h2 className="text-2xl font-semibold mb-4">What it does</h2>
          <p className="text-gray-400 mb-4">
            Agents write bad geometry on the first try. agentcad gives them a
            tight feedback loop — run, render, inspect, fix — so they converge
            on printable geometry without you babysitting.
          </p>
          <dl className="space-y-2 text-gray-300">
            <div>
              <dt className="inline font-semibold">Execute</dt>
              <dd className="inline">
                {" "}&mdash; run scripts, produce versioned STEP files +
                geometric metrics
              </dd>
            </div>
            <div>
              <dt className="inline font-semibold">Render</dt>
              <dd className="inline">
                {" "}&mdash; PNG views from any angle for visual verification
              </dd>
            </div>
            <div>
              <dt className="inline font-semibold">Export</dt>
              <dd className="inline">
                {" "}&mdash; STL, GLB, OBJ for 3D printing and web viewers
              </dd>
            </div>
            <div>
              <dt className="inline font-semibold">Validate</dt>
              <dd className="inline">
                {" "}&mdash; pre-execution checks catch errors in &lt;100ms
              </dd>
            </div>
            <div>
              <dt className="inline font-semibold">Inspect</dt>
              <dd className="inline">
                {" "}&mdash; topology report for debugging geometry issues
              </dd>
            </div>
            <div>
              <dt className="inline font-semibold">Diff</dt>
              <dd className="inline">
                {" "}&mdash; compare versions to track design iteration
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
          <CopyableBlock
            text={TRY_IT_PROMPT}
            label="Onboarding prompt for AI agents"
          />
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
            pip install agentcad[mcp]
          </pre>
          <p className="text-gray-400 text-sm mt-4">
            Requires Python 3.10–3.12. CadQuery/OpenCascade does not support
            3.13+.
          </p>
          <p className="text-gray-400 text-sm mt-2">
            CLI-only (no MCP server):{" "}
            <code className="bg-gray-900 px-1 rounded">
              pip install agentcad
            </code>
          </p>
        </section>

        {/* MCP */}
        <section className="mb-16" aria-label="MCP integration">
          <h2 className="text-2xl font-semibold mb-4">MCP integration</h2>
          <p className="text-gray-400 mb-4">
            Add to{" "}
            <code className="bg-gray-900 px-1 rounded">.mcp.json</code> for
            Claude Code, Cursor, or Windsurf:
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

        {/* Agent skill */}
        <section className="mb-16" aria-label="Agent skill marketplaces">
          <h2 className="text-2xl font-semibold mb-4">Agent skill</h2>
          <p className="text-gray-400 mb-4">
            agentcad is an installable agent skill on{" "}
            <a
              href="https://skills.sh"
              className="underline hover:text-white"
            >
              skills.sh
            </a>{" "}
            (Vercel) and{" "}
            <a
              href="https://clawhub.ai"
              className="underline hover:text-white"
            >
              ClawHub
            </a>{" "}
            (OpenClaw):
          </p>
          <div className="space-y-3">
            <div>
              <p className="text-xs text-gray-500 mb-1 font-mono">
                skills.sh (Vercel)
              </p>
              <pre className="bg-gray-900 border border-gray-800 rounded-lg p-4 text-sm overflow-x-auto">
                npx skills add jdilla1277/agentcad-skill
              </pre>
            </div>
            <div>
              <p className="text-xs text-gray-500 mb-1 font-mono">
                ClawHub (OpenClaw)
              </p>
              <pre className="bg-gray-900 border border-gray-800 rounded-lg p-4 text-sm overflow-x-auto">
                clawhub install jdilla1277/agentcad
              </pre>
            </div>
          </div>
          <p className="text-gray-400 text-sm mt-4">
            Listing:{" "}
            <a
              href="https://clawhub.ai/jdilla1277/agentcad"
              className="underline hover:text-white"
            >
              clawhub.ai/jdilla1277/agentcad
            </a>
            {" · "}
            Manifest:{" "}
            <a
              href="https://github.com/jdilla1277/agentcad-skill"
              className="underline hover:text-white"
            >
              github.com/jdilla1277/agentcad-skill
            </a>
          </p>
        </section>

        {/* Footer */}
        <footer className="border-t border-gray-800 pt-8 text-gray-500 text-sm flex items-center gap-4">
          <span>
            Built by{" "}
            <a href="https://jdilla.xyz" className="underline hover:text-white">
              James Dillard
            </a>
          </span>
          <span className="text-gray-700">&middot;</span>
          <a
            href="https://pypi.org/project/agentcad/"
            className="hover:text-white"
          >
            PyPI
          </a>
          <span className="text-gray-700">&middot;</span>
          <a
            href="https://github.com/jdilla1277/agentcad-skill"
            className="hover:text-white"
          >
            Skill
          </a>
        </footer>
      </div>
    </main>
  );
}
