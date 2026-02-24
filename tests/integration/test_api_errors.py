import base64
import json
import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.main import create_app
from app.models import Agent, Artifact, Contract, ContractStatus
from app.security import (
    canonical_request_message,
    generate_encryption_keypair,
    generate_signing_keypair,
    sign_message,
)


def _register_agent(client: TestClient, name: str) -> dict[str, str]:
    sign_private, sign_public = generate_signing_keypair()
    enc_private, enc_public = generate_encryption_keypair()
    response = client.post(
        "/agents/register",
        json={"name": name, "public_sign_key": sign_public, "public_encrypt_key": enc_public},
    )
    assert response.status_code == 201
    return {
        "agent_id": response.json()["id"],
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
    signing_private_key: str | None = None,
    agent_id_override: str | None = None,
):
    body = b""
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    final_timestamp = timestamp or str(int(time.time()))
    final_nonce = nonce or str(uuid4())
    message = canonical_request_message(method, path, final_timestamp, final_nonce, body)
    signature = sign_message(signing_private_key or agent["sign_private"], message)

    headers = {
        "x-agent-id": agent_id_override or agent["agent_id"],
        "x-timestamp": final_timestamp,
        "x-nonce": final_nonce,
        "x-signature": signature,
    }

    if payload is None:
        return client.request(method, path, headers=headers)
    return client.request(method, path, headers={**headers, "content-type": "application/json"}, content=body)


def _bootstrap_active_contract(client: TestClient, sku: str = "json_extraction", price: int = 10) -> dict:
    buyer = _register_agent(client, f"buyer-{uuid4().hex[:6]}")
    seller = _register_agent(client, f"seller-{uuid4().hex[:6]}")
    _signed_request(client, buyer, "POST", "/ledger/faucet", {"amount": 100}).raise_for_status()

    demand = _signed_request(
        client,
        buyer,
        "POST",
        "/listings",
        {"kind": "demand", "sku": sku, "price_credits": price, "description": "need"},
    )
    offer = _signed_request(
        client,
        seller,
        "POST",
        "/listings",
        {"kind": "offer", "sku": sku, "price_credits": price, "description": "have"},
    )
    contract = _signed_request(
        client,
        buyer,
        "POST",
        "/contracts/handshake",
        {
            "demand_listing_id": demand.json()["id"],
            "offer_listing_id": offer.json()["id"],
            "terms": "terms",
        },
    )
    _signed_request(client, buyer, "POST", f"/contracts/{contract.json()['id']}/activate").raise_for_status()
    return {
        "buyer": buyer,
        "seller": seller,
        "demand": demand.json(),
        "offer": offer.json(),
        "contract_id": contract.json()["id"],
    }


def test_health_duplicate_register_and_missing_headers(tmp_path: Path) -> None:
    app = create_app(db_url=f"sqlite:///{tmp_path / 'a.db'}", artifact_dir=tmp_path / "artifacts")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

        sign_private, sign_public = generate_signing_keypair()
        _, enc_public = generate_encryption_keypair()
        payload = {"name": "dup", "public_sign_key": sign_public, "public_encrypt_key": enc_public}

        first = client.post("/agents/register", json=payload)
        second = client.post("/agents/register", json=payload)

        assert first.status_code == 201
        assert sign_private
        assert second.status_code == 409
        assert client.get("/ledger/balance").status_code == 401


def test_auth_error_paths(tmp_path: Path) -> None:
    app = create_app(db_url=f"sqlite:///{tmp_path / 'b.db'}", artifact_dir=tmp_path / "artifacts")
    with TestClient(app) as client:
        buyer = _register_agent(client, "buyer")

        invalid_ts = _signed_request(client, buyer, "GET", "/ledger/balance", timestamp="not-an-int")
        stale = _signed_request(client, buyer, "GET", "/ledger/balance", timestamp="100")

        other_private, _ = generate_signing_keypair()
        bad_sig = _signed_request(
            client,
            buyer,
            "GET",
            "/ledger/balance",
            signing_private_key=other_private,
        )

        fake_private, _ = generate_signing_keypair()
        unknown = _signed_request(
            client,
            {"agent_id": "ghost", "sign_private": fake_private},
            "GET",
            "/ledger/balance",
            agent_id_override="ghost",
        )

        assert invalid_ts.status_code == 401
        assert stale.status_code == 401
        assert bad_sig.status_code == 401
        assert unknown.status_code == 401


def test_match_error_paths(tmp_path: Path) -> None:
    app = create_app(db_url=f"sqlite:///{tmp_path / 'c.db'}", artifact_dir=tmp_path / "artifacts")
    with TestClient(app) as client:
        buyer = _register_agent(client, "buyer")
        seller = _register_agent(client, "seller")

        _signed_request(client, buyer, "POST", "/ledger/faucet", {"amount": 30}).raise_for_status()

        demand = _signed_request(
            client,
            buyer,
            "POST",
            "/listings",
            {"kind": "demand", "sku": "json_extraction", "price_credits": 10, "description": "need"},
        ).json()
        offer = _signed_request(
            client,
            seller,
            "POST",
            "/listings",
            {"kind": "offer", "sku": "json_extraction", "price_credits": 10, "description": "have"},
        ).json()

        wrong_kind = _signed_request(client, buyer, "POST", "/match", {"demand_listing_id": offer["id"]})
        not_owner = _signed_request(client, seller, "POST", "/match", {"demand_listing_id": demand["id"]})

        no_offer_demand = _signed_request(
            client,
            buyer,
            "POST",
            "/listings",
            {"kind": "demand", "sku": "dataset_csv", "price_credits": 5, "description": "no offer"},
        ).json()
        no_offer = _signed_request(client, buyer, "POST", "/match", {"demand_listing_id": no_offer_demand["id"]})

        assert wrong_kind.status_code == 404
        assert not_owner.status_code == 403
        assert no_offer.status_code == 404


def test_handshake_error_paths(tmp_path: Path) -> None:
    app = create_app(db_url=f"sqlite:///{tmp_path / 'd.db'}", artifact_dir=tmp_path / "artifacts")
    with TestClient(app) as client:
        buyer = _register_agent(client, "buyer")
        seller = _register_agent(client, "seller")
        _signed_request(client, buyer, "POST", "/ledger/faucet", {"amount": 50}).raise_for_status()

        demand = _signed_request(
            client,
            buyer,
            "POST",
            "/listings",
            {"kind": "demand", "sku": "json_extraction", "price_credits": 20, "description": "need"},
        ).json()
        offer = _signed_request(
            client,
            seller,
            "POST",
            "/listings",
            {"kind": "offer", "sku": "json_extraction", "price_credits": 20, "description": "have"},
        ).json()

        not_found = _signed_request(
            client,
            buyer,
            "POST",
            "/contracts/handshake",
            {"demand_listing_id": "x", "offer_listing_id": "y", "terms": "t"},
        )
        invalid_kinds = _signed_request(
            client,
            buyer,
            "POST",
            "/contracts/handshake",
            {
                "demand_listing_id": demand["id"],
                "offer_listing_id": demand["id"],
                "terms": "t",
            },
        )
        forbidden = _signed_request(
            client,
            seller,
            "POST",
            "/contracts/handshake",
            {
                "demand_listing_id": demand["id"],
                "offer_listing_id": offer["id"],
                "terms": "t",
            },
        )
        mismatch_offer = _signed_request(
            client,
            seller,
            "POST",
            "/listings",
            {"kind": "offer", "sku": "dataset_csv", "price_credits": 20, "description": "other"},
        ).json()
        mismatch = _signed_request(
            client,
            buyer,
            "POST",
            "/contracts/handshake",
            {
                "demand_listing_id": demand["id"],
                "offer_listing_id": mismatch_offer["id"],
                "terms": "t",
            },
        )
        override_price = _signed_request(
            client,
            buyer,
            "POST",
            "/contracts/handshake",
            {
                "demand_listing_id": demand["id"],
                "offer_listing_id": offer["id"],
                "terms": "t",
                "price_credits": 15,
            },
        )

        assert not_found.status_code == 404
        assert invalid_kinds.status_code == 400
        assert forbidden.status_code == 403
        assert mismatch.status_code == 400
        assert override_price.status_code == 201
        assert override_price.json()["price_credits"] == 15


def test_activate_error_paths(tmp_path: Path) -> None:
    app = create_app(db_url=f"sqlite:///{tmp_path / 'e.db'}", artifact_dir=tmp_path / "artifacts")
    with TestClient(app) as client:
        context = _bootstrap_active_contract(client)

        not_found = _signed_request(client, context["buyer"], "POST", "/contracts/missing/activate")
        forbidden = _signed_request(client, context["seller"], "POST", f"/contracts/{context['contract_id']}/activate")
        wrong_state = _signed_request(client, context["buyer"], "POST", f"/contracts/{context['contract_id']}/activate")

        assert not_found.status_code == 404
        assert forbidden.status_code == 403
        assert wrong_state.status_code == 409


def test_deliver_error_paths_and_artifact_overwrite(tmp_path: Path) -> None:
    app = create_app(db_url=f"sqlite:///{tmp_path / 'f.db'}", artifact_dir=tmp_path / "artifacts")
    with TestClient(app) as client:
        ctx = _bootstrap_active_contract(client)

        not_found = _signed_request(
            client,
            ctx["seller"],
            "POST",
            "/contracts/missing/deliver",
            {"payload_b64": base64.b64encode(b"{}").decode("ascii")},
        )
        forbidden = _signed_request(
            client,
            ctx["buyer"],
            "POST",
            f"/contracts/{ctx['contract_id']}/deliver",
            {"payload_b64": base64.b64encode(b"{}").decode("ascii")},
        )

        with Session(client.app.state.engine) as session:
            contract = session.get(Contract, ctx["contract_id"])
            assert contract is not None
            contract.status = ContractStatus.PROPOSED
            session.add(contract)
            session.commit()

        wrong_state = _signed_request(
            client,
            ctx["seller"],
            "POST",
            f"/contracts/{ctx['contract_id']}/deliver",
            {"payload_b64": base64.b64encode(b"{}").decode("ascii")},
        )

        with Session(client.app.state.engine) as session:
            contract = session.get(Contract, ctx["contract_id"])
            assert contract is not None
            contract.status = ContractStatus.ACTIVE
            session.add(contract)
            session.commit()

        bad_b64 = _signed_request(
            client,
            ctx["seller"],
            "POST",
            f"/contracts/{ctx['contract_id']}/deliver",
            {"payload_b64": "%%%bad%%%"},
        )

        with Session(client.app.state.engine) as session:
            contract = session.get(Contract, ctx["contract_id"])
            assert contract is not None
            contract.sku = "unknown"
            session.add(contract)
            session.commit()

        unknown_sku = _signed_request(
            client,
            ctx["seller"],
            "POST",
            f"/contracts/{ctx['contract_id']}/deliver",
            {"payload_b64": base64.b64encode(b"{}").decode("ascii")},
        )

        with Session(client.app.state.engine) as session:
            contract = session.get(Contract, ctx["contract_id"])
            assert contract is not None
            contract.sku = "json_extraction"
            session.add(contract)
            session.commit()

        buyer_missing_ctx = _bootstrap_active_contract(client)
        with Session(client.app.state.engine) as session:
            contract = session.get(Contract, buyer_missing_ctx["contract_id"])
            assert contract is not None
            buyer = session.get(Agent, contract.buyer_id)
            assert buyer is not None
            session.delete(buyer)
            session.commit()

        buyer_missing = _signed_request(
            client,
            buyer_missing_ctx["seller"],
            "POST",
            f"/contracts/{buyer_missing_ctx['contract_id']}/deliver",
            {"payload_b64": base64.b64encode(b'{"ok":true}').decode("ascii")},
        )

        update_ctx = _bootstrap_active_contract(client)
        first_delivery = _signed_request(
            client,
            update_ctx["seller"],
            "POST",
            f"/contracts/{update_ctx['contract_id']}/deliver",
            {"payload_b64": base64.b64encode(b'{"ok":true}').decode("ascii")},
        )
        with Session(client.app.state.engine) as session:
            contract = session.get(Contract, update_ctx["contract_id"])
            assert contract is not None
            contract.status = ContractStatus.ACTIVE
            session.add(contract)
            session.commit()
        second_delivery = _signed_request(
            client,
            update_ctx["seller"],
            "POST",
            f"/contracts/{update_ctx['contract_id']}/deliver",
            {"payload_b64": base64.b64encode(b'{"ok":true,"v":2}').decode("ascii")},
        )

        assert not_found.status_code == 404
        assert forbidden.status_code == 403
        assert wrong_state.status_code == 409
        assert bad_b64.status_code == 400
        assert unknown_sku.status_code == 400
        assert buyer_missing.status_code == 404
        assert first_delivery.status_code == 200
        assert second_delivery.status_code == 200


def test_fetch_artifact_error_paths(tmp_path: Path) -> None:
    app = create_app(db_url=f"sqlite:///{tmp_path / 'g.db'}", artifact_dir=tmp_path / "artifacts")
    with TestClient(app) as client:
        ctx = _bootstrap_active_contract(client)

        not_found = _signed_request(client, ctx["buyer"], "GET", "/contracts/missing/artifact")
        forbidden = _signed_request(client, ctx["seller"], "GET", f"/contracts/{ctx['contract_id']}/artifact")
        unavailable = _signed_request(client, ctx["buyer"], "GET", f"/contracts/{ctx['contract_id']}/artifact")

        with Session(client.app.state.engine) as session:
            contract = session.get(Contract, ctx["contract_id"])
            assert contract is not None
            contract.status = ContractStatus.DELIVERED
            session.add(contract)
            session.commit()

        missing_row = _signed_request(client, ctx["buyer"], "GET", f"/contracts/{ctx['contract_id']}/artifact")

        with Session(client.app.state.engine) as session:
            artifact = Artifact(
                contract_id=ctx["contract_id"],
                storage_path=str(tmp_path / "nope.bin"),
                sha256="x",
                plaintext_sha256="y",
            )
            session.add(artifact)
            session.commit()

        missing_file = _signed_request(client, ctx["buyer"], "GET", f"/contracts/{ctx['contract_id']}/artifact")

        assert not_found.status_code == 404
        assert forbidden.status_code == 403
        assert unavailable.status_code == 409
        assert missing_row.status_code == 404
        assert missing_file.status_code == 404


def test_decision_and_get_contract_error_paths(tmp_path: Path) -> None:
    app = create_app(db_url=f"sqlite:///{tmp_path / 'h.db'}", artifact_dir=tmp_path / "artifacts")
    with TestClient(app) as client:
        ctx = _bootstrap_active_contract(client)

        # Deliver first so decision endpoints can be exercised.
        _signed_request(
            client,
            ctx["seller"],
            "POST",
            f"/contracts/{ctx['contract_id']}/deliver",
            {"payload_b64": base64.b64encode(b'{"ok":true}').decode("ascii")},
        ).raise_for_status()

        not_found = _signed_request(
            client,
            ctx["buyer"],
            "POST",
            "/contracts/missing/decision",
            {"accept": True},
        )
        forbidden = _signed_request(
            client,
            ctx["seller"],
            "POST",
            f"/contracts/{ctx['contract_id']}/decision",
            {"accept": True},
        )

        reject_ctx = _bootstrap_active_contract(client)
        _signed_request(
            client,
            reject_ctx["seller"],
            "POST",
            f"/contracts/{reject_ctx['contract_id']}/deliver",
            {"payload_b64": base64.b64encode(b'{"ok":true}').decode("ascii")},
        ).raise_for_status()
        reject = _signed_request(
            client,
            reject_ctx["buyer"],
            "POST",
            f"/contracts/{reject_ctx['contract_id']}/decision",
            {"accept": False},
        )

        missing_seller_ctx = _bootstrap_active_contract(client)
        _signed_request(
            client,
            missing_seller_ctx["seller"],
            "POST",
            f"/contracts/{missing_seller_ctx['contract_id']}/deliver",
            {"payload_b64": base64.b64encode(b'{"ok":true}').decode("ascii")},
        ).raise_for_status()
        with Session(client.app.state.engine) as session:
            contract = session.get(Contract, missing_seller_ctx["contract_id"])
            assert contract is not None
            seller = session.get(Agent, contract.seller_id)
            assert seller is not None
            session.delete(seller)
            session.commit()

        missing_seller = _signed_request(
            client,
            missing_seller_ctx["buyer"],
            "POST",
            f"/contracts/{missing_seller_ctx['contract_id']}/decision",
            {"accept": True},
        )

        missing_seller_reject_ctx = _bootstrap_active_contract(client)
        _signed_request(
            client,
            missing_seller_reject_ctx["seller"],
            "POST",
            f"/contracts/{missing_seller_reject_ctx['contract_id']}/deliver",
            {"payload_b64": base64.b64encode(b'{"ok":true}').decode("ascii")},
        ).raise_for_status()
        with Session(client.app.state.engine) as session:
            contract = session.get(Contract, missing_seller_reject_ctx["contract_id"])
            assert contract is not None
            seller = session.get(Agent, contract.seller_id)
            assert seller is not None
            session.delete(seller)
            session.commit()

        missing_seller_reject = _signed_request(
            client,
            missing_seller_reject_ctx["buyer"],
            "POST",
            f"/contracts/{missing_seller_reject_ctx['contract_id']}/decision",
            {"accept": False},
        )

        contract_not_found = _signed_request(client, ctx["buyer"], "GET", "/contracts/missing")
        third = _register_agent(client, "third")
        contract_forbidden = _signed_request(client, third, "GET", f"/contracts/{ctx['contract_id']}")
        contract_ok = _signed_request(client, ctx["buyer"], "GET", f"/contracts/{ctx['contract_id']}")

        assert not_found.status_code == 404
        assert forbidden.status_code == 403
        assert reject.status_code == 200
        assert reject.json()["outcome"] == "refund"
        assert missing_seller.status_code == 404
        assert missing_seller_reject.status_code == 404
        assert contract_not_found.status_code == 404
        assert contract_forbidden.status_code == 403
        assert contract_ok.status_code == 200
