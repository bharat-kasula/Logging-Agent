#!/usr/bin/env python3
"""
Matilda-Log — Team Bot / Log Analysis Agent

Entry point: starts the Gradio UI.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path when run as `python app.py`
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import settings  # noqa: E402


def _patch_gradio_api_info() -> None:
    """
    Gradio 4.44 + some pydantic versions crash in get_api_info() with
    TypeError on bool schemas. Safe empty API info keeps local UI working.
    """
    try:
        import gradio as gr

        def _safe_api_info(self):  # type: ignore[no-untyped-def]
            return {"named_endpoints": {}, "unnamed_endpoints": {}}

        gr.Blocks.get_api_info = _safe_api_info  # type: ignore[method-assign]
    except Exception:
        pass


def setup_logging() -> None:
    level = logging.DEBUG if settings.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quieter HTTP noise unless debugging
    if not settings.debug:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> None:
    setup_logging()
    _patch_gradio_api_info()
    # Import UI after Gradio patch
    from ui.chat_ui import build_ui  # noqa: WPS433

    log = logging.getLogger("matilda")
    log.info("Starting Matilda-Log")
    log.info("Ollama: %s model=%s", settings.ollama_base_url, settings.ollama_model)
    log.info("Loki:   %s", settings.loki_url)
    log.info(
        "UI:     http://%s:%s",
        settings.gradio_server_name,
        settings.gradio_server_port,
    )
    log.info(
        "Demo files: Siemens success + Amerihealth failure under data/"
    )

    demo = build_ui()
    demo.queue().launch(
        server_name=settings.gradio_server_name,
        server_port=settings.gradio_server_port,
        share=settings.gradio_share,
        # Keep stack traces out of the browser for demos
        show_error=False,
        inbrowser=False,
        show_api=False,
    )


if __name__ == "__main__":
    main()
