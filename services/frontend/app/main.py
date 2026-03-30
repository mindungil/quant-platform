from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="frontend", version="0.1.0")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Quant Command Deck</title>
  <style>
    :root {
      --bg: radial-gradient(circle at top left, #203a43, #0b1220 45%, #041017 100%);
      --card: rgba(255,255,255,0.07);
      --line: rgba(255,255,255,0.14);
      --text: #eef7fb;
      --accent: #f6bd60;
      --accent-2: #84dcc6;
      --warn: #f28482;
      --font: "IBM Plex Sans", "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: var(--font);
      display: grid;
      place-items: center;
    }
    main {
      width: min(1100px, calc(100vw - 32px));
      display: grid;
      gap: 16px;
      padding: 24px;
    }
    .hero, .panel {
      border: 1px solid var(--line);
      background: var(--card);
      backdrop-filter: blur(14px);
      border-radius: 24px;
      padding: 24px;
    }
    .hero h1 {
      margin: 0 0 8px;
      font-size: clamp(2rem, 6vw, 4rem);
      letter-spacing: -0.05em;
    }
    .hero p { margin: 0; max-width: 60ch; line-height: 1.5; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }
    .grid-wide { display: grid; grid-template-columns: 1.4fr 1fr; gap: 16px; }
    .metric { font-size: 2rem; color: var(--accent); margin: 12px 0 4px; }
    .tag { display: inline-block; padding: 6px 10px; border-radius: 999px; background: rgba(109,247,193,0.15); color: var(--accent-2); }
    .muted { opacity: 0.75; }
    .log {
      margin-top: 12px;
      font-family: "IBM Plex Mono", monospace;
      font-size: 0.9rem;
      white-space: pre-wrap;
      background: rgba(0,0,0,0.2);
      padding: 14px;
      border-radius: 16px;
      min-height: 120px;
    }
    .status-ok { color: var(--accent-2); }
    .status-warn { color: var(--warn); }
    input {
      width: 100%;
      background: rgba(255,255,255,0.08);
      border: 1px solid var(--line);
      color: var(--text);
      border-radius: 12px;
      padding: 12px 14px;
      font: inherit;
      margin-top: 10px;
    }
    button {
      margin-top: 10px;
      background: var(--accent);
      color: #07131a;
      font: inherit;
      font-weight: 700;
      border: 0;
      border-radius: 12px;
      padding: 12px 14px;
      cursor: pointer;
    }
    @media (max-width: 900px) {
      .grid-wide { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <span class="tag">Autonomous Trading Platform</span>
      <h1>Quant Command Deck</h1>
      <p>Gateway-authenticated dashboard surface for the local quant platform. Enter a JWT, fetch the aggregated dashboard summary, and stream live events over the public WebSocket bridge.</p>
    </section>
    <section class="grid">
      <article class="panel"><h2>Signal Loop</h2><div class="metric">Live</div><p>Feature driven scoring with threshold events.</p></article>
      <article class="panel"><h2>Agents</h2><div class="metric">3 + 2</div><p>Crypto, ETF, stock, auth, and reasoning context services wired in.</p></article>
      <article class="panel"><h2>Execution</h2><div class="metric">Guarded</div><p>Risk gate, credential store, exchange adapter, and user-aware gateway surface.</p></article>
      <article class="panel"><h2>Product</h2><div class="metric">Streaming</div><p>Gateway summary, authenticated dashboard route, and WebSocket bridge are present.</p></article>
    </section>
    <section class="grid-wide">
      <article class="panel">
        <h2>Gateway Session</h2>
        <p class="muted">Paste a JWT from <code>auth-service</code>, then fetch the dashboard and open the public WebSocket bridge.</p>
        <input id="token" placeholder="Bearer token without the Bearer prefix" />
        <button onclick="loadDashboard()">Load Dashboard</button>
        <button onclick="connectStream()">Connect Stream</button>
        <div id="status" class="log">status: idle</div>
      </article>
      <article class="panel">
        <h2>Live Feed</h2>
        <p class="muted">WebSocket snapshots arrive every 2 seconds from the gateway bridge.</p>
        <div id="feed" class="log">waiting for stream...</div>
      </article>
    </section>
  </main>
  <script>
    async function loadDashboard() {
      const token = document.getElementById("token").value.trim();
      const status = document.getElementById("status");
      if (!token) {
        status.textContent = "status: missing token";
        return;
      }
      const gatewayBase = `${location.protocol}//${location.hostname}:8017`;
      const response = await fetch(`${gatewayBase}/dashboard`, {
        headers: { "Authorization": `Bearer ${token}` }
      }).catch((error) => ({ ok: false, text: async () => error.message }));
      const text = await response.text();
      status.textContent = text;
    }

    function connectStream() {
      const token = document.getElementById("token").value.trim();
      const feed = document.getElementById("feed");
      if (!token) {
        feed.textContent = "missing token";
        return;
      }
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(`${protocol}://${location.hostname}:8017/ws?token=${encodeURIComponent(token)}`);
      socket.onmessage = (event) => { feed.textContent = event.data; };
      socket.onopen = () => { feed.textContent = "connected"; };
      socket.onclose = () => { feed.textContent += "\\nclosed"; };
    }
  </script>
</body>
</html>
"""
