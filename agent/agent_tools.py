"""Log query routing — local data/ files vs real Loki."""

from __future__ import annotations

import logging

from config import settings
from simulation.simulation import get_simulation_store
from tools.loki_tool import query_loki_logs

logger = logging.getLogger("matilda.tools")


def _pick_sim_mode_for_customer(customer: str, store) -> str:
    c = (customer or "").lower().replace(" ", "")
    if "siemen" in c:
        return "success"
    if "ameri" in c or "health" in c:
        return "failure"
    if store.mode in {"success", "failure"}:
        return store.mode
    return "failure"


def query_logs_routed(
    customer: str = "",
    lookback: str = "1h",
    filter_text: str = "",
    logql_override: str = "",
    limit: int = 0,
) -> str:
    """
    Prefer local installer logs under data/ until Loki is integrated.
    Set LOCAL_LOGS_ONLY=false and mode=off to use real Loki.
    """
    store = get_simulation_store()

    if settings.local_logs_only or store.mode != "off":
        picked = _pick_sim_mode_for_customer(customer or store.customer, store)
        store.mode = picked
        store.customer = (
            store.success_customer if picked == "success" else store.failure_customer
        )
        logger.info(
            "Local logs mode=%s customer=%s filter=%s",
            store.mode,
            customer or store.customer,
            filter_text,
        )
        return store.query_simulated(
            customer=customer or store.customer,
            lookback=lookback,
            filter_text=filter_text,
            logql_override=logql_override,
            limit=limit,
        )

    result = query_loki_logs(
        customer=customer,
        lookback=lookback,
        filter_text=filter_text,
        logql_override=logql_override,
        limit=limit,
    )
    if isinstance(result, str) and result.startswith("ERROR querying Loki"):
        logger.warning("Loki unavailable — using local data/ logs")
        picked = _pick_sim_mode_for_customer(customer, store)
        store.mode = picked
        store.customer = (
            store.success_customer if picked == "success" else store.failure_customer
        )
        return store.query_simulated(
            customer=customer or store.customer,
            lookback=lookback,
            filter_text=filter_text,
            logql_override=logql_override,
            limit=limit,
        )
    return result
