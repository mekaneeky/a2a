import base64
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import create_app
from app.models import AgentCard, AgentHeartbeat, Listing
from app.security import (
    canonical_request_message,
    generate_encryption_keypair,
    generate_signing_keypair,
    sign_message,
)
from app.sdk import AgentClient


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(
        db_url=f"sqlite:///{tmp_path / 'test.db'}",
        artifact_dir=tmp_path / "artifacts",
    )
    with TestClient(app) as test_client:
        yield test_client


def _register_agent(client: TestClient, name: str) -> dict[str, str]:
    sign_private, sign_public = generate_signing_keypair()
    enc_private, enc_public = generate_encryption_keypair()
    response = client.post(
        "/agents/register",
        json={
            "name": name,
            "public_sign_key": sign_public,
            "public_encrypt_key": enc_public,
        },
    )
    assert response.status_code == 201
    agent = response.json()
    return {
        "agent_id": agent["id"],
        "name": name,
        "sign_private": sign_private,
        "sign_public": sign_public,
        "enc_private": enc_private,
        "enc_public": enc_public,
    }


def _signed_request(
    client: TestClient,
    agent: dict[str, str],
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    nonce: str | None = None,
    timestamp: str | None = None,
):
    body = b""
    headers = {
        "x-agent-id": agent["agent_id"],
        "x-timestamp": timestamp or str(int(time.time())),
        "x-nonce": nonce or str(uuid4()),
    }
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    message = canonical_request_message(
        method=method,
        path=path,
        timestamp=headers["x-timestamp"],
        nonce=headers["x-nonce"],
        body=body,
    )
    headers["x-signature"] = sign_message(agent["sign_private"], message)

    if payload is None:
        return client.request(method, path, headers=headers)
    return client.request(method, path, headers={**headers, "content-type": "application/json"}, content=body)


def test_full_contract_flow_success(client: TestClient) -> None:
    buyer = _register_agent(client, "buyer")
    seller = _register_agent(client, "seller")

    faucet = _signed_request(client, buyer, "POST", "/ledger/faucet", {"amount": 100})
    assert faucet.status_code == 200

    demand = _signed_request(
        client,
        buyer,
        "POST",
        "/listings",
        {"kind": "demand", "sku": "json_extraction", "price_credits": 30, "description": "Need JSON"},
    )
    assert demand.status_code == 201

    offer = _signed_request(
        client,
        seller,
        "POST",
        "/listings",
        {"kind": "offer", "sku": "json_extraction", "price_credits": 30, "description": "Can do"},
    )
    assert offer.status_code == 201

    match = _signed_request(client, buyer, "POST", "/match", {"demand_listing_id": demand.json()["id"]})
    assert match.status_code == 200
    assert match.json()["offer_listing_id"] == offer.json()["id"]

    contract = _signed_request(
        client,
        buyer,
        "POST",
        "/contracts/handshake",
        {
            "demand_listing_id": demand.json()["id"],
            "offer_listing_id": offer.json()["id"],
            "terms": "deliver strict JSON",
        },
    )
    assert contract.status_code == 201
    contract_id = contract.json()["id"]

    activate = _signed_request(client, buyer, "POST", f"/contracts/{contract_id}/activate")
    assert activate.status_code == 200
    assert activate.json()["status"] == "active"

    payload = base64.b64encode(b'{"records":[{"id":1,"name":"alice"}]}').decode("ascii")
    deliver = _signed_request(
        client,
        seller,
        "POST",
        f"/contracts/{contract_id}/deliver",
        {"payload_b64": payload},
    )
    assert deliver.status_code == 200
    assert deliver.json()["status"] == "delivered"

    artifact = _signed_request(client, buyer, "GET", f"/contracts/{contract_id}/artifact")
    assert artifact.status_code == 200
    assert artifact.json()["ciphertext_b64"]
    assert artifact.json()["sha256"]

    decision = _signed_request(
        client,
        buyer,
        "POST",
        f"/contracts/{contract_id}/decision",
        {"accept": True},
    )
    assert decision.status_code == 200
    assert decision.json()["status"] == "settled"

    buyer_balance = _signed_request(client, buyer, "GET", "/ledger/balance")
    seller_balance = _signed_request(client, seller, "GET", "/ledger/balance")
    assert buyer_balance.status_code == 200
    assert seller_balance.status_code == 200
    assert buyer_balance.json()["balance"] == 70
    assert seller_balance.json()["balance"] == 30


def test_auto_refund_when_delivery_verification_fails(client: TestClient) -> None:
    buyer = _register_agent(client, "buyer")
    seller = _register_agent(client, "seller")

    assert _signed_request(client, buyer, "POST", "/ledger/faucet", {"amount": 20}).status_code == 200
    demand = _signed_request(
        client,
        buyer,
        "POST",
        "/listings",
        {"kind": "demand", "sku": "dataset_csv", "price_credits": 20, "description": "Need CSV"},
    )
    offer = _signed_request(
        client,
        seller,
        "POST",
        "/listings",
        {"kind": "offer", "sku": "dataset_csv", "price_credits": 20, "description": "CSV"},
    )
    contract = _signed_request(
        client,
        buyer,
        "POST",
        "/contracts/handshake",
        {
            "demand_listing_id": demand.json()["id"],
            "offer_listing_id": offer.json()["id"],
            "terms": "valid csv only",
        },
    )
    contract_id = contract.json()["id"]
    assert _signed_request(client, buyer, "POST", f"/contracts/{contract_id}/activate").status_code == 200

    invalid_payload = base64.b64encode(b"id,name\n").decode("ascii")
    deliver = _signed_request(
        client,
        seller,
        "POST",
        f"/contracts/{contract_id}/deliver",
        {"payload_b64": invalid_payload},
    )
    assert deliver.status_code == 200
    assert deliver.json()["status"] == "settled"
    assert deliver.json()["outcome"] == "refund"

    buyer_balance = _signed_request(client, buyer, "GET", "/ledger/balance")
    seller_balance = _signed_request(client, seller, "GET", "/ledger/balance")
    assert buyer_balance.json()["balance"] == 20
    assert seller_balance.json()["balance"] == 0

    decision = _signed_request(
        client,
        buyer,
        "POST",
        f"/contracts/{contract_id}/decision",
        {"accept": True},
    )
    assert decision.status_code == 409


def test_reused_nonce_is_rejected(client: TestClient) -> None:
    buyer = _register_agent(client, "buyer")
    fixed_nonce = "nonce-1"

    first = _signed_request(client, buyer, "POST", "/ledger/faucet", {"amount": 5}, nonce=fixed_nonce)
    second = _signed_request(client, buyer, "POST", "/ledger/faucet", {"amount": 5}, nonce=fixed_nonce)

    assert first.status_code == 200
    assert second.status_code == 409


def test_sdk_two_agent_clients_trade(client: TestClient) -> None:
    buyer = AgentClient.create(client, "buyer")
    seller = AgentClient.create(client, "seller")
    buyer.register()
    seller.register()

    # Unfiltered relay query branch coverage.
    assert buyer.list_listings(active=None, limit=0, offset=-10) == []
    assert buyer.list_contracts(limit=0, offset=-10) == []

    buyer.faucet(100)
    demand = buyer.create_listing("demand", "api_call", 15, "Need 2xx call")
    relay_demands = seller.list_listings(kind="demand", sku="api_call", active=True)
    assert any(item["id"] == demand["id"] for item in relay_demands)
    offer = seller.create_listing("offer", "api_call", 15, "Can execute call")

    match = buyer.match(demand["id"])
    assert match["offer_listing_id"] == offer["id"]

    contract = buyer.handshake(demand["id"], offer["id"], "return 2xx status")
    buyer.activate_contract(contract["id"])
    buyer_active_contracts = buyer.list_contracts(role="buyer", status="active")
    assert any(item["id"] == contract["id"] for item in buyer_active_contracts)
    relay_contracts = seller.list_contracts(role="seller", status="active")
    assert any(item["id"] == contract["id"] for item in relay_contracts)
    invalid_role = buyer.signed_request("GET", "/contracts", params={"role": "invalid"})
    assert invalid_role.status_code == 400

    delivered = seller.deliver(contract["id"], b'{"status_code":200,"response":{"ok":true}}')
    assert delivered["status"] == "delivered"

    artifact = buyer.get_artifact(contract["id"])
    plaintext = buyer.decrypt_artifact(artifact)
    assert plaintext == b'{"status_code":200,"response":{"ok":true}}'

    decision = buyer.decide(contract["id"], accept=True)
    assert decision["status"] == "settled"
    assert buyer.balance()["balance"] == 85
    assert seller.balance()["balance"] == 15


def test_buyer_searches_sellers_by_declared_agent_card(client: TestClient) -> None:
    buyer = AgentClient.create(client, "buyer-search")
    buyer.register()

    seller_match = AgentClient.create(client, "seller-match")
    seller_match.register(
        agent_card={
            "skus": [" JSON_Extraction "],
            "capabilities": [" Verifiable_Output "],
            "tags": ["Trusted "],
            "description": "best match",
        }
    )
    seller_missing = AgentClient.create(client, "seller-missing")
    seller_missing.register(
        agent_card={
            "skus": ["json_extraction"],
            "capabilities": ["basic_output"],
            "tags": ["cheap"],
        }
    )
    seller_highprice = AgentClient.create(client, "seller-highprice")
    seller_highprice.register(
        agent_card={
            "skus": ["json_extraction"],
            "capabilities": ["verifiable_output"],
            "tags": ["trusted"],
        }
    )
    seller_no_card = AgentClient.create(client, "seller-no-card")
    seller_no_card.register()

    seller_match.create_listing("offer", "json_extraction", 9, "match")
    seller_missing.create_listing("offer", "json_extraction", 8, "missing")
    seller_highprice.create_listing("offer", "json_extraction", 20, "high")
    seller_highprice_orphan = seller_highprice.create_listing("offer", "json_extraction", 21, "high-orphan")
    seller_no_card.create_listing("offer", "json_extraction", 7, "no card")

    with Session(client.app.state.engine) as session:
        # Cover defensive branch: dangling offer with missing seller should be ignored.
        orphan_offer = session.get(Listing, seller_highprice_orphan["id"])
        assert orphan_offer is not None
        orphan_offer.agent_id = "missing-seller"
        session.add(orphan_offer)

        # Cover missing-card branch by deleting one seller card row.
        card_row = session.exec(
            select(AgentCard).where(AgentCard.agent_id == seller_no_card.identity.agent_id)
        ).first()
        assert card_row is not None
        session.delete(card_row)
        session.commit()

    search_all = buyer.search_sellers(
        sku="json_extraction",
        required_capabilities=["verifiable_output", "VERIFIABLE_OUTPUT"],
        required_tags=["trusted"],
        max_price_credits=10,
        include_non_matching=True,
    )
    assert search_all["requirements"]["required_capabilities"] == ["verifiable_output"]
    assert search_all["requirements"]["required_tags"] == ["trusted"]
    assert len(search_all["results"]) == 4

    by_name = {item["seller"]["name"]: item for item in search_all["results"]}
    assert by_name["seller-match"]["card_match"]
    assert by_name["seller-match"]["reasons"] == []

    assert not by_name["seller-missing"]["card_match"]
    assert "missing_capabilities:verifiable_output" in by_name["seller-missing"]["reasons"]
    assert "missing_tags:trusted" in by_name["seller-missing"]["reasons"]

    assert not by_name["seller-highprice"]["card_match"]
    assert "price_too_high" in by_name["seller-highprice"]["reasons"]

    assert not by_name["seller-no-card"]["card_match"]
    assert "sku_not_declared" in by_name["seller-no-card"]["reasons"]

    search_matching_only = buyer.search_sellers(
        sku="json_extraction",
        required_capabilities=["verifiable_output"],
        required_tags=["trusted"],
        max_price_credits=10,
        include_non_matching=False,
    )
    assert len(search_matching_only["results"]) == 1
    assert search_matching_only["results"][0]["seller"]["name"] == "seller-match"

    search_reputation = buyer.search_sellers(
        sku="json_extraction",
        required_capabilities=["verifiable_output"],
        required_tags=["trusted"],
        min_reputation=1,
        include_non_matching=True,
    )
    rep_by_name = {item["seller"]["name"]: item for item in search_reputation["results"]}
    assert "reputation_too_low" in rep_by_name["seller-match"]["reasons"]


def test_buyer_search_can_require_online_sellers(client: TestClient) -> None:
    buyer = AgentClient.create(client, "buyer-online")
    buyer.register()

    seller_online = AgentClient.create(client, "seller-online")
    seller_online.register(
        agent_card={
            "skus": ["json_extraction"],
            "capabilities": ["verifiable_output"],
            "tags": ["trusted"],
        }
    )
    seller_offline = AgentClient.create(client, "seller-offline")
    seller_offline.register(
        agent_card={
            "skus": ["json_extraction"],
            "capabilities": ["verifiable_output"],
            "tags": ["trusted"],
        }
    )

    seller_online.create_listing("offer", "json_extraction", 9, "online")
    seller_offline.create_listing("offer", "json_extraction", 8, "offline")

    heartbeat = seller_online.heartbeat()
    assert heartbeat["agent_id"] == seller_online.identity.agent_id
    assert heartbeat["last_seen_at"]

    search = buyer.search_sellers(
        sku="json_extraction",
        required_capabilities=["verifiable_output"],
        required_tags=["trusted"],
        require_online=True,
        online_within_seconds=120,
        include_non_matching=True,
    )
    assert search["requirements"]["require_online"] is True
    assert search["requirements"]["online_within_seconds"] == 120

    by_name = {item["seller"]["name"]: item for item in search["results"]}
    assert by_name["seller-online"]["card_match"] is True
    assert "seller_offline" not in by_name["seller-online"]["reasons"]
    assert by_name["seller-offline"]["card_match"] is False
    assert "seller_offline" in by_name["seller-offline"]["reasons"]

    matching_only = buyer.search_sellers(
        sku="json_extraction",
        required_capabilities=["verifiable_output"],
        required_tags=["trusted"],
        require_online=True,
        online_within_seconds=120,
        include_non_matching=False,
    )
    assert [item["seller"]["name"] for item in matching_only["results"]] == ["seller-online"]

    with Session(client.app.state.engine) as session:
        online_row = session.exec(
            select(AgentHeartbeat).where(AgentHeartbeat.agent_id == seller_online.identity.agent_id)
        ).first()
        assert online_row is not None
        online_row.last_seen_at = datetime.now(timezone.utc) - timedelta(seconds=3600)
        session.add(online_row)
        session.commit()

    stale_search = buyer.search_sellers(
        sku="json_extraction",
        required_capabilities=["verifiable_output"],
        required_tags=["trusted"],
        require_online=True,
        online_within_seconds=30,
        include_non_matching=True,
    )
    stale_by_name = {item["seller"]["name"]: item for item in stale_search["results"]}
    assert "seller_offline" in stale_by_name["seller-online"]["reasons"]
