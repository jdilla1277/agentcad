import { neon } from "@neondatabase/serverless";

const MAX_PAYLOAD_BYTES = 100_000; // 100KB
const RATE_LIMIT_PER_HOUR = 10;

// Simple in-memory rate limiter (resets on cold start, good enough for alpha)
const ipCounts = new Map<string, { count: number; resetAt: number }>();

function isRateLimited(ip: string): boolean {
  const now = Date.now();
  const entry = ipCounts.get(ip);
  if (!entry || now > entry.resetAt) {
    ipCounts.set(ip, { count: 1, resetAt: now + 3600_000 });
    return false;
  }
  entry.count++;
  return entry.count > RATE_LIMIT_PER_HOUR;
}

const EXPECTED_KEY = "agentcad-alpha-2026";

export async function POST(request: Request) {
  // Check API key
  if (request.headers.get("x-agentcad-key") !== EXPECTED_KEY) {
    return Response.json({ error: "Unauthorized." }, { status: 401 });
  }

  // Rate limit by IP
  const ip =
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    "unknown";
  if (isRateLimited(ip)) {
    return Response.json(
      { error: "Rate limited. Max 10 submissions per hour." },
      { status: 429 }
    );
  }

  // Check payload size
  const contentLength = request.headers.get("content-length");
  if (contentLength && parseInt(contentLength) > MAX_PAYLOAD_BYTES) {
    return Response.json(
      { error: "Payload too large. Max 100KB." },
      { status: 413 }
    );
  }

  let body: Record<string, unknown>;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "Invalid JSON." }, { status: 400 });
  }

  // Store in Neon
  const sql = neon(process.env.NEON_CONNECTION_STRING!);
  try {
    await sql`
      INSERT INTO feedback (ip, payload, created_at)
      VALUES (${ip}, ${JSON.stringify(body)}, NOW())
    `;
  } catch (e: unknown) {
    // Table might not exist yet — create it
    if (e instanceof Error && e.message.includes("does not exist")) {
      await sql`
        CREATE TABLE IF NOT EXISTS feedback (
          id SERIAL PRIMARY KEY,
          ip TEXT,
          payload JSONB,
          created_at TIMESTAMPTZ DEFAULT NOW()
        )
      `;
      await sql`
        INSERT INTO feedback (ip, payload, created_at)
        VALUES (${ip}, ${JSON.stringify(body)}, NOW())
      `;
    } else {
      console.error("Feedback storage error:", e);
      return Response.json(
        { error: "Internal error." },
        { status: 500 }
      );
    }
  }

  // Notify Discord
  try {
    await notifyDiscord(body);
  } catch {}

  return Response.json({ status: "received" });
}

async function notifyDiscord(bundle: Record<string, unknown>): Promise<string> {
  const webhookUrl = process.env.DISCORD_WEBHOOK_URL;
  if (!webhookUrl) return "no webhook url";

  const summary =
    (bundle.summary as string) ||
    (bundle.bundle as Record<string, unknown>)?.summary ||
    "No message";
  const signals = (bundle.friction_signals ||
    (bundle.bundle as Record<string, unknown>)?.friction_signals) as
    | Record<string, unknown>
    | undefined;

  const lines = [`**New agentcad feedback**`, `> ${summary}`];
  if (signals) {
    lines.push(
      `Errors: ${signals.errors ?? "?"} | Successes: ${signals.successes ?? "?"} | Retries: ${signals.total_retries ?? "?"}`
    );
  }

  const resp = await fetch(webhookUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: lines.join("\n") }),
  });
  return `${resp.status} ${resp.statusText}`;
}
