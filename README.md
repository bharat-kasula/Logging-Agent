# Matilda-Log

**Team Bot / Log Analysis Agent** — a local Gradio chat UI that answers natural-language questions about customer issues by querying **Loki** (or demo sample logs) and returning a structured **Root Cause Analysis (RCA)**.

| Piece | Where it runs |
|--------|----------------|
| Gradio UI + agent | Your laptop |
| **Ollama** LLM | Your laptop (`localhost:11434`) |
| **Loki** | Company servers (configurable URL) |

---

## Features

- Chat questions like *“What is the issue on Customer A?”*
- LangChain tool-calling agent with a **LogQL / Loki tool**
- Structured RCA: Symptom · Evidence · Root Cause · Next Steps · Confidence
- **Demo Simulation Mode**: load success logs, convert to failure, analyze without Loki
- Editable sample JSON under `data/`
- Manual LogQL debug panel
- Clear chat, example prompts, env-based config

---

## Project layout

```
Matilda-Log/
├── app.py                 # Entry point
├── config.py              # Settings from env / .env
├── requirements.txt
├── .env.example
├── README.md
├── agent/
│   ├── agent.py           # Ollama + tool loop
│   └── rca.py             # System prompt + offline fallback
├── tools/
│   └── loki_tool.py       # LogQL client + formatters
├── simulation/
│   └── simulation.py      # Success/failure demo store
├── ui/
│   └── chat_ui.py         # Gradio interface
└── data/
    ├── success_logs.json
    ├── failure_logs.json
    └── success_logs_customerb.json
```

---

## Prerequisites

- **Python 3.11+**
- **Ollama** installed locally: https://ollama.com
- Network access to company **Loki** (for Normal Mode only)

---

## 1. Install dependencies

```bash
cd Matilda-Log

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env               # then edit .env
```

---

## 2. Run Ollama (local LLM)

```bash
# Terminal A — if not already running as a service
ollama serve

# Terminal B — pull a model (once)
ollama pull llama3.1
# or: ollama pull llama3.2 / mistral / qwen2.5
```

Set in `.env` if you use another model:

```env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1
```

---

## 3. Configure Loki (Normal Mode)

Edit `.env`:

```env
LOKI_URL=https://loki.your-company.com
LOKI_TOKEN=your-bearer-token
# or:
# LOKI_USERNAME=...
# LOKI_PASSWORD=...
LOKI_ORG_ID=your-tenant          # if multi-tenant Grafana
DEFAULT_CUSTOMER_LABEL=customer
LOKI_STREAM_SELECTOR={job=~".+"}
```

**Tip:** Keep `LOKI_STREAM_SELECTOR` as a valid LogQL stream selector. The agent adds `customer="…"` automatically.

---

## 4. Start the Gradio app

```bash
source .venv/bin/activate
python app.py
```

Open: **http://127.0.0.1:7860**

---

## 5. Demo simulation (no Loki required)

Ideal for sales / exec demos.

| UI control | Effect |
|------------|--------|
| **Mode → success** | Agent reads `data/success_logs.json` |
| **Mode → failure** | Agent reads `data/failure_logs.json` |
| **Load success logs** | Reload success file + switch to success |
| **Load failure logs** | Reload failure file + switch to failure |
| **Convert success → failure** | Rewrite success lines (503, timeouts, FATAL) and set mode=failure |
| **Reload samples from disk** | After you edit JSON files |
| **Mode → off** | Real Loki queries |

### Suggested live demo script

1. Start app + Ollama (`llama3.1` pulled).
2. Set mode **failure** (or Load failure logs).
3. Ask: *“Root cause of the installation failure on CustomerA?”*
4. Show structured RCA (timeout / 503 / artifact registry).
5. Optionally: Load **success** → **Convert success → failure** → ask again.
6. Edit `data/failure_logs.json` → Reload samples → re-ask.

### Editing samples

JSON shape:

```json
{
  "customer": "CustomerA",
  "scenario": "installation_failure_timeout",
  "logs": [
    {
      "timestamp": "2026-08-01T10:00:50Z",
      "level": "ERROR",
      "service": "installer",
      "message": "Package download failed... timeout",
      "labels": { "customer": "CustomerA", "job": "installer" }
    }
  ]
}
```

---

## Example questions

- What is the issue on Customer A?
- Show me errors for Customer B in the last 2 hours
- Root cause of the installation failure on CustomerX
- Any timeouts for CustomerA in the last 6 hours?

---

## Manual LogQL (debug)

In the right panel, paste a full LogQL query and click **Run query (no LLM)** to verify Loki connectivity without the agent.

Example:

```logql
{job=~".+", customer="CustomerA"} |= "error"
```

---

## Configuration reference

| Variable | Default | Purpose |
|----------|---------|---------|
| `LOKI_URL` | `http://localhost:3100` | Loki base URL |
| `LOKI_USERNAME` / `LOKI_PASSWORD` | empty | Basic auth |
| `LOKI_TOKEN` | empty | Bearer token (preferred if set) |
| `LOKI_ORG_ID` | empty | `X-Scope-OrgID` header |
| `DEFAULT_CUSTOMER_LABEL` | `customer` | Label key for customer filter |
| `LOKI_STREAM_SELECTOR` | `{job=~".+"}` | Base stream selector |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama |
| `OLLAMA_MODEL` | `llama3.1` | Model name |
| `DEFAULT_LOOKBACK` | `1h` | Default time window |
| `MAX_LOG_LINES_TO_LLM` | `80` | Cap context size |
| `GRADIO_SERVER_PORT` | `7860` | UI port |
| `DEBUG` | `true` | Verbose agent/Loki logging |

---

## Architecture (runtime)

```
User (Gradio chat)
       │
       ▼
 Matilda agent (LangChain + Ollama tool calling)
       │
       ├─ mode=off ──► LokiClient ──► company Loki (LogQL)
       │
       └─ mode=success|failure ──► SimulationStore (JSON samples)
       │
       ▼
 Structured RCA markdown
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Agent errors / empty answers | `ollama list` — is model pulled? `curl localhost:11434` |
| Loki connection error | Check VPN, `LOKI_URL`, token/org ID; try Manual LogQL |
| Simulation ignored | Ensure Mode is **success** or **failure**, not **off** |
| Gradio port in use | `GRADIO_SERVER_PORT=7861 python app.py` |
| Tool calling weak | Try a larger model (`llama3.1:8b`, `qwen2.5:14b`) |

Offline fallback: if Ollama fails mid-run, Matilda still queries logs once and builds a **heuristic RCA** so demos don’t fully die.

---

## License / notes

Internal demo tool. Simulation is temporary for presentations and can be removed when automated detection is online.
