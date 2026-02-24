import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.sdk import AgentClient


def test_mode_2_evm_rpc_with_anvil(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rpc_url = os.getenv("LEDGER_EVM_RPC_URL", "http://127.0.0.1:8545")
    monkeypatch.setenv("LEDGER_EVM_RPC_URL", rpc_url)

    try:
        app = create_app(
            db_url=f"sqlite:///{tmp_path / 'mode2-rpc.db'}",
            artifact_dir=tmp_path / "artifacts-rpc",
            ledger_backend="evm_rpc",
        )
    except ValueError as exc:
        pytest.skip(f"evm_rpc unavailable: {exc}")

    with TestClient(app) as client:
        buyer = AgentClient.create(client, "buyer-rpc")
        buyer.register()
        buyer.faucet(15)
        assert buyer.balance()["balance"] == 15
