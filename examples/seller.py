#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from threading import Event
from typing import Any, Callable

import httpx

from examples.agent_apps import OfferSpec, SellerApp, SellerTask

TaskHandler = Callable[[SellerTask], bytes | str | dict[str, Any] | list[Any]]


@dataclass(frozen=True)
class SellerOffer:
    sku: str
    price_credits: int
    description: str
    capabilities: tuple[str, ...] = ("verifiable_output",)
    tags: tuple[str, ...] = ("trusted", "fast")


def json_extraction_handler(task: SellerTask) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    if isinstance(task.task_input, dict):
        maybe_records = task.task_input.get("records")
        if isinstance(maybe_records, list):
            records = [item for item in maybe_records if isinstance(item, dict)]
    return {"query": task.query, "records": records}


def make_ollama_handler(
    *,
    model: str = "qwen2.5:32b",
    ollama_base_url: str = "http://127.0.0.1:11434",
) -> TaskHandler:
    def _handler(task: SellerTask) -> dict[str, Any]:
        prompt = (
            "You are a seller agent. Respond with JSON.\n"
            f"sku={task.sku}\n"
            f"query={task.query}\n"
            f"input={json.dumps(task.task_input, sort_keys=True)}"
        )
        try:
            with httpx.Client(base_url=ollama_base_url, timeout=90.0) as http:
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
        except httpx.ConnectError as exc:
            raise RuntimeError(
                "ollama_unreachable: unable to connect to "
                f"{ollama_base_url} (start `ollama serve` and pull model `{model}`)"
            ) from exc
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"ollama_timeout: request exceeded timeout at {ollama_base_url} for model `{model}`"
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            raise RuntimeError(
                f"ollama_http_error: status={status_code} base_url={ollama_base_url} model={model}"
            ) from exc
        return {"query": task.query, "result": payload.get("response", "")}

    return _handler


def serve(
    *,
    handler: TaskHandler,
    offer: SellerOffer,
    base_url: str = "http://127.0.0.1:8000",
    seller_name: str = "seller-agent",
    poll_seconds: float = 1.0,
    heartbeat_seconds: float = 15.0,
    log=print,
    stop_event: Event | None = None,
) -> None:
    app = SellerApp(
        base_url=base_url,
        seller_name=seller_name,
        poll_seconds=poll_seconds,
        heartbeat_seconds=heartbeat_seconds,
        log=log,
        stop_event=stop_event,
        task_handlers={offer.sku: handler},
        offers=[
            OfferSpec(
                sku=offer.sku,
                price_credits=offer.price_credits,
                description=offer.description,
                capabilities=offer.capabilities,
                tags=offer.tags,
            )
        ],
    )
    app.run()


if __name__ == "__main__":
    ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:32b")
    ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    serve(
        handler=make_ollama_handler(
            model=ollama_model,
            ollama_base_url=ollama_base_url,
        ),
        offer=SellerOffer(
            sku="json_extraction",
            price_credits=10,
            description=f"Ollama-backed offer for json_extraction ({ollama_model})",
        ),
    )
