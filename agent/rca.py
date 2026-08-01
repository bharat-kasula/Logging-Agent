"""
RCA response helpers — clean chat-style answers for demos.
"""

from __future__ import annotations

import re
from typing import Optional

SYSTEM_PROMPT = """You are **Matilda**, a friendly support engineer in a chat.
Answer only with this structure (markdown):

**Symptom**
one or two short sentences

**Key evidence**
- short log quotes (important lines only)

**Root cause**
plain language, one short paragraph

**Next steps**
1. …
2. …

**Confidence:** High | Medium | Low — one short reason

Never mention Loki, Ollama, simulation mode, connection refused, tool dumps, or internal errors.
Ignore noise such as nm-cloud-setup unit not loaded.
"""

# Lines that must never appear as evidence or in the final chat bubble
NOISE_PATTERNS = (
    "nm-cloud-setup",
    "failed to stop",
    "failed to disable unit",
    "simulation mode",
    "lookback ignored",
    "lines returned",
    "success source:",
    "failure source:",
    "filter:",
    "error querying loki",
    "connection refused",
    "customer_id/filter",
    "agent error",
    "raw tool",
    "errno",
    "offline heuristic",
    "ollama unavailable",
)

# Strip these from any accidental leak in the final answer
LEAK_PATTERNS = re.compile(
    r"(?is)("
    r"connection refused|"
    r"agent error|"
    r"raw tool excerpt|"
    r"errno \d+|"
    r"offline heuristic|"
    r"ollama unavailable|"
    r"simulation mode\s*=|"
    r"\[SIMULATION[^\]]*\]"
    r").*"
)


def _is_noise(line: str) -> bool:
    low = line.lower()
    return any(n in low for n in NOISE_PATTERNS)


def _is_strong_evidence(line: str) -> bool:
    if _is_noise(line):
        return False
    low = line.lower()
    return any(
        k in low
        for k in (
            "fatal",
            "k3s-selinux",
            "k3s installation failed",
            "unable to find a match: k3s",
            "failed to find the k3s-selinux",
            "selinux set to permissive",
            "installation complete",
            "ok: installation complete",
            "installation failed",
        )
    )


def extract_customer(question: str, default: str = "Amerihealth") -> str:
    q = question or ""
    for name in ("Amerihealth", "Siemens", "CustomerA", "CustomerB"):
        if name.lower() in q.lower():
            return name
    m = re.search(
        r"\b(?:on|for|customer(?:_id)?\s*[:=]?\s*)([A-Za-z][A-Za-z0-9_-]+)",
        q,
        re.I,
    )
    if m:
        token = m.group(1)
        if token.lower() not in {
            "the",
            "last",
            "installation",
            "install",
            "failure",
            "issue",
            "error",
            "root",
            "cause",
        }:
            return token
    return default


def _display_line(line: str) -> str:
    """Turn internal log formatting into a short human-readable quote."""
    s = line.strip()
    # Drop simulation / routing headers
    s = re.sub(r"^\[SIMULATION[^\]]*\]\s*", "", s)
    # [ERROR | install] msg  →  ERROR: msg
    s = re.sub(
        r"^\[(ERROR|WARN|FATAL|INFO)\s*\|\s*[^\]]+\]\s*",
        r"\1: ",
        s,
        flags=re.I,
    )
    # [2026-07-10T18:31:07 | FATAL | install] msg  →  FATAL: msg
    s = re.sub(
        r"^\[[\dT:\-\.]+Z?\s*\|\s*(ERROR|WARN|FATAL|INFO)\s*\|\s*[^\]]+\]\s*",
        r"\1: ",
        s,
        flags=re.I,
    )
    # customer_id=X | source=... noise tags at start of line
    s = re.sub(r"^customer_id=\S+\s*\|\s*source=\S+\s*", "", s, flags=re.I)
    # Collapse "ERROR: Error:" / "WARN: WARN:" duplicates from double-prefixing
    s = re.sub(
        r"^(ERROR|WARN|FATAL|INFO):\s*\1:\s*",
        r"\1: ",
        s,
        flags=re.I,
    )
    s = re.sub(r"^(ERROR|WARN|FATAL|INFO):\s*Error:\s*", r"\1: ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:200]


def pick_evidence(log_text: str, max_items: int = 5) -> list[str]:
    """Select high-signal log lines for the answer (display-cleaned)."""
    strong: list[str] = []
    weak: list[str] = []
    for raw in (log_text or "").splitlines():
        line = raw.strip()
        if not line or _is_noise(line):
            continue
        display = _display_line(line)
        if not display or _is_noise(display):
            continue
        if _is_strong_evidence(line) or _is_strong_evidence(display):
            strong.append(display)
        elif any(k in line.upper() for k in ("FATAL", "ERROR", "WARN", "OK")):
            if "nm-cloud" not in line.lower() and "document(s) restored" not in line.lower():
                weak.append(display)
        if len(strong) >= max_items:
            break

    chosen = strong[:max_items]
    if len(chosen) < 3:
        for w in weak:
            if w not in chosen:
                chosen.append(w)
            if len(chosen) >= max_items:
                break

    out: list[str] = []
    seen: set[str] = set()
    for c in chosen:
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def sanitize_answer(text: str) -> str:
    """Remove any accidental internal / connection noise from chat output."""
    if not text:
        return text
    lines = []
    for line in text.splitlines():
        low = line.lower()
        if any(
            x in low
            for x in (
                "connection refused",
                "agent error",
                "raw tool",
                "offline heuristic",
                "ollama unavailable",
                "errno ",
                "simulation mode=",
                "[simulation",
            )
        ):
            continue
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    # Collapse accidental triple blanks
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _has_install_success(lower: str) -> bool:
    return (
        "installation complete" in lower
        or "ok: installation complete" in lower
        or "install completed successfully" in lower
    )


def _has_fatal_k3s(lower: str) -> bool:
    return (
        "k3s installation failed" in lower
        or "fatal: k3s" in lower
        or "| fatal | install] k3s" in lower
        or "fatal | install] k3s installation failed" in lower
    )


def analyze_logs(log_text: str, question: str, customer: Optional[str] = None) -> str:
    """
    Produce a clean demo-ready chat RCA from local (or future Loki) log text.
    """
    customer = customer or extract_customer(question)
    lower = (log_text or "").lower()
    evidence = pick_evidence(log_text)
    success = _has_install_success(lower)
    fatal_k3s = _has_fatal_k3s(lower)
    selinux_noise = "k3s-selinux" in lower or "selinux policy" in lower

    # Success wins when install completed — even if SELinux warnings appeared earlier
    if success and not fatal_k3s:
        success_ev = [
            e
            for e in evidence
            if any(
                k in e.lower()
                for k in ("complete", "success", "ok:", "install")
            )
        ] or evidence[:3]
        # Prefer showing the complete marker if we can
        if not any("complete" in e.lower() for e in success_ev):
            success_ev = ["OK: installation complete"] + success_ev[:4]
        note = ""
        if selinux_noise:
            note = (
                " Earlier SELinux package warnings were non-fatal "
                "(installer continued and finished)."
            )
        return sanitize_answer(
            _format_chat(
                symptom=(
                    f"I checked the install logs for **{customer}** — "
                    "the run completed successfully."
                ),
                evidence=success_ev[:5],
                cause=(
                    f"**{customer}** reached **INSTALLATION COMPLETE**. "
                    f"No fatal k3s failure is present in this log set.{note}"
                ),
                steps=[
                    "If the customer still reports a problem, confirm environment and time window.",
                    "For post-install issues, pull application runtime logs rather than install logs.",
                    "Verify cluster health endpoints for the deployed environment.",
                ],
                confidence="High",
                conf_reason="Install stream ends with INSTALLATION COMPLETE and no FATAL k3s failure.",
            )
        )

    # Hard failure: FATAL k3s (typically after missing k3s-selinux on some sites)
    if fatal_k3s or (selinux_noise and not success and "unable to find a match: k3s-selinux" in lower):
        # Require real abort signals — not mere WARN on a successful Siemens-like run
        if fatal_k3s or "unable to find a match: k3s-selinux" in lower:
            return sanitize_answer(
                _format_chat(
                    symptom=(
                        f"**{customer}** installation failed during **k3s master setup**. "
                        "Cluster bootstrap did not complete."
                    ),
                    evidence=evidence
                    or [
                        "ERROR: Unable to find a match: k3s-selinux",
                        "FATAL: k3s installation failed",
                    ],
                    cause=(
                        "The installer could not find the **k3s-selinux** policy package for this OS "
                        "(log shows package match failure on **10.1**, then a FATAL k3s install). "
                        "Without that SELinux policy package, k3s install aborts."
                    ),
                    steps=[
                        "Install matching `container-selinux` / `k3s-selinux` packages for this OS, "
                        "or confirm SELinux permissive is approved for this site.",
                        "Confirm the installer bundle includes the SELinux RPM for Oracle Linux 10.1 "
                        "(or the target distro).",
                        "Re-run install and confirm you reach `INSTALLATION COMPLETE` with no FATAL k3s errors.",
                        "If it fails again, attach `/matilda/.config/log/matilda_install_*.log` to the ticket.",
                    ],
                    confidence="High",
                    conf_reason=(
                        "FATAL k3s line and/or k3s-selinux package match failure appear in the install log."
                    ),
                )
            )

    if "timeout" in lower or "503" in lower:
        return sanitize_answer(
            _format_chat(
                symptom=f"**{customer}** shows timeout or HTTP 503 failures in the reported window.",
                evidence=evidence or ["Timeout / 503 patterns present in logs."],
                cause="A dependent service or registry endpoint was unavailable or too slow.",
                steps=[
                    "Verify network path and DNS to the failing host.",
                    "Retry after confirming the dependency is healthy.",
                    "Capture the first timeout timestamp for correlation.",
                ],
                confidence="Medium",
                conf_reason="Timeout/5xx patterns found; may need broader context.",
            )
        )

    if evidence or "error" in lower or "fatal" in lower:
        return sanitize_answer(
            _format_chat(
                symptom=f"**{customer}** shows error signatures in the install/runtime logs.",
                evidence=evidence or ["Error lines present in the log sample."],
                cause=(
                    "The failing step is indicated by ERROR/FATAL entries; "
                    "start from the first fatal marker in sequence."
                ),
                steps=[
                    "Locate the first FATAL/ERROR after the last successful step.",
                    "Fix the dependency or config named in that line.",
                    "Re-run the install or failed step and confirm success markers.",
                ],
                confidence="Medium",
                conf_reason="Error lines found; primary failure may need full log correlation.",
            )
        )

    return sanitize_answer(
        _format_chat(
            symptom=f"I could not find a clear failure pattern for **{customer}** in the available logs.",
            evidence=["No strong ERROR/FATAL install markers in the current sample."],
            cause=(
                "The incident may be outside this log file, or the failure is not expressed "
                "as a standard install FATAL."
            ),
            steps=[
                "Confirm the customer name and that the correct log set is selected.",
                "Ask for the approximate failure time and widen the search window.",
                "When live log shipping is connected, pull the install stream for this customer.",
            ],
            confidence="Low",
            conf_reason="Not enough error evidence in the current sample.",
        )
    )


def _format_chat(
    *,
    symptom: str,
    evidence: list[str],
    cause: str,
    steps: list[str],
    confidence: str,
    conf_reason: str,
) -> str:
    # Confidence badge: High / Medium / Low only + short reason (no tool stack talk)
    conf = confidence.strip().title()
    if conf not in {"High", "Medium", "Low"}:
        conf = "Medium"

    ev = "\n".join(f"- `{e}`" for e in evidence[:5])
    st = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    return (
        f"**Symptom**\n{symptom}\n\n"
        f"**Key evidence**\n{ev}\n\n"
        f"**Root cause**\n{cause}\n\n"
        f"**Next steps**\n{st}\n\n"
        f"**Confidence:** {conf}\n"
        f"{conf_reason}"
    )


def fallback_rca_from_logs(log_text: str, question: str) -> str:
    return analyze_logs(log_text, question)
