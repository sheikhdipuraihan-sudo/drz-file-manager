// Cloudflare Worker: CORS proxy for catbox.moe uploads.
// Browser -> this Worker -> catbox.moe. Catbox sends no CORS headers, so browsers cannot call it directly.
// Deploy: dash.cloudflare.com -> Workers & Pages -> Create -> paste this file.
// Then add a variable ALLOWED_ORIGINS = https://your-app.vercel.app (comma-separated for several).
// In your app set CATBOX_PROXY_URL = https://<your-worker>.workers.dev

const CATBOX_API = "https://catbox.moe/user/api.php";
const MAX_BYTES = 200 * 1024 * 1024; // keep in line with CATBOX_MAX_MB

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const allowed = (env.ALLOWED_ORIGINS || "").split(",").map((s) => s.trim()).filter(Boolean);
    const ok = allowed.includes(origin);
    const cors = {
      "Access-Control-Allow-Origin": ok ? origin : "null",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Max-Age": "86400",
      "Vary": "Origin",
    };

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    if (request.method !== "POST" || !ok) return new Response("Forbidden", { status: 403, headers: cors });

    const len = Number(request.headers.get("Content-Length") || 0);
    if (!len || len > MAX_BYTES) return new Response("Bad size", { status: 413, headers: cors });

    const upstream = await fetch(CATBOX_API, {
      method: "POST",
      headers: {
        "Content-Type": request.headers.get("Content-Type") || "",
        "Content-Length": String(len),
        "User-Agent": "DRZ-File-Manager/1.0",
      },
      body: request.body,
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { ...cors, "Content-Type": "text/plain; charset=utf-8" },
    });
  },
};
