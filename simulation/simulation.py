"""
Demo simulation store.

Loads real installer log dumps (plain text) or JSON samples.
Default demos:
  - Success → Siemens (header at top of success_install.log stream)
  - Failure → Amerihealth (customer_id)
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

from config import settings
from tools.log_parser import load_log_file
from tools.loki_tool import format_logs_for_llm

logger = logging.getLogger("matilda.simulation")

# Default demo customers for this install-log format
DEFAULT_SUCCESS_CUSTOMER = "Siemens"
DEFAULT_FAILURE_CUSTOMER = "Amerihealth"


def _customer_match(query: str, entry_customer: str, message: str) -> bool:
    """Fuzzy match CustomerA / Amerihealth / siemens etc."""
    if not query:
        return True
    q = query.strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    c = (entry_customer or "").lower().replace(" ", "").replace("_", "").replace("-", "")
    m = (message or "").lower().replace(" ", "")
    if not q:
        return True
    if q in c or c in q:
        return True
    if q in m or f"customer_id={q}" in m or f"customer={q}" in m:
        return True
    # partial: "ameri" matches amerihealth
    if len(q) >= 4 and (q in c or any(q in part for part in c.split())):
        return True
    return False


class SimulationStore:
    """
    Modes:
      - off: agent uses real Loki
      - success: Siemens success install log
      - failure: Amerihealth failed install log
    """

    def __init__(self) -> None:
        self.success_customer: str = DEFAULT_SUCCESS_CUSTOMER
        self.failure_customer: str = DEFAULT_FAILURE_CUSTOMER
        # "active" customer filter default for UI
        self.customer: str = DEFAULT_FAILURE_CUSTOMER
        self.success_logs: list[dict[str, Any]] = []
        self.failure_logs: list[dict[str, Any]] = []
        self.success_path = settings.success_logs_path
        self.failure_path = settings.failure_logs_path
        self._resolve_default_paths()
        self._load_defaults()
        # Prefer local data/ files until Loki is available
        default_mode = (settings.simulation_default_mode or "failure").lower()
        if default_mode not in {"off", "success", "failure"}:
            default_mode = "failure"
        if settings.local_logs_only and default_mode == "off":
            default_mode = "failure"
        self.mode: str = default_mode
        if self.mode == "success":
            self.customer = self.success_customer
        elif self.mode == "failure":
            self.customer = self.failure_customer
        logger.info(
            "Simulation default mode=%s customer=%s local_logs_only=%s",
            self.mode,
            self.customer,
            settings.local_logs_only,
        )

    def _resolve_default_paths(self) -> None:
        """Prefer real installer dumps if present in data/."""
        data = settings.data_dir
        candidates_success = [
            data / "success_install.log",
            data / "success_logs.json",
            settings.success_logs_path,
        ]
        candidates_failure = [
            data / "failed_log.txt",
            data / "failure_logs.json",
            settings.failure_logs_path,
        ]
        for p in candidates_success:
            if p.exists():
                self.success_path = p
                break
        for p in candidates_failure:
            if p.exists():
                self.failure_path = p
                break

    def _load_defaults(self) -> None:
        self.success_logs = load_log_file(
            self.success_path,
            customer=self.success_customer,
            header_note=f"Siemens | customer_id={self.success_customer}",
        )
        self.failure_logs = load_log_file(
            self.failure_path,
            customer=self.failure_customer,
            header_note=f"customer_id={self.failure_customer}",
        )
        logger.info(
            "Loaded success=%s (%d lines) failure=%s (%d lines)",
            self.success_path.name,
            len(self.success_logs),
            self.failure_path.name,
            len(self.failure_logs),
        )

    def reload_from_disk(self) -> str:
        self._resolve_default_paths()
        self._load_defaults()
        return (
            f"Reloaded: success **{self.success_path.name}** "
            f"({len(self.success_logs)} lines, customer={self.success_customer}) · "
            f"failure **{self.failure_path.name}** "
            f"({len(self.failure_logs)} lines, customer_id={self.failure_customer})"
        )

    def set_mode(self, mode: str) -> str:
        mode = (mode or "off").strip().lower()
        if mode not in {"off", "success", "failure"}:
            return f"Unknown mode {mode!r}. Use off | success | failure."
        self.mode = mode
        if mode == "success":
            self.customer = self.success_customer
        elif mode == "failure":
            self.customer = self.failure_customer
        return (
            f"Simulation mode=**{self.mode}** · "
            f"ask about **{self.customer}** "
            f"(success file={self.success_path.name}, failure file={self.failure_path.name})"
        )

    def set_customer(self, customer: str) -> str:
        self.customer = (customer or self.customer).strip()
        return f"Demo customer filter set to **{self.customer}**."

    def load_success(self) -> str:
        self.success_logs = load_log_file(
            self.success_path,
            customer=self.success_customer,
            header_note=f"Siemens | customer_id={self.success_customer}",
        )
        self.mode = "success"
        self.customer = self.success_customer
        return (
            f"Loaded **Siemens** success install log "
            f"(`{self.success_path.name}`, {len(self.success_logs)} lines). "
            f"Mode=success. Ask e.g. “What is the status of Siemens installation?”"
        )

    def load_failure(self) -> str:
        self.failure_logs = load_log_file(
            self.failure_path,
            customer=self.failure_customer,
            header_note=f"customer_id={self.failure_customer}",
        )
        self.mode = "failure"
        self.customer = self.failure_customer
        return (
            f"Loaded **Amerihealth** failure install log "
            f"(`{self.failure_path.name}`, {len(self.failure_logs)} lines). "
            f"Mode=failure. Ask e.g. “Root cause of installation failure on Amerihealth?”"
        )

    def convert_success_to_failure(self) -> str:
        """
        Demo transform: start from Siemens success stream and inject failure markers.
        Switches mode to failure (in-memory; does not overwrite Amerihealth file).
        """
        if not self.success_logs:
            self.load_success()
        if not self.success_logs:
            return "No success logs to convert."

        converted: list[dict[str, Any]] = []
        # Keep Siemens header but note conversion
        for entry in deepcopy(self.success_logs[: min(40, len(self.success_logs))]):
            msg = str(entry.get("message") or entry.get("line") or "")
            msg2 = msg
            msg2 = re.sub(r"(?i)INSTALLATION COMPLETE", "INSTALLATION FAILED", msg2)
            msg2 = re.sub(r"(?i)completed successfully", "FAILED", msg2)
            if "k3s" in msg2.lower() and "OK" in str(entry.get("level", "")):
                msg2 = "k3s install step degraded (simulated)"
            entry["message"] = msg2
            entry["line"] = msg2
            entry["level"] = "ERROR" if "fail" in msg2.lower() else entry.get("level", "INFO")
            labels = entry.get("labels") or {}
            labels["customer"] = self.failure_customer
            labels["customer_id"] = self.failure_customer
            labels["sim"] = "converted"
            entry["labels"] = labels
            converted.append(entry)

        converted.append(
            {
                "timestamp": "",
                "level": "ERROR",
                "service": "install",
                "message": (
                    f"[ERROR] Failed to find the k3s-selinux policy (simulated for "
                    f"customer_id={self.failure_customer})"
                ),
                "line": "[ERROR] Failed to find the k3s-selinux policy",
                "labels": {
                    "customer": self.failure_customer,
                    "customer_id": self.failure_customer,
                    "job": "install",
                    "sim": "converted",
                },
            }
        )
        converted.append(
            {
                "timestamp": "",
                "level": "ERROR",
                "service": "install",
                "message": (
                    f"FATAL: k3s installation failed — customer_id={self.failure_customer}"
                ),
                "line": "FATAL: k3s installation failed",
                "labels": {
                    "customer": self.failure_customer,
                    "customer_id": self.failure_customer,
                    "job": "install",
                    "sim": "converted",
                },
            }
        )

        self.failure_logs = converted
        self.mode = "failure"
        self.customer = self.failure_customer
        return (
            f"Converted Siemens success sample → failure scenario for "
            f"**{self.failure_customer}** ({len(converted)} lines, in-memory). "
            "Ask for RCA on Amerihealth."
        )

    def active_logs(self) -> list[dict[str, Any]]:
        if self.mode == "success":
            return self.success_logs
        if self.mode == "failure":
            return self.failure_logs
        return []

    def query_simulated(
        self,
        customer: str = "",
        lookback: str = "1h",
        filter_text: str = "",
        logql_override: str = "",
        limit: int = 0,
    ) -> str:
        logs = self.active_logs()
        cust = (customer or self.customer or "").strip()
        filt = (filter_text or "").strip().lower()

        # Prefer ERROR/WARN/FATAL when user asks about issues without filter
        prefer_errors = any(
            k in (customer + " " + filter_text + " " + lookback).lower()
            for k in ()
        )

        filtered: list[dict[str, Any]] = []
        for entry in logs:
            labels = entry.get("labels") or {}
            entry_cust = str(
                labels.get("customer_id")
                or labels.get("customer")
                or ""
            )
            msg = str(entry.get("message") or entry.get("line") or "")
            if not _customer_match(cust, entry_cust, msg):
                continue
            if filt:
                if filt in {"error", "errors", "fail", "failed", "failure", "fatal"}:
                    if str(entry.get("level", "")).upper() not in {
                        "ERROR",
                        "WARN",
                        "FATAL",
                    } and not any(
                        k in msg.lower()
                        for k in ("error", "fail", "fatal", "unable", "timeout")
                    ):
                        continue
                elif filt not in msg.lower() and filt not in str(labels).lower():
                    continue
            filtered.append(entry)

        # If nothing matched filter but we have logs for customer, return tail of stream
        if not filtered and logs:
            filtered = [
                e
                for e in logs
                if _customer_match(
                    cust,
                    str((e.get("labels") or {}).get("customer_id") or (e.get("labels") or {}).get("customer") or ""),
                    str(e.get("message") or e.get("line") or ""),
                )
            ]
            if not filtered:
                filtered = logs

        # Cap context: keep signal errors + ALWAYS the stream tail (outcome markers live there).
        # Ignore noisy early "Failed to stop nm-cloud-setup".
        max_n = limit or settings.max_log_lines_to_llm
        noise = ("nm-cloud-setup", "failed to stop", "failed to disable unit")

        def is_noise(e: dict[str, Any]) -> bool:
            m = str(e.get("message") or e.get("line") or "").lower()
            return any(n in m for n in noise)

        def is_signal_error(e: dict[str, Any]) -> bool:
            if is_noise(e):
                return False
            m = str(e.get("message") or e.get("line") or "").lower()
            lvl = str(e.get("level") or "").upper()
            return lvl in {"ERROR", "FATAL"} or any(
                k in m
                for k in (
                    "fatal",
                    "unable to find a match",
                    "k3s installation failed",
                    "failed to find the k3s",
                )
            )

        def is_outcome(e: dict[str, Any]) -> bool:
            m = str(e.get("message") or e.get("line") or "").lower()
            return any(
                k in m
                for k in (
                    "installation complete",
                    "installation failed",
                    "k3s installation failed",
                    "install completed",
                )
            )

        errors = [e for e in filtered if is_signal_error(e)]
        # Prefer full stream (not just filtered) for outcome + tail so success markers survive
        stream = logs if logs else filtered
        outcomes = [e for e in stream if is_outcome(e)]
        head = (filtered or stream)[:2]
        # Last N lines of the real install stream (complete / final steps)
        tail_n = min(25, max(12, max_n // 3))
        tail = stream[-tail_n:] if stream else []

        combined: list[dict[str, Any]] = []
        seen: set[int] = set()
        # Order: header → key errors → outcome markers → install tail
        for e in head + errors[: max(20, max_n // 2)] + outcomes + tail:
            key = id(e)
            if key in seen:
                continue
            seen.add(key)
            combined.append(e)
        # Soft cap but never drop outcomes/tail if possible
        if len(combined) > max_n:
            keep_ids = {id(e) for e in outcomes + tail}
            trimmed = [e for e in combined if id(e) in keep_ids]
            for e in combined:
                if id(e) not in keep_ids:
                    trimmed.insert(0, e)
                if len(trimmed) >= max_n:
                    break
            # Re-append outcomes/tail that may have been cut
            for e in outcomes + tail:
                if e not in trimmed:
                    trimmed.append(e)
            combined = trimmed[-max_n:] if len(trimmed) > max_n else trimmed

        # Compact internal header only (never shown raw in chat — RCA filters it)
        src = (
            self.success_path.name if self.mode == "success" else self.failure_path.name
        )
        header = (
            f"customer={cust or self.customer}\n"
            f"source={src}\n"
            f"Filter: {filter_text or '(none)'}\n"
            f"Lines returned: {len(combined)} (matched {len(filtered)} / total {len(logs)})\n"
            f"---\n"
        )
        return header + format_logs_for_llm(combined, max_lines=max(max_n, len(combined)))

    def preview(self, max_lines: int = 15) -> str:
        logs = self.active_logs()
        if self.mode == "off":
            return (
                "Simulation is **off** — agent will query real Loki.\n"
                f"Files ready: Siemens success=`{self.success_path.name}` "
                f"({len(self.success_logs)}), "
                f"Amerihealth failure=`{self.failure_path.name}` "
                f"({len(self.failure_logs)})."
            )
        if not logs:
            return f"Mode={self.mode} but no logs loaded. Click Load success/failure."
        head = format_logs_for_llm(logs[:3], max_lines=3)
        tail = format_logs_for_llm(logs[-max_lines:], max_lines=max_lines)
        return (
            f"Mode=**{self.mode}** | customer=**{self.customer}** | lines={len(logs)}\n\n"
            f"--- header ---\n{head}\n\n--- tail (most relevant for install) ---\n{tail}"
        )


_store: Optional[SimulationStore] = None


def get_simulation_store() -> SimulationStore:
    global _store
    if _store is None:
        _store = SimulationStore()
    return _store
