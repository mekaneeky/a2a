from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.sdk import AgentClient


def test_mode_1_db_backend_interface_switch(tmp_path: Path) -> None:
    app = create_app(
        db_url=f"sqlite:///{tmp_path / 'mode1.db'}",
        artifact_dir=tmp_path / "artifacts1",
        ledger_backend="db",
    )

    with TestClient(app) as client:
        assert client.app.state.ledger_backend_name == "db"

        buyer = AgentClient.create(client, "buyer-mode1")
        buyer.register()
        buyer.faucet(25)
        assert buyer.balance()["balance"] == 25


def test_mode_2_evm_local_faucet_and_balance(tmp_path: Path) -> None:
    app = create_app(
        db_url=f"sqlite:///{tmp_path / 'mode2.db'}",
        artifact_dir=tmp_path / "artifacts2",
        ledger_backend="evm_local",
    )

    with TestClient(app) as client:
        assert client.app.state.ledger_backend_name == "evm_local"

        buyer = AgentClient.create(client, "buyer-mode2")
        buyer.register()
        buyer.faucet(40)
        assert buyer.balance()["balance"] == 40


def test_mode_3_evm_local_contract_escrow_and_settlement(tmp_path: Path) -> None:
    app = create_app(
        db_url=f"sqlite:///{tmp_path / 'mode3.db'}",
        artifact_dir=tmp_path / "artifacts3",
        ledger_backend="evm_local",
    )

    with TestClient(app) as client:
        buyer = AgentClient.create(client, "buyer-mode3")
        seller = AgentClient.create(client, "seller-mode3")
        buyer.register()
        seller.register()

        buyer.faucet(100)
        demand = buyer.create_listing("demand", "json_extraction", 10, "need output")
        offer = seller.create_listing("offer", "json_extraction", 10, "can deliver")

        contract = buyer.handshake(demand["id"], offer["id"], "must be valid json")
        buyer.activate_contract(contract["id"])
        seller.deliver(contract["id"], b'{"records":[{"id":1}]}')
        decision = buyer.decide(contract["id"], accept=True)

        assert decision["outcome"] == "payout"
        assert buyer.balance()["balance"] == 90
        assert seller.balance()["balance"] == 10
