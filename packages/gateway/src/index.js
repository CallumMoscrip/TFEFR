/**
 * Gateway entry point.
 * - HTTP server for health and (later) REST endpoints.
 * - WebSocket endpoint (/ws) for chat/events.
 * - For MVP it simply echoes user messages as assistant replies.
 *
 * NOTE:
 *  - In production, terminate TLS upstream and keep this behind a reverse proxy.
 *  - Add auth (JWT/OIDC), schema validation, rate limiting as you mature.
 */
import "dotenv/config";
import express from "express";
import cors from "cors";
import { createServer } from "http";
import { WebSocketServer } from "ws";

const PORT = process.env.PORT ? Number(process.env.PORT) : 4000;

const app = express();
app.use(cors());
app.use(express.json());

app.get("/health", (_req, res) => {
  res.status(200).json({ ok: true, service: "gateway", time: new Date().toISOString() });
});

const httpServer = createServer(app);

// WebSocket endpoint (path-scoped)
const wss = new WebSocketServer({ server: httpServer, path: "/ws" });

/** Simple broadcast helper (not used in MVP but useful later) */
const broadcast = (data) => {
  const msg = typeof data === "string" ? data : JSON.stringify(data);
  wss.clients.forEach((client) => {
    if (client.readyState === 1) client.send(msg);
  });
};

wss.on("connection", (ws, req) => {
  // Minimal connection greeting
  ws.send(JSON.stringify({
    v: 1,
    type: "system.message",
    role: "system",
    payload: { text: "Gateway connected. Ready for commands." }
  }));

  ws.on("message", (raw) => {
    let msg;
    try { msg = JSON.parse(raw.toString()); } catch {
      ws.send(JSON.stringify({
        v: 1,
        type: "system.message",
        role: "system",
        payload: { text: "Invalid JSON received." }
      }));
      return;
    }

    // MVP: handle only 'user.message'
    if (msg?.type === "user.message" && msg?.payload?.text) {
      const userText = msg.payload.text;

      // TODO: Call proprietary interpreter and AI2-THOR here.
      // For now, echo back to prove the loop works.
      ws.send(JSON.stringify({
        v: 1,
        type: "system.message",
        role: "assistant",
        payload: { text: `Echo: ${userText}` }
      }));

      // You can also send an operational update message like this:
      ws.send(JSON.stringify({
        v: 1,
        type: "system.update",
        payload: {
          interpreter: { status: "stubbed" },
          robot: { state: "idle" }
        }
      }));
    } else {
      ws.send(JSON.stringify({
        v: 1,
        type: "system.message",
        role: "system",
        payload: { text: "Unsupported message type." }
      }));
    }
  });

  ws.on("close", () => {
    // Clean-up per-connection resources here if needed.
  });
});

// Optional: heartbeat to keep the connection alive on certain proxies.
const HEARTBEAT_MS = 30000;
setInterval(() => {
  wss.clients.forEach((client) => {
    if (client.readyState === 1) {
      try { client.ping(); } catch {}
    }
  });
}, HEARTBEAT_MS);

httpServer.listen(PORT, () => {
  console.log(`[gateway] listening on http://localhost:${PORT} (WS path: /ws)`);
});
