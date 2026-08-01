"""
Matilda agent: chat + local log analysis (+ Ollama when available).

- LOCAL_LOGS_ONLY → never call Loki; read data/ files for log queries
- USE_OLLAMA=true + Ollama up → natural chat with optional query_logs tool
- No Ollama → structured RCA for install/log questions; friendly chat otherwise
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from config import settings
from simulation.simulation import get_simulation_store
from agent.rca import SYSTEM_PROMPT, analyze_logs, extract_customer, sanitize_answer

logger = logging.getLogger("matilda.agent")

# Broader system prompt when LLM is available
CHAT_SYSTEM_PROMPT = """You are **Matilda**, a helpful support engineer chat assistant.

Answer general questions naturally and briefly (markdown is fine).
You may also discuss Matilda installs, k3s, SELinux, and troubleshooting.

HARD FACTS about demo customers (never contradict these):
- **Amerihealth** = FAILED install (missing k3s-selinux package → FATAL k3s install)
- **Siemens** = SUCCESSFUL install (reached INSTALLATION COMPLETE)
- When asked for a **failed customer list**, include ONLY Amerihealth — never list Siemens as failed.
- When asked for successful customers, include Siemens.

Rules:
- Be concise and friendly.
- Do not invent customers or log lines.
- Never mention connection refused, Ollama internals, simulation mode, or tool dumps.
- For non-install topics (coffee, weather, etc.) just answer normally — do not force an RCA template.
"""


def _query_logs_impl(
    customer: str = "",
    lookback: str = "1h",
    filter_text: str = "",
    logql_override: str = "",
    limit: int = 0,
) -> str:
    """Local data/ files when LOCAL_LOGS_ONLY; otherwise Loki with local fallback."""
    from agent.agent_tools import query_logs_routed

    return query_logs_routed(
        customer=customer,
        lookback=lookback,
        filter_text=filter_text,
        logql_override=logql_override,
        limit=limit,
    )


def _ollama_reachable() -> bool:
    """True only when USE_OLLAMA is enabled and the Ollama HTTP API responds."""
    if not getattr(settings, "use_ollama", False):
        return False
    try:
        import httpx

        r = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=1.5)
        return r.status_code == 200
    except Exception:
        return False


def is_inventory_question(question: str) -> bool:
    """True when user wants a list of customers / failed / successful installs."""
    q = (question or "").lower().strip()
    if not q:
        return False
    patterns = (
        r"\bfailed customers?\b",
        r"\bfailure list\b",
        r"\blist of (failed|failing) customers?\b",
        r"\bcustomers? (that |which )?(failed|failing)\b",
        r"\bwhich customers? (failed|failing)\b",
        r"\bshow (me )?(all )?failed\b",
        r"\bfailed customer list\b",
        r"\bsuccess(ful)? customers?\b",
        r"\bcustomers? (with )?(successful|success) install",
        r"\blist (all )?customers?\b",
        r"\bcustomer list\b",
        r"\ball customers?\b",
        r"\bwho failed\b",
        r"\bwhich installs? failed\b",
    )
    return any(re.search(p, q) for p in patterns)


def answer_customer_inventory(question: str) -> str:
    """
    Grounded inventory from demo data/ files only.
    Amerihealth = failure, Siemens = success. Never mix those up.
    """
    q = (question or "").lower()
    want_failed = any(
        x in q for x in ("fail", "failed", "failure", "broken", "unsuccessful")
    )
    want_success = any(
        x in q
        for x in (
            "success",
            "successful",
            "succeeded",
            "completed successfully",
            "passed",
        )
    )

    failed_block = (
        "### Failed installs\n"
        "1. **Amerihealth**\n"
        "   - **Status:** Failed during k3s master setup\n"
        "   - **Root cause:** Missing `k3s-selinux` package (package match failure on OL 10.1 → FATAL k3s install)\n"
        "   - **Fix:** Install matching `container-selinux` / `k3s-selinux` for this OS, "
        "or approve SELinux permissive, then re-run install\n"
    )
    success_block = (
        "### Successful installs\n"
        "1. **Siemens**\n"
        "   - **Status:** `INSTALLATION COMPLETE`\n"
        "   - **Note:** SELinux package warnings appeared earlier but were **non-fatal**; "
        "install finished successfully\n"
    )

    # Pure failed list (e.g. "failed customer list") — Siemens is NOT failed
    if want_failed and not want_success:
        return (
            "**Failed customer list** (from current demo logs)\n\n"
            f"{failed_block}\n"
            "**Not on this list:** **Siemens** — install completed successfully "
            "(SELinux warnings only, non-fatal).\n\n"
            "_Only **1** failed customer in demo data. Ask "
            "“Root cause of the installation failure on Amerihealth?” for full RCA._"
        )

    if want_success and not want_failed:
        return (
            "**Successful customer list** (from current demo logs)\n\n"
            f"{success_block}\n"
            "_Ask “Did Siemens installation complete successfully?” for details._"
        )

    return (
        "**Customer install inventory** (demo logs under `data/`)\n\n"
        f"{failed_block}\n"
        f"{success_block}\n"
        "**Summary:** 1 failed (**Amerihealth**), 1 successful (**Siemens**).\n\n"
        "Ask for a customer name to get full root-cause analysis."
    )


def is_log_question(question: str) -> bool:
    """True when the user is asking about install logs / customer RCA / status."""
    q = (question or "").lower().strip()
    if not q:
        return False

    # Inventory / list questions are handled separately (not single-customer RCA)
    if is_inventory_question(q):
        return False

    # Explicit demo customers → always treat as log/RCA
    if any(c in q for c in ("amerihealth", "siemens", "customera", "customerb")):
        return True

    # Conceptual / how-to questions without a customer → general chat, not RCA dump
    conceptual = bool(
        re.search(
            r"\b(what is|what's|whats|how (do|to|can)|explain|why does|tell me about)\b",
            q,
        )
    )
    if conceptual and not re.search(
        r"\b(root cause|failed for|failure on|status of|logs? for|customer)\b", q
    ):
        return False

    # Log / incident style questions
    if re.search(
        r"\b(root cause|what failed|why did|status of|rca|troubleshoot|"
        r"installation failed|install failed|install error|from the logs?|"
        r"show (me )?errors?|summarize .*errors?)\b",
        q,
    ):
        return True
    if re.search(r"\b(did|has)\b.*\b(install|installation)\b.*\b(complete|succeed|fail)", q):
        return True
    if re.search(r"\b(error|fatal|failure)\b.*\b(install|customer|log)", q):
        return True
    return False


def _fetch_logs_for_customer(customer: str) -> str:
    logs_err = _query_logs_impl(
        customer=customer,
        lookback=settings.default_lookback,
        filter_text="error",
    )
    logs_all = _query_logs_impl(
        customer=customer,
        lookback="24h",
        filter_text="",
        limit=40,
    )
    logs = logs_err
    if logs_all and logs_all not in logs:
        logs = logs_err + "\n---\n" + logs_all
    if "Lines returned: 0" in logs or "(no log lines" in logs:
        logs = logs_all or logs_err
    return logs


def _general_chat_answer(question: str) -> str:
    """
    Friendly answers when Ollama is off and the question is not a pure log RCA.
    Keeps the demo usable for non-customer questions.
    """
    q = (question or "").lower().strip()

    if any(x in q for x in ("who are you", "what are you", "what is matilda", "help", "what can you")):
        return (
            "I'm **Matilda**, your install/log assistant.\n\n"
            "I can:\n"
            "- Explain **why a customer install failed** (e.g. Amerihealth)\n"
            "- Confirm a **successful install** (e.g. Siemens)\n"
            "- Summarize errors from the local demo logs\n\n"
            "Try asking:\n"
            "- *Root cause of the installation failure on Amerihealth?*\n"
            "- *Did Siemens installation complete successfully?*\n"
            "- *What is k3s-selinux and why does it matter?*"
        )

    if "k3s-selinux" in q or ("selinux" in q and ("fix" in q or "how" in q or "what" in q)):
        return (
            "**k3s-selinux** is the SELinux policy package k3s needs on RHEL/Oracle Linux hosts.\n\n"
            "If the package is missing for the OS version (e.g. OL **10.1**), the installer may:\n"
            "1. Warn that `container-selinux` / `k3s-selinux` is unavailable\n"
            "2. Try SELinux permissive as a fallback\n"
            "3. On some sites still **FATAL** and abort k3s install\n\n"
            "**How to fix**\n"
            "1. Install matching `container-selinux` and `k3s-selinux` RPMs for your distro, **or**\n"
            "2. Approve SELinux permissive for that environment\n"
            "3. Re-run install until you see `INSTALLATION COMPLETE`\n\n"
            "Ask about **Amerihealth** to see a real failure example from the demo logs."
        )

    if "k3s" in q and any(x in q for x in ("what", "how", "why", "explain")):
        return (
            "**k3s** is a lightweight Kubernetes distribution used by the Matilda installer "
            "to bootstrap the cluster (master setup).\n\n"
            "Install can fail if SELinux policy packages are missing, network/registry is blocked, "
            "or prerequisites are not met. I can pull demo logs for **Amerihealth** (failed) or "
            "**Siemens** (success) if you want a concrete RCA."
        )

    if any(x in q for x in ("loki", "how do you get logs", "data source", "where.*logs")):
        return (
            "Today I'm reading **local demo install logs** under `data/`:\n"
            "- `failed_log.txt` → **Amerihealth** (failure)\n"
            "- `success_install.log` → **Siemens** (success)\n\n"
            "When Loki is connected later, the same chat will query live streams instead. "
            "Ask about a customer to run RCA now."
        )

    if any(x in q for x in ("hello", "hi ", "hey", "good morning", "good afternoon")):
        return (
            "Hi — I'm Matilda. Ask me about a customer install "
            "(Amerihealth / Siemens) or a general install question like k3s / SELinux."
        )

    if is_inventory_question(q):
        return answer_customer_inventory(q)

    if any(
        x in q
        for x in (
            "common install",
            "common failure",
            "typical failure",
            "summarize",
            "what usually fails",
        )
    ):
        return (
            "Common Matilda install failure themes from the demo logs:\n\n"
            "1. **Missing k3s-selinux / container-selinux** (seen on **Amerihealth**) — "
            "package not available for the OS (e.g. OL 10.1) → FATAL k3s install.\n"
            "2. **SELinux warnings that are non-fatal** (seen on **Siemens**) — same package "
            "warns, but install still reaches `INSTALLATION COMPLETE`.\n"
            "3. **Noise that is not the root cause** — e.g. `nm-cloud-setup` unit not loaded "
            "(safe to ignore for RCA).\n\n"
            "Ask for a specific customer to get a full RCA with evidence lines."
        )

    # Default: don't dump Amerihealth RCA for unrelated questions
    return (
        "I can help with **install log analysis** and common Matilda install topics "
        "(k3s, SELinux, customer status).\n\n"
        f"I didn't treat this as a log RCA question: *{question.strip()[:160]}*\n\n"
        "Examples that work well:\n"
        "- Root cause of the installation failure on **Amerihealth**?\n"
        "- Did **Siemens** installation complete successfully?\n"
        "- What is k3s-selinux and how do I fix it?\n"
        "- What can you do?"
    )


class MatildaAgent:
    """Chat agent: Ollama when available; smart local fallback otherwise."""

    def __init__(self) -> None:
        self._llm_ready = False
        self._tools_by_name: dict = {}
        self.llm_with_tools = None
        self.llm = None

    def _ensure_llm(self) -> bool:
        if self._llm_ready and self.llm_with_tools is not None:
            return True
        if not _ollama_reachable():
            return False
        try:
            from langchain_core.tools import StructuredTool
            from langchain_ollama import ChatOllama
            from pydantic import BaseModel, Field, field_validator

            def query_logs_tool(
                customer: str = "",
                lookback: str = "1h",
                filter_text: str = "",
                logql_override: str = "",
                limit: int = 0,
            ) -> str:
                # Ollama often sends null for unused optional args
                return _query_logs_impl(
                    customer=customer or "",
                    lookback=lookback or "1h",
                    filter_text=filter_text or "",
                    logql_override=logql_override or "",
                    limit=int(limit or 0),
                )

            class QueryLogsInput(BaseModel):
                customer: Optional[str] = Field(
                    default="", description="Customer id/name e.g. Amerihealth"
                )
                lookback: Optional[str] = Field(
                    default="1h", description="Time window e.g. 1h, 2h, 6h"
                )
                filter_text: Optional[str] = Field(
                    default="", description="Keyword filter e.g. error, fatal"
                )
                logql_override: Optional[str] = Field(
                    default="", description="Full LogQL if provided"
                )
                limit: Optional[int] = Field(default=0, description="Max lines")

                @field_validator("customer", "lookback", "filter_text", "logql_override", mode="before")
                @classmethod
                def _none_str(cls, v: Any) -> str:
                    if v is None:
                        return ""
                    return str(v)

                @field_validator("limit", mode="before")
                @classmethod
                def _none_int(cls, v: Any) -> int:
                    if v is None or v == "":
                        return 0
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        return 0

            tool = StructuredTool.from_function(
                func=query_logs_tool,
                name="query_logs",
                description=(
                    "Query install/app logs for a customer. "
                    "Uses local demo files (Amerihealth failure / Siemens success) "
                    "or Loki when connected. Call this for root-cause / install status questions."
                ),
                args_schema=QueryLogsInput,
            )
            self.llm = ChatOllama(
                model=settings.ollama_model,
                base_url=settings.ollama_base_url,
                temperature=settings.ollama_temperature,
            )
            self.llm_with_tools = self.llm.bind_tools([tool])
            self._tools_by_name = {tool.name: tool}
            self._llm_ready = True
            logger.info(
                "Ollama connected — model=%s url=%s",
                settings.ollama_model,
                settings.ollama_base_url,
            )
            return True
        except Exception as e:
            logger.warning("Ollama setup failed, using local chat/RCA: %s", e)
            return False

    def run(
        self, question: str, chat_history: Optional[list[dict[str, str]]] = None
    ) -> str:
        question = (question or "").strip()
        if not question:
            return "Please ask a question about a customer installation or error."

        store = get_simulation_store()
        # Only default to Amerihealth when this looks like a log/RCA question
        if is_log_question(question):
            customer = extract_customer(
                question, default=store.customer or "Amerihealth"
            )
        else:
            customer = extract_customer(question, default="") or ""

        try:
            answer = self._run_inner(question, customer, chat_history)
        except Exception as e:
            logger.warning("Agent run failed: %s", e)
            if is_log_question(question) and customer:
                logs = _fetch_logs_for_customer(customer)
                answer = analyze_logs(logs, question, customer=customer)
            else:
                answer = _general_chat_answer(question)

        return sanitize_answer(answer)

    def _run_inner(
        self,
        question: str,
        customer: str,
        chat_history: Optional[list[dict[str, str]]],
    ) -> str:
        # Inventory lists must be grounded — Ollama previously mis-labeled Siemens as failed.
        if is_inventory_question(question):
            logger.info("Customer inventory answer")
            return answer_customer_inventory(question)

        # Customer / install RCA → deterministic analysis from data/ logs (accurate demos).
        # LOCAL_LOGS_ONLY controls Loki vs files inside query_logs, not the chat brain.
        if is_log_question(question):
            cust = customer or get_simulation_store().customer or "Amerihealth"
            logs = _fetch_logs_for_customer(cust)
            local_rca = analyze_logs(logs, question, customer=cust)
            # Optional: lightly polish wording with Ollama while keeping facts from local_rca
            if self._ensure_llm():
                polished = self._polish_rca_with_llm(question, local_rca, logs[:6000])
                if polished:
                    logger.info("Ollama polished RCA for customer=%s", cust)
                    return polished
            logger.info("Local RCA for customer=%s", cust)
            return local_rca

        # Free-form chat → Ollama when available
        if self._ensure_llm():
            logger.info("Ollama free chat")
            return self._run_llm(question, chat_history)

        logger.info("General chat (no Ollama)")
        return _general_chat_answer(question)

    def _polish_rca_with_llm(self, question: str, local_rca: str, log_excerpt: str) -> str:
        """Ask Ollama to rephrase the grounded RCA; fall back to local_rca on any issue."""
        from langchain_core.messages import HumanMessage, SystemMessage

        prompt = (
            "You are Matilda, a support engineer. Rephrase the grounded RCA below as a clear "
            "chat answer. Keep the same facts, evidence quotes, root cause, and confidence. "
            "Do NOT invent new log lines or change the root cause. "
            "Use markdown sections: **Symptom**, **Key evidence**, **Root cause**, "
            "**Next steps**, **Confidence:**\n\n"
            f"User question: {question}\n\n"
            f"Grounded RCA (source of truth):\n{local_rca}\n\n"
            f"Log excerpt (reference only):\n{log_excerpt[:4000]}"
        )
        try:
            ai = self.llm.invoke(
                [
                    SystemMessage(content=CHAT_SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ]
            )
            text = (getattr(ai, "content", None) or "").strip()
            if not text or len(text) < 40:
                return local_rca
            # If model drifted away from the key fault signal, keep local
            low = text.lower()
            loc = local_rca.lower()
            if "k3s-selinux" in loc and "k3s-selinux" not in low and "selinux" not in low:
                return local_rca
            if "installation complete" in loc and "complete" not in low and "success" not in low:
                return local_rca
            return sanitize_answer(text)
        except Exception as e:
            logger.warning("RCA polish failed: %s", e)
            return local_rca

    def _run_llm(
        self,
        question: str,
        chat_history: Optional[list[dict[str, str]]],
    ) -> str:
        from langchain_core.messages import (
            AIMessage,
            HumanMessage,
            SystemMessage,
            ToolMessage,
        )

        messages: list[Any] = [SystemMessage(content=CHAT_SYSTEM_PROMPT)]
        if chat_history:
            for turn in chat_history[-6:]:
                role, content = turn.get("role"), turn.get("content") or ""
                if role == "user":
                    messages.append(HumanMessage(content=content))
                elif role == "assistant":
                    messages.append(AIMessage(content=content))
        messages.append(HumanMessage(content=question))

        try:
            for hop in range(4):
                ai: AIMessage = self.llm_with_tools.invoke(messages)
                messages.append(ai)
                tool_calls = getattr(ai, "tool_calls", None) or []
                if not tool_calls:
                    text = (ai.content or "").strip()
                    if text:
                        return sanitize_answer(text)
                    break
                for call in tool_calls:
                    name = call.get("name") if isinstance(call, dict) else call["name"]
                    args = call.get("args") if isinstance(call, dict) else call["args"]
                    call_id = (
                        call.get("id")
                        if isinstance(call, dict)
                        else call.get("id", name)
                    )
                    # Coerce nulls from Ollama tool JSON
                    raw = dict(args or {})
                    safe_args = {
                        "customer": raw.get("customer") or "",
                        "lookback": raw.get("lookback") or "1h",
                        "filter_text": raw.get("filter_text") or "",
                        "logql_override": raw.get("logql_override") or "",
                        "limit": raw.get("limit") if raw.get("limit") not in (None, "") else 0,
                    }
                    try:
                        safe_args["limit"] = int(safe_args["limit"] or 0)
                    except (TypeError, ValueError):
                        safe_args["limit"] = 0
                    logger.info("Tool call hop=%s name=%s args=%s", hop, name, safe_args)
                    tool = self._tools_by_name.get(name)
                    try:
                        result = (
                            tool.invoke(safe_args) if tool else f"Unknown tool: {name}"
                        )
                    except Exception as tool_err:
                        logger.warning("Tool %s failed: %s", name, tool_err)
                        result = f"(log query failed: {tool_err})"
                    messages.append(
                        ToolMessage(content=str(result), tool_call_id=call_id or name)
                    )
            final = self.llm.invoke(messages)
            text = (getattr(final, "content", None) or "").strip()
            if text:
                return sanitize_answer(text)
        except Exception as e:
            logger.warning("LLM path failed, falling back: %s", e)

        # Fallback without showing errors
        if is_log_question(question):
            store = get_simulation_store()
            cust = extract_customer(question, default=store.customer or "Amerihealth")
            return analyze_logs(_fetch_logs_for_customer(cust), question, customer=cust)
        return _general_chat_answer(question)


_agent: Optional[MatildaAgent] = None


def get_agent() -> MatildaAgent:
    global _agent
    if _agent is None:
        _agent = MatildaAgent()
    return _agent


def reset_agent() -> None:
    global _agent
    _agent = None
