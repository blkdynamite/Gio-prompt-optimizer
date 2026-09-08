// Vercel serverless function: POST /api/signup  { "email": "..." }
//
// The browser never talks to Supabase. This function holds the key (set
// SUPABASE_KEY in the Vercel project; the publishable key is enough because
// the database function is insert-only), hashes the caller's IP with that key
// so the raw address is never stored, and forwards to the rate-limited
// public.gio_signup() RPC. Responses: 204 ok, 400 bad email, 429 rate
// limited, 405 wrong method, 500 misconfigured.

const crypto = require("crypto");

const SUPABASE_URL = "https://isvgkmrgoxysyflkyyyg.supabase.co";
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// Best-effort per-instance throttle in front of the database limit.
const recent = new Map(); // ipHash -> [timestamps]
const LOCAL_WINDOW_MS = 60_000;
const LOCAL_MAX = 5;

function clientIp(req) {
  const xf = req.headers["x-forwarded-for"];
  const first = Array.isArray(xf) ? xf[0] : (xf || "").split(",")[0];
  return (first || req.socket?.remoteAddress || "").trim();
}

function throttled(ipHash) {
  const now = Date.now();
  const hits = (recent.get(ipHash) || []).filter((t) => now - t < LOCAL_WINDOW_MS);
  hits.push(now);
  recent.set(ipHash, hits);
  if (recent.size > 5000) recent.clear();
  return hits.length > LOCAL_MAX;
}

module.exports = async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST");
    return res.status(405).end();
  }

  const key = process.env.SUPABASE_KEY;
  if (!key) return res.status(500).json({ error: "signup not configured" });

  let body = req.body;
  if (typeof body === "string") {
    try { body = JSON.parse(body); } catch { body = {}; }
  }
  const email = String((body && body.email) || "").trim();
  if (!EMAIL_RE.test(email) || email.length > 254) {
    return res.status(400).json({ error: "invalid email" });
  }

  const ipHash = crypto.createHmac("sha256", key).update(clientIp(req)).digest("hex");
  if (throttled(ipHash)) return res.status(429).json({ error: "rate limited" });

  const upstream = await fetch(`${SUPABASE_URL}/rest/v1/rpc/gio_signup`, {
    method: "POST",
    headers: {
      apikey: key,
      Authorization: `Bearer ${key}`,
      "Content-Type": "application/json",
      Prefer: "return=minimal",
    },
    body: JSON.stringify({ p_email: email, p_source: "landing", p_ip_hash: ipHash }),
  });

  if (upstream.ok) return res.status(204).end();

  let detail = "";
  try { detail = (await upstream.json()).message || ""; } catch {}
  if (/rate limited/i.test(detail)) return res.status(429).json({ error: "rate limited" });
  if (/invalid email/i.test(detail)) return res.status(400).json({ error: "invalid email" });
  return res.status(502).json({ error: "signup failed" });
};
