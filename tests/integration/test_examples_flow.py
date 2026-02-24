from __future__ import annotations

from threading import Event, Thread
from time import sleep
from typing import Any

from uvicorn import Config, Server

from app.main import create_app
from examples.buyer import BuyerTask, pick_first_result, run_task
from examples.seller import SellerOffer, json_extraction_handler, serve


def _run_server(server: Server) -> None:
    server.run()


def test_buyer_and_seller_examples_complete_trade(tmp_path) -> None:
    app = create_app(
        db_url=f"sqlite:///{tmp_path / 'examples.db'}",
        artifact_dir=tmp_path / "artifacts",
    )

    config = Config(app=app, host="127.0.0.1", port=8899, log_level="warning")
    server = Server(config)

    server_thread = Thread(target=_run_server, args=(server,), daemon=True)
    server_thread.start()

    # Give uvicorn a moment to bind.
    sleep(0.5)

    seller_logs: list[str] = []
    buyer_logs: list[str] = []
    stop_event = Event()

    seller_thread = Thread(
        target=serve,
        kwargs={
            "handler": json_extraction_handler,
            "offer": SellerOffer(
                sku="json_extraction",
                price_credits=10,
                description="Auto offer for json_extraction",
            ),
            "base_url": "http://127.0.0.1:8899",
            "seller_name": "seller-example",
            "poll_seconds": 0.2,
            "log": seller_logs.append,
            "stop_event": stop_event,
        },
        daemon=True,
    )
    seller_thread.start()

    # Seller should start first and listen.
    sleep(0.4)

    run_task(
        BuyerTask(
            sku="json_extraction",
            max_price_credits=10,
            query="Need json_extraction output",
        ),
        base_url="http://127.0.0.1:8899",
        buyer_name="buyer-example",
        poll_seconds=0.2,
        faucet_amount=100,
        choose_seller=pick_first_result,
        log=buyer_logs.append,
    )

    stop_event.set()
    seller_thread.join(timeout=5)
    server.should_exit = True
    server_thread.join(timeout=5)

    assert any("decision=payout" in line for line in buyer_logs)
    assert any("final balance=90" in line for line in buyer_logs)
    assert any("delivered contract=" in line for line in seller_logs)


def test_examples_pass_query_and_input_into_custom_seller_handler(tmp_path) -> None:
    app = create_app(
        db_url=f"sqlite:///{tmp_path / 'examples-query.db'}",
        artifact_dir=tmp_path / "artifacts-query",
    )

    config = Config(app=app, host="127.0.0.1", port=8900, log_level="warning")
    server = Server(config)

    server_thread = Thread(target=_run_server, args=(server,), daemon=True)
    server_thread.start()
    sleep(0.5)

    stop_event = Event()
    seller_logs: list[str] = []
    buyer_logs: list[str] = []
    captured: list[dict[str, Any]] = []

    def capture_handler(task):
        captured.append(
            {
                "contract_id": task.contract_id,
                "query": task.query,
                "input": task.task_input,
                "raw_terms": task.raw_terms,
            }
        )
        return json_extraction_handler(task)

    seller_thread = Thread(
        target=serve,
        kwargs={
            "handler": capture_handler,
            "offer": SellerOffer(
                sku="json_extraction",
                price_credits=10,
                description="Auto offer for json_extraction",
            ),
            "base_url": "http://127.0.0.1:8900",
            "seller_name": "seller-query",
            "poll_seconds": 0.2,
            "log": seller_logs.append,
            "stop_event": stop_event,
        },
        daemon=True,
    )
    seller_thread.start()
    sleep(0.4)

    run_task(
        BuyerTask(
            sku="json_extraction",
            max_price_credits=10,
            query="extract records from provided payload",
            task_input={"records": [{"id": 7}, {"id": 9}]},
        ),
        base_url="http://127.0.0.1:8900",
        buyer_name="buyer-query",
        poll_seconds=0.2,
        faucet_amount=100,
        choose_seller=pick_first_result,
        log=buyer_logs.append,
    )

    stop_event.set()
    seller_thread.join(timeout=5)
    server.should_exit = True
    server_thread.join(timeout=5)

    assert captured
    assert captured[0]["query"] == "extract records from provided payload"
    assert captured[0]["input"] == {"records": [{"id": 7}, {"id": 9}]}
    assert any("decision=payout" in line for line in buyer_logs)
