import { useEffect, useRef, useState } from "react";
import "./styles.css";

/**
 * Minimal, production-friendly WebSocket client for chat/events.
 * - Manages connection lifecycle and simple reconnect
 * - Sends 'user.message' and displays 'system.message'
 * - Future: replace with a dedicated service and add schema validation (zod)
 */
export default function App() {
  const [status, setStatus] = useState("disconnected"); // disconnected | connecting | connected
  const [messages, setMessages] = useState([]); // { role: 'user'|'assistant'|'system', text: string }
  const [input, setInput] = useState("");
  const wsRef = useRef(null);
  const url = import.meta.env.VITE_GATEWAY_WS_URL || "ws://localhost:4000/ws";
  const reconnectTimer = useRef(null);

  useEffect(() => {
    connect();
    return () => {
      if (wsRef.current) wsRef.current.close(1000, "page unload");
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const connect = () => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;
    setStatus("connecting");
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("connected");
      setMessages((cur) => [
        ...cur,
        { role: "system", text: "Connected to gateway." }
      ]);
    };

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        // Expecting 'system.message' for assistant/system lines; expand as your protocol grows
        if (msg?.type === "system.message" && msg?.payload?.text) {
          setMessages((cur) => [
            ...cur,
            { role: msg.role || "assistant", text: msg.payload.text }
          ]);
        } else if (msg?.type === "system.update") {
          // Placeholder: Show interpreter/robot updates as system info
          setMessages((cur) => [
            ...cur,
            { role: "system", text: `[update] ${JSON.stringify(msg.payload)}` }
          ]);
        }
      } catch (err) {
        setMessages((cur) => [
          ...cur,
          { role: "system", text: `Malformed message: ${evt.data}` }
        ]);
      }
    };

    ws.onclose = () => {
      setStatus("disconnected");
      setMessages((cur) => [
        ...cur,
        { role: "system", text: "Disconnected. Reconnecting in 2s..." }
      ]);
      reconnectTimer.current = setTimeout(connect, 2000);
    };

    ws.onerror = () => {
      // Let the onclose handler handle reconnect
    };
  };

  const sendMessage = () => {
    const text = input.trim();
    if (!text || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    // Minimal protocol message – version & correlation can be added later
    const outgoing = {
      v: 1,
      type: "user.message",
      payload: { text }
    };
    wsRef.current.send(JSON.stringify(outgoing));
    setMessages((cur) => [...cur, { role: "user", text }]);
    setInput("");
  };

  return (
    <div className="app-root">
      <header>
        <h1>AI2-THOR Control (MVP)</h1>
        <div className={`status ${status}`}>{status}</div>
      </header>

      <main>
        {/* Video placeholder – wire WebRTC here later */}
        <section className="video-pane">
          <div className="video-placeholder">Video stream (coming soon)</div>
        </section>

        <section className="chat-pane">
          <div className="messages" aria-live="polite">
            {messages.map((m, i) => (
              <div key={i} className={`msg ${m.role}`}>
                <strong>{m.role}:</strong> <span>{m.text}</span>
              </div>
            ))}
          </div>

          <div className="composer">
            <input
              type="text"
              placeholder="Type a command for the robot…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && sendMessage()}
              aria-label="Chat input"
            />
            <button onClick={sendMessage} disabled={status !== "connected"}>
              Send
            </button>
          </div>
        </section>
      </main>
    </div>
  );
}
