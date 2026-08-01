"""
Loki client + LangChain tool for LogQL queries.

Supports:
  - Basic auth (username/password)
  - Bearer token
  - X-Scope-OrgID header (Grafana multi-tenant)
  - Customer label filters and time ranges
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from config import settings

logger = logging.getLogger("matilda.loki")

# Map friendly lookbacks → timedelta
LOOKBACK_MAP = {
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "2h": timedelta(hours=2),
    "6h": timedelta(hours=6),
    "12h": timedelta(hours=12),
    "24h": timedelta(hours=24),
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
}


def parse_lookback(lookback: str) -> timedelta:
    """Parse '30m', '2h', '1d' style strings into timedelta."""
    key = (lookback or settings.default_lookback).strip().lower()
    if key in LOOKBACK_MAP:
        return LOOKBACK_MAP[key]
    m = re.fullmatch(r"(\d+)([mhd])", key)
    if not m:
        logger.warning("Unknown lookback %r — using default %s", lookback, settings.default_lookback)
        return LOOKBACK_MAP.get(settings.default_lookback, timedelta(hours=1))
    n, unit = int(m.group(1)), m.group(2)
    if unit == "m":
        return timedelta(minutes=n)
    if unit == "h":
        return timedelta(hours=n)
    return timedelta(days=n)


def build_logql(
    customer: Optional[str] = None,
    extra_filter: str = "",
    stream_selector: Optional[str] = None,
    customer_label: Optional[str] = None,
) -> str:
    """
    Build a LogQL query string.

    Example:
      {job=~".+", customer="CustomerA"} |= "error"
    """
    selector = (stream_selector or settings.loki_stream_selector).strip()
    # Ensure braces
    if not selector.startswith("{"):
        selector = "{" + selector + "}"

    label = customer_label or settings.default_customer_label
    if customer:
        # Inject customer label into stream selector
        inner = selector.strip()[1:-1].strip()
        cust_clause = f'{label}="{customer}"'
        if inner:
            # Avoid double-adding if already present
            if label + "=" not in inner:
                selector = "{" + inner + ", " + cust_clause + "}"
            else:
                # Replace existing customer label value roughly
                selector = re.sub(
                    rf'{re.escape(label)}="[^"]*"',
                    cust_clause,
                    selector,
                )
        else:
            selector = "{" + cust_clause + "}"

    query = selector
    filt = (extra_filter or "").strip()
    if filt:
        # Allow raw pipeline (|= "x") or plain keyword
        if filt.startswith("|") or filt.startswith("!"):
            query = f"{query} {filt}"
        else:
            query = f'{query} |= `{filt}`'
    return query


class LokiClient:
    """Thin HTTP client for Loki query_range API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        token: Optional[str] = None,
        org_id: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> None:
        self.base_url = (base_url or settings.loki_url).rstrip("/")
        self.username = username if username is not None else settings.loki_username
        self.password = password if password is not None else settings.loki_password
        self.token = token if token is not None else settings.loki_token
        self.org_id = org_id if org_id is not None else settings.loki_org_id
        self.timeout = timeout or settings.loki_timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.org_id:
            headers["X-Scope-OrgID"] = self.org_id
        return headers

    def _auth(self) -> Optional[httpx.BasicAuth]:
        if self.token:
            return None
        if self.username:
            return httpx.BasicAuth(self.username, self.password or "")
        return None

    def query_range(
        self,
        logql: str,
        lookback: str = "1h",
        limit: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Execute LogQL against /loki/api/v1/query_range.

        Returns raw Loki JSON response.
        """
        limit = limit or settings.loki_query_limit
        end = datetime.now(timezone.utc)
        start = end - parse_lookback(lookback)

        # Loki wants nanosecond epoch for query_range
        params = {
            "query": logql,
            "start": str(int(start.timestamp() * 1e9)),
            "end": str(int(end.timestamp() * 1e9)),
            "limit": str(limit),
            "direction": "backward",
        }

        url = f"{self.base_url}/loki/api/v1/query_range"
        logger.info("Loki query_range: %s | lookback=%s limit=%s", logql, lookback, limit)

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(
                url,
                params=params,
                headers=self._headers(),
                auth=self._auth(),
            )
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def flatten_streams(payload: dict[str, Any], max_lines: int = 200) -> list[dict[str, Any]]:
        """
        Flatten Loki streams into a list of {timestamp, line, labels} dicts.
        Newest first (direction=backward).
        """
        results: list[dict[str, Any]] = []
        data = payload.get("data") or {}
        result = data.get("result") or []
        for stream in result:
            labels = stream.get("stream") or {}
            for ts_ns, line in stream.get("values") or []:
                try:
                    ts = datetime.fromtimestamp(int(ts_ns) / 1e9, tz=timezone.utc)
                    ts_iso = ts.isoformat()
                except Exception:
                    ts_iso = str(ts_ns)
                results.append(
                    {
                        "timestamp": ts_iso,
                        "line": line,
                        "labels": labels,
                    }
                )
                if len(results) >= max_lines:
                    return results
        return results


def format_logs_for_llm(logs: list[dict[str, Any]], max_lines: Optional[int] = None) -> str:
    """Compact text block of log lines for the LLM context window."""
    max_lines = max_lines or settings.max_log_lines_to_llm
    lines: list[str] = []
    for entry in logs[:max_lines]:
        labels = entry.get("labels") or {}
        # Prefer structured message if present (simulation format)
        msg = entry.get("message") or entry.get("line") or ""
        level = entry.get("level") or labels.get("level") or ""
        service = entry.get("service") or labels.get("job") or labels.get("service") or ""
        ts = entry.get("timestamp") or ""
        prefix_parts = [p for p in [ts, level, service] if p]
        prefix = " | ".join(prefix_parts)
        lines.append(f"[{prefix}] {msg}" if prefix else str(msg))
    if not lines:
        return "(no log lines returned)"
    return "\n".join(lines)


def query_loki_logs(
    customer: str = "",
    lookback: str = "1h",
    filter_text: str = "",
    logql_override: str = "",
    limit: int = 0,
) -> str:
    """
    Tool function for the agent: query Loki and return formatted log text.

    Args:
        customer: Customer name/id for label filter (optional).
        lookback: Time window e.g. 30m, 1h, 2h, 6h.
        filter_text: Extra LogQL filter or keyword (e.g. error, timeout).
        logql_override: If set, use this full LogQL instead of building one.
        limit: Max log lines (0 = config default).
    """
    try:
        client = LokiClient()
        if logql_override.strip():
            logql = logql_override.strip()
        else:
            logql = build_logql(
                customer=customer.strip() or None,
                extra_filter=filter_text,
            )
        logger.info("Agent Loki tool → LogQL: %s", logql)
        payload = client.query_range(
            logql=logql,
            lookback=lookback or settings.default_lookback,
            limit=limit or settings.loki_query_limit,
        )
        flat = client.flatten_streams(payload, max_lines=settings.max_log_lines_to_llm)
        header = (
            f"LogQL: {logql}\n"
            f"Lookback: {lookback or settings.default_lookback}\n"
            f"Lines: {len(flat)}\n"
            f"---\n"
        )
        return header + format_logs_for_llm(flat)
    except httpx.HTTPStatusError as e:
        msg = f"Loki HTTP error {e.response.status_code}: {e.response.text[:500]}"
        logger.error(msg)
        return f"ERROR querying Loki: {msg}"
    except httpx.RequestError as e:
        msg = f"Loki connection error: {e}"
        logger.error(msg)
        return f"ERROR querying Loki: {msg}. Check LOKI_URL and network access."
    except Exception as e:
        logger.exception("Unexpected Loki error")
        return f"ERROR querying Loki: {e}"
