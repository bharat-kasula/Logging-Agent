"""
Parse installation / app log files into a common structure for simulation + LLM.

Supports:
  - Plain text lines (Matilda installer style)
  - Optional header lines (customer id, product name)
  - Existing JSON sample format (logs: [...])
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

# [2026-07-28 16:57:05] INFO: message
# [2026-07-28 16:57:05] OK:   message
# [ERROR]  Failed to find...
TS_LEVEL_RE = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\]\s*"
    r"(?P<level>INFO|WARN|WARNING|ERROR|OK|FATAL|DEBUG|TRACE)?\s*:?\s*(?P<msg>.*)$",
    re.IGNORECASE,
)
BRACKET_LEVEL_RE = re.compile(
    r"^\[(?P<level>INFO|WARN|WARNING|ERROR|FATAL|DEBUG)\]\s*(?P<msg>.*)$",
    re.IGNORECASE,
)


def _infer_level(line: str, explicit: Optional[str] = None) -> str:
    if explicit:
        lvl = explicit.upper()
        if lvl == "WARNING":
            return "WARN"
        if lvl == "OK":
            return "INFO"
        return lvl
    u = line.upper()
    if "FATAL" in u:
        return "ERROR"
    if "ERROR" in u or "FAILED" in u:
        return "ERROR"
    if "WARN" in u:
        return "WARN"
    return "INFO"


def parse_plain_log_text(
    text: str,
    *,
    customer: str,
    source_file: str = "",
    header_note: str = "",
) -> list[dict[str, Any]]:
    """
    Convert raw multi-line installer log text into structured entries.
    Prepends customer header so the agent can attribute the stream.
    """
    entries: list[dict[str, Any]] = []

    # Explicit header for demos / multi-customer simulation
    if customer or header_note:
        header_msg = f"customer_id={customer}"
        if header_note:
            header_msg = f"{header_note} | {header_msg}"
        entries.append(
            {
                "timestamp": "",
                "level": "INFO",
                "service": "install",
                "message": header_msg,
                "line": header_msg,
                "labels": {
                    "customer": customer,
                    "customer_id": customer,
                    "job": "install",
                    "source": source_file or "plain",
                },
            }
        )

    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line.strip():
            continue

        ts = ""
        level: Optional[str] = None
        msg = line

        m = TS_LEVEL_RE.match(line)
        if m:
            ts = m.group("ts").replace(" ", "T")
            level = m.group("level")
            msg = m.group("msg") or line
        else:
            m2 = BRACKET_LEVEL_RE.match(line)
            if m2:
                level = m2.group("level")
                msg = m2.group("msg") or line

        lvl = _infer_level(line, level)
        entries.append(
            {
                "timestamp": ts,
                "level": lvl,
                "service": "install",
                "message": msg if msg else line,
                "line": line,
                "labels": {
                    "customer": customer,
                    "customer_id": customer,
                    "job": "install",
                    "source": source_file or "plain",
                },
            }
        )

    return entries


def load_log_file(
    path: Path,
    *,
    customer: str,
    header_note: str = "",
) -> list[dict[str, Any]]:
    """
    Load logs from JSON sample or plain .log/.txt installer dump.
    """
    if not path.exists():
        return []

    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8", errors="replace")

    if suffix == ".json":
        data = json.loads(text)
        logs = data.get("logs") if isinstance(data, dict) else data
        if not isinstance(logs, list):
            return []
        # Ensure customer labels
        out: list[dict[str, Any]] = []
        cust = customer or (data.get("customer") if isinstance(data, dict) else "") or ""
        if cust or header_note:
            out.extend(
                parse_plain_log_text(
                    "",
                    customer=str(cust),
                    source_file=path.name,
                    header_note=header_note,
                )[:1]
            )
        for entry in logs:
            e = dict(entry)
            labels = dict(e.get("labels") or {})
            if cust:
                labels.setdefault("customer", cust)
                labels.setdefault("customer_id", cust)
            e["labels"] = labels
            if "message" not in e and "line" in e:
                e["message"] = e["line"]
            if "line" not in e and "message" in e:
                e["line"] = e["message"]
            out.append(e)
        return out

    # Plain text installer / app logs
    return parse_plain_log_text(
        text,
        customer=customer,
        source_file=path.name,
        header_note=header_note,
    )
