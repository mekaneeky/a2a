from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.sdk import AgentClient, SDKError, create_local_agent


def test_create_local_agent_has_empty_agent_id() -> None:
    agent = create_local_agent("a")
    assert agent.agent_id is None
    assert agent.name == "a"


def test_signed_request_requires_registered_agent(tmp_path: Path) -> None:
    app = create_app(
        db_url=f"sqlite:///{tmp_path / 'sdk-auth.db'}",
        artifact_dir=tmp_path / "artifacts",
    )

    with TestClient(app) as client:
        buyer = AgentClient.create(client, "buyer")
        with pytest.raises(SDKError):
            buyer.balance()


def test_sdk_clients_can_run_explicit_flow_with_override_price(tmp_path: Path) -> None:
    app = create_app(
        db_url=f"sqlite:///{tmp_path / 'sdk-flow.db'}",
        artifact_dir=tmp_path / "artifacts",
    )

    with TestClient(app) as client:
        buyer = AgentClient.create(client, "buyer")
        seller = AgentClient.create(client, "seller")
        buyer.register()
        seller.register()

        buyer.faucet(100)

        demand = buyer.create_listing("demand", "json_extraction", 20, "need json")
        offer = seller.create_listing("offer", "json_extraction", 20, "json available", rfq_enabled=True)

        buyer.match(demand["id"])
        contract = buyer.handshake(
            demand_listing_id=demand["id"],
            offer_listing_id=offer["id"],
            terms="strict json",
            price_credits=17,
        )
        assert contract["price_credits"] == 17

        activated = buyer.activate_contract(contract["id"])
        assert activated["status"] == "active"

        payload = b'{"records":[{"id":1}]}'
        delivered = seller.deliver(contract["id"], payload)
        assert delivered["status"] == "delivered"

        artifact = buyer.get_artifact(contract["id"])
        assert buyer.decrypt_artifact(artifact) == payload

        details = buyer.get_contract(contract["id"])
        assert details["status"] == "delivered"

        decision = buyer.decide(contract["id"], accept=False)
        assert decision["outcome"] == "refund"
        assert buyer.balance()["balance"] == 100
        assert seller.balance()["balance"] == 0


def test_decrypt_artifact_rejects_invalid_payload(tmp_path: Path) -> None:
    app = create_app(
        db_url=f"sqlite:///{tmp_path / 'sdk-invalid-artifact.db'}",
        artifact_dir=tmp_path / "artifacts",
    )

    with TestClient(app) as client:
        buyer = AgentClient.create(client, "buyer")
        with pytest.raises(SDKError):
            buyer.decrypt_artifact({"ciphertext_b64": "%%%bad%%%"})


class _FakeResponse:
    def __init__(self) -> None:
        self._payload = {"id": "agent-1"}

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _FakeHttpClient:
    def __init__(self) -> None:
        self.called = False
        self.last_payload: dict | None = None

    def post(self, _path: str, json: dict):
        self.called = True
        self.last_payload = json
        assert json["name"] == "buyer"
        return _FakeResponse()


def test_agent_client_create_classmethod_and_register_shape() -> None:
    fake_http = _FakeHttpClient()
    client = AgentClient.create(fake_http, "buyer")

    payload = client.register()

    assert fake_http.called
    assert payload["id"] == "agent-1"
    assert client.identity.agent_id == "agent-1"


def test_register_includes_optional_agent_card() -> None:
    fake_http = _FakeHttpClient()
    client = AgentClient.create(fake_http, "buyer")

    client.register(agent_card={"skus": ["json_extraction"], "capabilities": ["verifiable_output"], "tags": ["trusted"]})

    assert fake_http.last_payload is not None
    assert fake_http.last_payload["agent_card"]["skus"] == ["json_extraction"]


def test_sdk_list_contracts_validates_role(tmp_path: Path) -> None:
    app = create_app(
        db_url=f"sqlite:///{tmp_path / 'sdk-role.db'}",
        artifact_dir=tmp_path / "artifacts",
    )

    with TestClient(app) as client:
        buyer = AgentClient.create(client, "buyer")
        buyer.register()
        with pytest.raises(SDKError):
            buyer.list_contracts(role="invalid")


def test_sdk_search_sellers_uses_default_filters(tmp_path: Path) -> None:
    app = create_app(
        db_url=f"sqlite:///{tmp_path / 'sdk-search.db'}",
        artifact_dir=tmp_path / "artifacts",
    )

    with TestClient(app) as client:
        buyer = AgentClient.create(client, "buyer")
        seller = AgentClient.create(client, "seller")
        buyer.register()
        seller.register(agent_card={"skus": ["json_extraction"]})
        seller.create_listing("offer", "json_extraction", 5, "offer")

        search = buyer.search_sellers(sku="json_extraction")

        assert len(search["results"]) == 1
        assert search["results"][0]["seller"]["name"] == "seller"
