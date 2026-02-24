#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from examples.agent_apps import BuyerApp, ContractTermsBuilder, SearchEvaluator, SearchRequest


def pick_first_result(search_response: dict[str, Any], *, log=print) -> dict[str, Any] | None:
    results = search_response.get("results", [])
    if not results:
        log("[buyer] evaluator: no results yet")
        return None
    selected = results[0]
    log(
        "[buyer] evaluator: picked first result "
        f"seller={selected['seller']['name']} offer={selected['offer']['listing_id']}"
    )
    return selected


def _parse_index_from_text(text: str, *, max_idx: int) -> int | None:
    match = re.search(r"-?\d+", text)
    if match is None:
        return None
    value = int(match.group(0))
    if value < 0 or value > max_idx:
        return None
    return value


def ollama_select_result(
    search_response: dict[str, Any],
    *,
    model: str = "qwen2.5:32b",
    ollama_base_url: str = "http://127.0.0.1:11434",
    log=print,
) -> dict[str, Any] | None:
    results = search_response.get("results", [])
    if not results:
        log("[buyer] evaluator(ollama): no results yet")
        return None

    try:
        prompt = (
            "Pick the best seller index for the buyer.\n"
            f"Return only one integer from 0 to {max(0, len(results) - 1)}.\n"
            f"requirements={json.dumps(search_response.get('requirements', {}), sort_keys=True)}\n"
            f"results={json.dumps(results, sort_keys=True)}"
        )
        with httpx.Client(base_url=ollama_base_url, timeout=45.0) as http:
            response = http.post(
                "/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                },
            )
            response.raise_for_status()
            payload = response.json()

        index = _parse_index_from_text(
            str(payload.get("response", "")),
            max_idx=max(0, len(results) - 1),
        )
        if index is None:
            log("[buyer] evaluator(ollama): bad output; fallback to first result")
            return pick_first_result(search_response, log=log)

        selected = results[index]
        log(
            "[buyer] evaluator(ollama): picked index "
            f"{index} seller={selected['seller']['name']} offer={selected['offer']['listing_id']}"
        )
        return selected
    except Exception:
        log("[buyer] evaluator(ollama): invocation failed; fallback to first result")
        return pick_first_result(search_response, log=log)


def llm_select_result(
    search_response: dict[str, Any],
    *,
    model: str = "qwen2.5:32b",
    ollama_base_url: str = "http://127.0.0.1:11434",
    log=print,
) -> dict[str, Any] | None:
    return ollama_select_result(
        search_response,
        model=model,
        ollama_base_url=ollama_base_url,
        log=log,
    )


@dataclass(frozen=True)
class BuyerTask:
    sku: str
    max_price_credits: int
    query: str
    task_input: Any | None = None
    acceptance_criteria: str | None = None
    required_capabilities: tuple[str, ...] = ("verifiable_output",)
    required_tags: tuple[str, ...] = ("trusted",)
    min_reputation: int = 0
    require_online: bool = True
    online_within_seconds: int = 120
    include_non_matching: bool = True
    limit: int = 20


def run_task(
    task: BuyerTask,
    *,
    base_url: str = "http://127.0.0.1:8000",
    buyer_name: str = "buyer-agent",
    poll_seconds: float = 1.0,
    faucet_amount: int = 100,
    choose_seller: SearchEvaluator = pick_first_result,
    build_contract_terms: ContractTermsBuilder | None = None,
    max_wait_seconds: float | None = None,
    status_log_seconds: float = 30.0,
    log=print,
) -> None:
    app = BuyerApp(
        base_url=base_url,
        buyer_name=buyer_name,
        faucet_amount=faucet_amount,
        poll_seconds=poll_seconds,
        log=log,
    )
    app.run_once(
        SearchRequest(
            sku=task.sku,
            max_price_credits=task.max_price_credits,
            required_capabilities=task.required_capabilities,
            required_tags=task.required_tags,
            min_reputation=task.min_reputation,
            require_online=task.require_online,
            online_within_seconds=task.online_within_seconds,
            include_non_matching=task.include_non_matching,
            limit=task.limit,
            task_query=task.query,
            task_input=task.task_input,
            acceptance_criteria=task.acceptance_criteria,
        ),
        evaluate_search_results=choose_seller,
        build_contract_terms=build_contract_terms,
        max_wait_seconds=max_wait_seconds,
        status_log_seconds=status_log_seconds,
    )


if __name__ == "__main__":
    run_task(
        BuyerTask(
            sku="json_extraction",
            max_price_credits=10,
            query="Extract all records from this JSON payload",
            task_input={"records": [{"id": 1, "source": "buyer"}]},
        ),
        choose_seller=pick_first_result,
    )
