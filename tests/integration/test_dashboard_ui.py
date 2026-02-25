import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.mark.parametrize("ledger_backend", ["db", "evm_local"])
def test_dashboard_ui_can_run_end_to_end_trade(tmp_path: Path, ledger_backend: str) -> None:
    app = create_app(
        db_url=f"sqlite:///{tmp_path / f'dashboard-flow-{ledger_backend}.db'}",
        artifact_dir=tmp_path / "artifacts",
        ledger_backend=ledger_backend,
    )

    with TestClient(app) as client:
        dashboard = client.get("/dashboard")
        assert dashboard.status_code == 200
        assert "Solar Souq Control Deck" in dashboard.text

        seller = client.post(
            "/ui/api/agents",
            json={
                "name": "seller-ui",
                "role": "seller",
                "skus": ["json_extraction"],
                "capabilities": ["verifiable_output"],
                "tags": ["trusted"],
                "description": "Dashboard seller",
            },
        )
        assert seller.status_code == 201
        seller_id = seller.json()["id"]

        buyer = client.post(
            "/ui/api/agents",
            json={
                "name": "buyer-ui",
                "role": "buyer",
            },
        )
        assert buyer.status_code == 201
        buyer_id = buyer.json()["id"]

        faucet = client.post(f"/ui/api/agents/{buyer_id}/faucet", json={"amount": 100})
        assert faucet.status_code == 200
        assert faucet.json()["balance"] == 100

        demand = client.post(
            "/ui/api/listings",
            json={
                "agent_id": buyer_id,
                "kind": "demand",
                "sku": "json_extraction",
                "price_credits": 10,
                "description": "Need extraction",
            },
        )
        assert demand.status_code == 201
        demand_id = demand.json()["id"]

        offer = client.post(
            "/ui/api/listings",
            json={
                "agent_id": seller_id,
                "kind": "offer",
                "sku": "json_extraction",
                "price_credits": 10,
                "description": "Can extract",
            },
        )
        assert offer.status_code == 201
        offer_id = offer.json()["id"]

        search = client.post(
            "/ui/api/search",
            json={
                "buyer_id": buyer_id,
                "sku": "json_extraction",
                "required_capabilities": ["verifiable_output"],
                "required_tags": ["trusted"],
                "max_price_credits": 10,
            },
        )
        assert search.status_code == 200
        results = search.json()["results"]
        assert results
        assert any(item["offer"]["listing_id"] == offer_id for item in results)

        contract = client.post(
            "/ui/api/contracts/handshake-activate",
            json={
                "buyer_id": buyer_id,
                "demand_listing_id": demand_id,
                "offer_listing_id": offer_id,
                "terms": json.dumps(
                    {
                        "query": "Extract records",
                        "input": {"records": [{"id": 1, "source": "dashboard"}]},
                    }
                ),
            },
        )
        assert contract.status_code == 200
        contract_id = contract.json()["id"]
        assert contract.json()["status"] == "active"

        delivered = client.post(
            f"/ui/api/contracts/{contract_id}/deliver",
            json={
                "seller_id": seller_id,
                "payload_text": '{"records":[{"id":1,"source":"seller"}]}',
            },
        )
        assert delivered.status_code == 200
        assert delivered.json()["status"] == "delivered"

        artifact = client.post(
            f"/ui/api/contracts/{contract_id}/artifact",
            json={"buyer_id": buyer_id},
        )
        assert artifact.status_code == 200
        assert '"records":[{"id":1,"source":"seller"}]' in artifact.json()["plaintext"]

        decision = client.post(
            f"/ui/api/contracts/{contract_id}/decision",
            json={"buyer_id": buyer_id, "accept": True},
        )
        assert decision.status_code == 200
        assert decision.json()["outcome"] == "payout"

        state = client.get("/ui/api/state")
        assert state.status_code == 200
        payload = state.json()
        assert payload["ledger_backend"] == ledger_backend
        assert any(agent["id"] == buyer_id and agent["balance"] == 90 for agent in payload["agents"])
        assert any(agent["id"] == seller_id and agent["balance"] == 10 for agent in payload["agents"])
        assert any(item["id"] == contract_id and item["status"] == "settled" for item in payload["contracts"])


def test_dashboard_ui_returns_404_for_unmanaged_agent(tmp_path: Path) -> None:
    app = create_app(
        db_url=f"sqlite:///{tmp_path / 'dashboard-errors.db'}",
        artifact_dir=tmp_path / "artifacts",
    )

    with TestClient(app) as client:
        response = client.post(
            "/ui/api/listings",
            json={
                "agent_id": "not-managed",
                "kind": "demand",
                "sku": "json_extraction",
                "price_credits": 10,
                "description": "bad",
            },
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Unknown dashboard agent"


def test_dashboard_ui_persists_managed_agent_keys_across_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "dashboard-persist.db"
    store_path = tmp_path / "dashboard-agents.json"
    monkeypatch.setenv("A2A_DASHBOARD_AGENTS_FILE", str(store_path))

    app_first = create_app(
        db_url=f"sqlite:///{db_path}",
        artifact_dir=tmp_path / "artifacts-first",
    )
    with TestClient(app_first) as first:
        buyer = first.post(
            "/ui/api/agents",
            json={
                "name": "buyer-persist",
                "role": "buyer",
            },
        )
        assert buyer.status_code == 201
        buyer_id = buyer.json()["id"]
        faucet = first.post(f"/ui/api/agents/{buyer_id}/faucet", json={"amount": 11})
        assert faucet.status_code == 200
        assert faucet.json()["balance"] == 11

    assert store_path.exists()

    app_second = create_app(
        db_url=f"sqlite:///{db_path}",
        artifact_dir=tmp_path / "artifacts-second",
    )
    with TestClient(app_second) as second:
        state = second.get("/ui/api/state")
        assert state.status_code == 200
        agents = state.json()["agents"]
        row = next(item for item in agents if item["id"] == buyer_id)
        assert row["ui_managed"] is True
        assert "buyer" in row["roles"]

        faucet = second.post(f"/ui/api/agents/{buyer_id}/faucet", json={"amount": 11})
        assert faucet.status_code == 200
        assert faucet.json()["balance"] == 22


def test_dashboard_ui_local_example_runner_endpoints(tmp_path: Path) -> None:
    app = create_app(
        db_url=f"sqlite:///{tmp_path / 'dashboard-local-runner.db'}",
        artifact_dir=tmp_path / "artifacts",
    )
    with TestClient(app) as client:
        examples_response = client.get("/ui/api/local/examples")
        assert examples_response.status_code == 200
        examples = examples_response.json()["examples"]
        assert any(item["role"] == "buyer" for item in examples)
        assert any(item["role"] == "seller" for item in examples)

        chosen = next((item for item in examples if item["id"] == "echo_seller"), None)
        if chosen is None:
            chosen = next(item for item in examples if item["role"] == "seller")

        started = client.post(f"/ui/api/local/examples/{chosen['id']}/start")
        assert started.status_code == 201
        run_id = started.json()["run_id"]
        assert started.json()["status"] in {"running", "exited", "failed"}

        # Allow subprocess to write at least one line when available.
        time.sleep(0.2)

        state = client.get("/ui/api/state")
        assert state.status_code == 200
        assert any(item["run_id"] == run_id for item in state.json()["local_runs"])

        run_log = client.get(f"/ui/api/local/runs/{run_id}/log?tail=50")
        assert run_log.status_code == 200
        assert run_log.json()["run_id"] == run_id

        stopped = client.post(f"/ui/api/local/runs/{run_id}/stop")
        assert stopped.status_code == 200
        assert stopped.json()["run_id"] == run_id
        assert stopped.json()["status"] in {"exited", "failed"}


def test_dashboard_ui_local_example_runner_unknown_ids(tmp_path: Path) -> None:
    app = create_app(
        db_url=f"sqlite:///{tmp_path / 'dashboard-local-errors.db'}",
        artifact_dir=tmp_path / "artifacts",
    )
    with TestClient(app) as client:
        missing_example = client.post("/ui/api/local/examples/not-real/start")
        assert missing_example.status_code == 404
        assert missing_example.json()["detail"] == "Unknown local example"

        missing_run = client.post("/ui/api/local/runs/not-real/stop")
        assert missing_run.status_code == 404
        assert missing_run.json()["detail"] == "Unknown local run"

        missing_log = client.get("/ui/api/local/runs/not-real/log")
        assert missing_log.status_code == 404
        assert missing_log.json()["detail"] == "Unknown local run"
