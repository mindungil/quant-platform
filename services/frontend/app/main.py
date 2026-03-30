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
      --bg: linear-gradient(135deg, #08111f, #102944 60%, #113d3d);
      --card: rgba(255,255,255,0.08);
      --line: rgba(255,255,255,0.18);
      --text: #edf5ff;
      --accent: #ffcc66;
      --accent-2: #6df7c1;
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
    .metric { font-size: 2rem; color: var(--accent); margin: 12px 0 4px; }
    .tag { display: inline-block; padding: 6px 10px; border-radius: 999px; background: rgba(109,247,193,0.15); color: var(--accent-2); }
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <span class="tag">Autonomous Trading Platform</span>
      <h1>Quant Command Deck</h1>
      <p>Phase 1 through Phase 5 repository scaffold is live. Market data, signal generation, strategy selection, execution safety, orchestration, and dashboard surfaces are all wired into the local platform.</p>
    </section>
    <section class="grid">
      <article class="panel"><h2>Signal Loop</h2><div class="metric">Live</div><p>Feature driven scoring with threshold events.</p></article>
      <article class="panel"><h2>Agents</h2><div class="metric">3 + 1</div><p>Crypto active, ETF and stock gated by market hours, orchestrator supervising.</p></article>
      <article class="panel"><h2>Execution</h2><div class="metric">Safe</div><p>Risk gate, credential store, exchange adapter, order path.</p></article>
      <article class="panel"><h2>Product</h2><div class="metric">Ready</div><p>Gateway summary and dashboard scaffold in place.</p></article>
    </section>
  </main>
</body>
</html>
"""
