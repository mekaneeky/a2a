from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.services import ledger_backend


def test_build_ledger_backend_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="Unsupported ledger backend"):
        ledger_backend.build_ledger_backend("unknown")


def test_evm_local_backend_covers_transfer_paths() -> None:
    backend = ledger_backend.build_ledger_backend("evm_local")

    buyer = backend.agent_account("buyer")
    seller = backend.agent_account("seller")
    reserve_id = "contract-reserve-1"
    reserve_account = backend.escrow_account(reserve_id)

    faucet_tx = backend.post_transfer(
        None,
        from_account="mint",
        to_account=buyer,
        amount=10,
        reason="faucet",
        contract_id=None,
        allow_overdraft=True,
    )
    assert faucet_tx.startswith("0x")
    assert backend.get_balance(None, buyer) == 10

    reserve_tx = backend.post_transfer(
        None,
        from_account=buyer,
        to_account=reserve_account,
        amount=4,
        reason="reserve",
        contract_id=reserve_id,
    )
    assert reserve_tx.startswith("0x")
    assert backend.get_balance(None, buyer) == 6
    assert backend.get_balance(None, reserve_account) == 4

    payout_tx = backend.post_transfer(
        None,
        from_account=reserve_account,
        to_account=seller,
        amount=4,
        reason="payout",
        contract_id=reserve_id,
    )
    assert payout_tx.startswith("0x")
    assert backend.get_balance(None, reserve_account) == 0
    assert backend.get_balance(None, seller) == 4

    refund_id = "contract-refund-1"
    refund_account = backend.escrow_account(refund_id)
    backend.post_transfer(
        None,
        from_account=buyer,
        to_account=refund_account,
        amount=2,
        reason="reserve",
        contract_id=refund_id,
    )
    refund_tx = backend.post_transfer(
        None,
        from_account=refund_account,
        to_account=buyer,
        amount=2,
        reason="refund",
        contract_id=refund_id,
    )
    assert refund_tx.startswith("0x")

    auto_refund_id = "contract-refund-2"
    auto_refund_account = backend.escrow_account(auto_refund_id)
    backend.post_transfer(
        None,
        from_account=buyer,
        to_account=auto_refund_account,
        amount=1,
        reason="reserve",
        contract_id=auto_refund_id,
    )
    auto_refund_tx = backend.post_transfer(
        None,
        from_account=auto_refund_account,
        to_account=buyer,
        amount=1,
        reason="auto_refund_verification_failed",
        contract_id=auto_refund_id,
    )
    assert auto_refund_tx.startswith("0x")

    grant_tx = backend.post_transfer(
        None,
        from_account="system",
        to_account=buyer,
        amount=5,
        reason="grant",
        contract_id=None,
        allow_overdraft=True,
    )
    assert grant_tx.startswith("0x")
    assert backend.get_balance(None, buyer) == 11


def test_evm_local_backend_validation_errors() -> None:
    backend = ledger_backend.build_ledger_backend("evm_local")

    buyer = backend.agent_account("buyer")
    escrow = backend.escrow_account("contract-a")

    with pytest.raises(ValueError, match="amount must be positive"):
        backend.post_transfer(
            None,
            from_account=buyer,
            to_account=escrow,
            amount=0,
            reason="reserve",
            contract_id="contract-a",
        )

    with pytest.raises(ValueError, match="from_account and to_account must differ"):
        backend.post_transfer(
            None,
            from_account=buyer,
            to_account=buyer,
            amount=1,
            reason="grant",
            contract_id=None,
            allow_overdraft=True,
        )

    with pytest.raises(ValueError, match="contract_id is required for reserve"):
        backend.post_transfer(
            None,
            from_account=buyer,
            to_account=escrow,
            amount=1,
            reason="reserve",
            contract_id=None,
        )

    with pytest.raises(ValueError, match="contract_id is required for payout"):
        backend.post_transfer(
            None,
            from_account=escrow,
            to_account=buyer,
            amount=1,
            reason="payout",
            contract_id=None,
        )

    with pytest.raises(ValueError, match="contract_id is required for refund"):
        backend.post_transfer(
            None,
            from_account=escrow,
            to_account=buyer,
            amount=1,
            reason="refund",
            contract_id=None,
        )


def test_evm_backend_can_bind_existing_contract_address() -> None:
    first = ledger_backend.build_ledger_backend("evm_local")
    second = ledger_backend.EvmLedgerBackend(
        name="evm_local",
        web3=first._web3,
        contract_address=first._contract.address,
        operator_address=first._operator_address,
        private_key=None,
    )
    assert second._contract.address == first._contract.address


def test_evm_backend_private_key_transact_path() -> None:
    backend = ledger_backend.EvmLedgerBackend.__new__(ledger_backend.EvmLedgerBackend)
    backend._operator_address = "0x123"
    backend._private_key = "0xabc"

    class _FakeSigned:
        raw_transaction = b"raw"

    class _FakeAccount:
        def __init__(self) -> None:
            self.called = False

        def sign_transaction(self, tx, *, private_key):
            self.called = True
            assert tx["from"] == "0x123"
            assert private_key == "0xabc"
            return _FakeSigned()

    class _FakeEth:
        chain_id = 1
        gas_price = 10

        def __init__(self) -> None:
            self.account = _FakeAccount()
            self.sent = False
            self.waited = False

        def get_transaction_count(self, _address):
            return 7

        def send_raw_transaction(self, raw):
            assert raw == b"raw"
            self.sent = True
            return b"\x12"

        def wait_for_transaction_receipt(self, _tx_hash):
            self.waited = True

    class _FakeWeb3:
        def __init__(self) -> None:
            self.eth = _FakeEth()

        @staticmethod
        def to_hex(_value):
            return "0x12"

    class _FakeCall:
        def build_transaction(self, tx):
            return tx

    backend._web3 = _FakeWeb3()
    tx_hash = backend._transact(_FakeCall())
    assert tx_hash == "0x12"
    assert backend._web3.eth.sent is True
    assert backend._web3.eth.waited is True


@dataclass
class _DummyEvmBackend:
    name: str
    web3: object
    contract_address: str | None
    operator_address: str
    private_key: str | None


def _install_fake_web3(monkeypatch: pytest.MonkeyPatch, *, connected: bool, accounts: list[str]):
    class FakeHTTPProvider:
        def __init__(self, url: str) -> None:
            self.url = url

    class FakeEthereumTesterProvider:
        pass

    class FakeWeb3:
        def __init__(self, _provider) -> None:
            self.eth = SimpleNamespace(
                accounts=accounts,
                account=SimpleNamespace(from_key=lambda _key: SimpleNamespace(address="0xkeyaddr")),
            )
            self._connected = connected

        def is_connected(self) -> bool:
            return self._connected

    module_web3 = types.ModuleType("web3")
    module_web3.Web3 = FakeWeb3
    module_eth_tester = types.ModuleType("web3.providers.eth_tester")
    module_eth_tester.EthereumTesterProvider = FakeEthereumTesterProvider
    module_rpc = types.ModuleType("web3.providers.rpc")
    module_rpc.HTTPProvider = FakeHTTPProvider

    monkeypatch.setitem(sys.modules, "web3", module_web3)
    monkeypatch.setitem(sys.modules, "web3.providers.eth_tester", module_eth_tester)
    monkeypatch.setitem(sys.modules, "web3.providers.rpc", module_rpc)


def test_build_ledger_backend_evm_rpc_validation_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ledger_backend, "EvmLedgerBackend", _DummyEvmBackend)

    with pytest.raises(ValueError, match="LEDGER_EVM_RPC_URL is required"):
        ledger_backend.build_ledger_backend("evm_rpc")

    _install_fake_web3(monkeypatch, connected=False, accounts=["0xacc"])
    with pytest.raises(ValueError, match="Cannot connect to EVM RPC"):
        ledger_backend.build_ledger_backend("evm_rpc", evm_rpc_url="http://rpc")

    _install_fake_web3(monkeypatch, connected=True, accounts=[])
    with pytest.raises(ValueError, match="no unlocked accounts"):
        ledger_backend.build_ledger_backend("evm_rpc", evm_rpc_url="http://rpc")

    _install_fake_web3(monkeypatch, connected=True, accounts=["0xabc"])
    backend_with_key = ledger_backend.build_ledger_backend(
        "evm_rpc",
        evm_rpc_url="http://rpc",
        evm_private_key="0xpriv",
    )
    assert backend_with_key.operator_address == "0xkeyaddr"
    assert backend_with_key.private_key == "0xpriv"

    _install_fake_web3(monkeypatch, connected=True, accounts=["0xabc"])
    backend_with_unlocked = ledger_backend.build_ledger_backend(
        "evm_rpc",
        evm_rpc_url="http://rpc",
    )
    assert backend_with_unlocked.operator_address == "0xabc"
