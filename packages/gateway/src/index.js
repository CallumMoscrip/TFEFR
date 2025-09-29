// packages/gateway/src/index.js
import "dotenv/config";
import express from "express";
import cors from "cors";
import { createServer } from "http";
import { WebSocketServer } from "ws";
import { PythonBridge } from "./pythonBridge.js";

const app = express();
app.use(cors());
app.use(express.json());
app.get("/health", (_req, res) => res.json({ ok: true, service: "gateway", time: new Date().toISOString() }));

const httpServer = createServer(app);
const wss = new WebSocketServer({ server: httpServer, path: "/ws" });

// Configure & start Python interpreter
const PYTHON_CMD  = process.env.PYTHON_CMD  || "python3";
const PYTHON_ARGS = (process.env.PYTHON_ARGS || "-u server/interpreter.py").split(" ");

const py = new PythonBridge({ cmd: PYTHON_CMD, args: PYTHON_ARGS, responseTimeoutMs: 8000 });
py.start();

py.on("status", (s) => console.log("[python]", s));
py.on("stderr", (line) => console.warn("[python:stderr]", line));

// WebSocket handling
wss.on("connection", (ws) => {
  ws.send(JSON.stringify({ v: 1, type: "system.message", role: "system", payload: { text: "Connected. Ready." } }));

  ws.on("message", async (raw) => {
    let msg;
    try { msg = JSON.parse(raw.toString()); } catch {
      ws.send(JSON.stringify({ v: 1, type: "system.message", role: "system", payload: { text: "Invalid JSON" } }));
      return;
    }

    if (msg?.type === "user.message" && msg?.payload?.text) {
      const userText = String(msg.payload.text);

      // Send to Python with correlation ID
      try {
        py.send(ws, userText);
      } catch (err) {
        ws.send(JSON.stringify({
          v: 1, type: "system.message", role: "system",
          payload: { text: `Interpreter error: ${err.message}` }
        }));
      }
    } else {
      ws.send(JSON.stringify({ v: 1, type: "system.message", role: "system", payload: { text: "Unsupported message type" } }));
    }
  });

  ws.on("close", () => {
    // Clean up any pending requests for this socket
    py.dropWs(ws);
  });
});

const PORT = process.env.PORT ? Number(process.env.PORT) : 4000;
httpServer.listen(PORT, () => {
  console.log(`[gateway] listening on http://localhost:${PORT} (WS path: /ws)`);
});
