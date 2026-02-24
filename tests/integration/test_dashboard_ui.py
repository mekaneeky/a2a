import json
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
