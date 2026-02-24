#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

from examples.agent_apps import SellerTask
from examples.seller import SellerOffer, serve


def echo_handler(task: SellerTask) -> dict[str, Any]:
    """Return buyer-provided task input as JSON output."""
    return {
        "echo": True,
        "query": task.query,
        "input": task.task_input,
        "contract_id": task.contract_id,
    }


if __name__ == "__main__":
    serve(
        handler=echo_handler,
        offer=SellerOffer(
            sku="json_extraction",
            price_credits=10,
            description="Echo seller for quick local testing",
        ),
    )
