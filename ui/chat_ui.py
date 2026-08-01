"""
Gradio chat interface — clean demo chat for Matilda.
"""

from __future__ import annotations

import logging

import gradio as gr

from agent.agent import get_agent
from agent.rca import sanitize_answer
from simulation.simulation import get_simulation_store

logger = logging.getLogger("matilda.ui")

EXAMPLE_QUESTIONS = [
    "Root cause of the installation failure on Amerihealth?",
    "Did Siemens installation complete successfully?",
    "Show me the failed customer list",
    "What is k3s-selinux and how do I fix it?",
]

# Minimal CSS: hide Gradio chrome that looks like debug UI
_CSS = """
.contain { max-width: 860px !important; margin: 0 auto; padding: 0 12px; }
footer, .footer, #footer { display: none !important; }
/* Hide API / settings / flag chrome when present */
button.svelte-1ipelgc, .icon-button-wrapper { opacity: 0.35; }
.gradio-container { min-height: 100vh !important; }
#chatbot { border-radius: 12px; }
.header-sub { color: #64748b; font-size: 0.95rem; margin-top: -0.4rem; }
.status-line { color: #64748b; font-size: 0.85rem; }
"""


def _history_to_messages(history: list) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for item in history or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            u, a = item[0], item[1]
            if u:
                messages.append({"role": "user", "content": str(u)})
            if a:
                messages.append({"role": "assistant", "content": str(a)})
    return messages


def respond(message: str, history: list) -> tuple[str, list]:
    message = (message or "").strip()
    if not message:
        return "", history or []

    history = list(history or [])
    prior = _history_to_messages(history)
    logger.info("User: %s", message)
    try:
        answer = get_agent().run(message, chat_history=prior)
    except Exception as exc:
        logger.exception("Chat respond failed")
        # Never show stack traces / connection errors in the bubble
        answer = (
            "Sorry — I could not analyze the logs just now. "
            "Please try again, or switch the demo log set below."
        )
        logger.debug("suppressed error: %s", exc)

    answer = sanitize_answer(answer or "")
    history.append([message, answer])
    return "", history


def clear_chat() -> tuple[list, str]:
    return [], ""


def set_mode(mode: str) -> str:
    msg = get_simulation_store().set_mode(mode)
    return _status_md()


def load_success() -> str:
    get_simulation_store().load_success()
    return _status_md()


def load_failure() -> str:
    get_simulation_store().load_failure()
    return _status_md()


def _status_md() -> str:
    from agent.agent import _ollama_reachable

    store = get_simulation_store()
    brain = "Ollama online" if _ollama_reachable() else "local rules (Ollama offline)"
    if store.mode == "success":
        logs = "Siemens · `data/success_install.log`"
    else:
        logs = "Amerihealth · `data/failed_log.txt`"
    return f"Logs: **{logs}** · Chat: **{brain}**"


def build_ui() -> gr.Blocks:
    store = get_simulation_store()
    theme = gr.themes.Soft(primary_hue="indigo", secondary_hue="slate")

    with gr.Blocks(title="Matilda Log Assistant", theme=theme, css=_CSS) as demo:
        with gr.Column(elem_classes=["contain"]):
            gr.Markdown("# Matilda Log Assistant")
            gr.Markdown(
                '<p class="header-sub">Chat about customer install issues. '
                "Answers come from local demo logs today — live Loki later.</p>",
            )

            chatbot = gr.Chatbot(
                elem_id="chatbot",
                label="",
                height=540,
                show_copy_button=True,
                bubble_full_width=False,
                show_label=False,
                avatar_images=(None, None),
            )
            with gr.Row():
                msg = gr.Textbox(
                    show_label=False,
                    placeholder="e.g. Root cause of the installation failure on Amerihealth?",
                    scale=5,
                    lines=2,
                    container=False,
                )
                send_btn = gr.Button("Send", variant="primary", scale=1)
            with gr.Row():
                clear_btn = gr.Button("Clear chat", size="sm")
                status = gr.Markdown(value=_status_md(), elem_classes=["status-line"])

            gr.Examples(
                examples=EXAMPLE_QUESTIONS,
                inputs=msg,
                label="Try asking",
            )

            with gr.Accordion("Switch demo customer", open=False):
                gr.Markdown(
                    "Pick which local install log Matilda should read "
                    "(until real Loki is connected)."
                )
                mode = gr.Radio(
                    choices=[
                        ("Amerihealth — failed install", "failure"),
                        ("Siemens — successful install", "success"),
                    ],
                    value="failure" if store.mode != "success" else "success",
                    label="Customer log set",
                    show_label=True,
                )
                with gr.Row():
                    btn_fail = gr.Button("Amerihealth (failure)", size="sm")
                    btn_ok = gr.Button("Siemens (success)", size="sm")

        send_btn.click(respond, inputs=[msg, chatbot], outputs=[msg, chatbot])
        msg.submit(respond, inputs=[msg, chatbot], outputs=[msg, chatbot])
        clear_btn.click(clear_chat, outputs=[chatbot, msg])

        mode.change(set_mode, inputs=mode, outputs=status)
        btn_fail.click(load_failure, outputs=status)
        btn_ok.click(load_success, outputs=status)

    return demo
