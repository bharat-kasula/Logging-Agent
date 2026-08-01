"""
Matilda-Log configuration.

Load settings from environment variables and optional .env file.
All sensitive / environment-specific values live here — never hard-code secrets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Project root (directory containing this file)
ROOT_DIR = Path(__file__).resolve().parent

# Load .env from project root if present
load_dotenv(ROOT_DIR / ".env")


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Application settings (immutable snapshot)."""

    # --- Loki ---
    loki_url: str
    loki_username: str
    loki_password: str
    loki_token: str
    loki_org_id: str
    # Label used for customer filtering in LogQL, e.g. customer="Acme"
    default_customer_label: str
    # Optional fixed LogQL stream selector prefix (job, app, etc.)
    loki_stream_selector: str
    loki_query_limit: int
    loki_timeout_seconds: int

    # --- Ollama / LLM ---
    ollama_base_url: str
    ollama_model: str
    ollama_temperature: float

    # --- Agent ---
    default_lookback: str  # e.g. "1h", "2h", "6h"
    max_log_lines_to_llm: int

    # --- Gradio ---
    gradio_server_name: str
    gradio_server_port: int
    gradio_share: bool

    # --- Paths ---
    data_dir: Path
    success_logs_path: Path
    failure_logs_path: Path

    # --- Simulation / local files ---
    # When Loki is not available, default to local data/ logs (failure | success | off)
    simulation_default_mode: str
    # If true, never call Loki — only data/ samples
    local_logs_only: bool
    # If false, skip Ollama and answer from local log analysis (best for demos without LLM)
    use_ollama: bool

    # --- Debug ---
    debug: bool


def load_settings() -> Settings:
    """Build Settings from environment variables."""
    data_dir = Path(os.getenv("DATA_DIR", str(ROOT_DIR / "data")))
    return Settings(
        loki_url=os.getenv("LOKI_URL", "http://localhost:3100").rstrip("/"),
        loki_username=os.getenv("LOKI_USERNAME", ""),
        loki_password=os.getenv("LOKI_PASSWORD", ""),
        loki_token=os.getenv("LOKI_TOKEN", ""),
        loki_org_id=os.getenv("LOKI_ORG_ID", ""),
        default_customer_label=os.getenv("DEFAULT_CUSTOMER_LABEL", "customer"),
        loki_stream_selector=os.getenv(
            "LOKI_STREAM_SELECTOR", '{job=~".+"}'
        ),
        loki_query_limit=_int(os.getenv("LOKI_QUERY_LIMIT"), 100),
        loki_timeout_seconds=_int(os.getenv("LOKI_TIMEOUT_SECONDS"), 30),
        ollama_base_url=os.getenv(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        ).rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3.1"),
        ollama_temperature=float(os.getenv("OLLAMA_TEMPERATURE", "0.2")),
        default_lookback=os.getenv("DEFAULT_LOOKBACK", "1h"),
        max_log_lines_to_llm=_int(os.getenv("MAX_LOG_LINES_TO_LLM"), 80),
        gradio_server_name=os.getenv("GRADIO_SERVER_NAME", "127.0.0.1"),
        gradio_server_port=_int(os.getenv("GRADIO_SERVER_PORT"), 7860),
        gradio_share=_bool(os.getenv("GRADIO_SHARE"), False),
        data_dir=data_dir,
        # Prefer real installer dumps if present (see data/success_install.log, failed_log.txt)
        success_logs_path=Path(
            os.getenv(
                "SUCCESS_LOGS_PATH",
                str(
                    data_dir / "success_install.log"
                    if (data_dir / "success_install.log").exists()
                    else data_dir / "success_logs.json"
                ),
            )
        ),
        failure_logs_path=Path(
            os.getenv(
                "FAILURE_LOGS_PATH",
                str(
                    data_dir / "failed_log.txt"
                    if (data_dir / "failed_log.txt").exists()
                    else data_dir / "failure_logs.json"
                ),
            )
        ),
        # Default: use local failure logs for demos until Loki is wired
        simulation_default_mode=os.getenv(
            "SIMULATION_DEFAULT_MODE", "failure"
        ).strip().lower(),
        local_logs_only=_bool(os.getenv("LOCAL_LOGS_ONLY"), True),
        # Default true so the agent auto-uses Ollama when the API is reachable
        use_ollama=_bool(os.getenv("USE_OLLAMA"), True),
        debug=_bool(os.getenv("DEBUG"), False),
    )


# Singleton for simple imports: `from config import settings`
settings = load_settings()
