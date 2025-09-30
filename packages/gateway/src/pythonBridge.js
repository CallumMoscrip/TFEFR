// packages/gateway/src/pythonBridge.js
// TFEFR ↔ Python interpreter bridge with message-ID correlation.
//
// Protocol:
//  - Node → Python (stdin):  "MSGID:<uuid>|<user text>\n"
//  - Python → Node (stdout): "[OUT][<uuid>] <assistant text>"
//                            "[PLAN][<uuid>] <planner summary>"
// Multiple concurrent WebSockets are supported; each send is correlated by <uuid>.

import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { randomUUID } from "node:crypto";
import { EventEmitter } from "node:events";

const OUT_RE = /^\[(OUT|PLAN)\]\[(?<id>[^\]]+)\]\s?(?<rest>.*)$/;

export class PythonBridge extends EventEmitter {
  /**
   * @param {{ cmd: string, args?: string[], env?: Record<string,string>, responseTimeoutMs?: number }} opts
   */
  constructor({ cmd, args = [], env = {}, responseTimeoutMs = 8000 }) {
    super();
    this.cmd = cmd;
    this.args = args;
    this.env = { ...process.env, ...env };
    this.proc = null;
    this.rl = null;
    this.responseTimeoutMs = responseTimeoutMs;

    /** @type {Map<string, {ws: any, timer: NodeJS.Timeout|null}>} */
    this.pending = new Map(); // msgId -> { ws, timer }
  }

  start() {
    if (this.proc) return;

    this.proc = spawn(this.cmd, this.args, { stdio: ["pipe", "pipe", "pipe"], env: this.env });

    this.proc.on("spawn", () => this.emit("status", { state: "spawned", pid: this.proc.pid }));
    this.proc.on("exit", (code, signal) => {
      this.emit("status", { state: "exit", code, signal });
      this._cleanup();
    });
    this.proc.on("error", (err) => this.emit("status", { state: "error", error: err.message }));

    this.rl = createInterface({ input: this.proc.stdout });
    this.rl.on("line", (line) => this._onStdout(line));

    const rlErr = createInterface({ input: this.proc.stderr });
    rlErr.on("line", (line) => this.emit("stderr", line));
  }

  stop() {
    if (!this.proc) return;
    this.proc.kill("SIGTERM");
  }

  /**
   * Send a user utterance associated with a specific websocket/client.
   * @param {any} ws - the WebSocket instance to route responses back to
   * @param {string} text
   * @returns {string} msgId
   */
  send(ws, text) {
    if (!this.proc) throw new Error("Python process not started");
    const msgId = randomUUID();
    const payload = `MSGID:${msgId}|${(text ?? "").toString().trim()}\n`;

    // Track pending request for timeout/cleanup
    if (this.pending.has(msgId)) {
      throw new Error("Duplicate msgId (unexpected)");
    }
    const timer = setTimeout(() => {
      const entry = this.pending.get(msgId);
      if (entry?.ws && entry.ws.readyState === 1) {
        entry.ws.send(JSON.stringify({
          v: 1, type: "system.message", role: "system",
          payload: { text: "Interpreter timeout (no response)" }
        }));
      }
      this.pending.delete(msgId);
    }, this.responseTimeoutMs);

    this.pending.set(msgId, { ws, timer });

    // Write to python
    this.proc.stdin.write(payload);
    return msgId;
  }

  /**
   * Immediately remove all pending entries for a given WebSocket.
   * Call this on ws 'close'.
   */
  dropWs(ws) {
    for (const [id, entry] of this.pending.entries()) {
      if (entry.ws === ws) {
        if (entry.timer) clearTimeout(entry.timer);
        this.pending.delete(id);
      }
    }
  }

  _onStdout(line) {
    const m = line.match(OUT_RE);
    if (!m || !m.groups) {
      // Unstructured line; broadcast to admin logs
      this.emit("stderr", `[unparsed] ${line}`);
      return;
    }
    const kind = m[1]; // OUT or PLAN
    const id = m.groups.id;
    const text = (m.groups.rest || "").trim();

    const entry = this.pending.get(id);
    if (!entry) {
      // Late/dangling output; log and ignore
      this.emit("stderr", `[dangling ${kind}] ${id}: ${text}`);
      return;
    }

    // Route to the correct WebSocket
    const { ws, timer } = entry;
    if (ws && ws.readyState === 1) {
      if (kind === "OUT") {
        ws.send(JSON.stringify({
          v: 1, type: "system.message", role: "assistant", payload: { text }
        }));
        // Consider the request complete on first OUT
        if (timer) clearTimeout(timer);
        this.pending.delete(id);
      } else if (kind === "PLAN") {
        ws.send(JSON.stringify({
          v: 1, type: "system.update", payload: { plan: text, msgId: id }
        }));
        // Keep pending until OUT arrives (or timeout)
      }
    } else {
      if (timer) clearTimeout(timer);
      this.pending.delete(id);
    }
  }

  _cleanup() {
    try { this.rl?.close?.(); } catch {}
    this.rl = null;
    if (this.proc) {
      try { this.proc.removeAllListeners(); } catch {}
    }
    this.proc = null;
    for (const [, entry] of this.pending) {
      if (entry.timer) clearTimeout(entry.timer);
    }
    this.pending.clear();
  }
}
