from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlmodel import Session

from app.services import ledger as db_ledger


class LedgerBackend(Protocol):
    name: str

    def agent_account(self, agent_id: str) -> str: ...

    def escrow_account(self, contract_id: str) -> str: ...

    def get_balance(self, session: Session, account: str) -> int: ...

    def post_transfer(
        self,
        session: Session,
        from_account: str,
        to_account: str,
        amount: int,
        *,
        reason: str,
        contract_id: str | None,
        allow_overdraft: bool = False,
    ) -> str: ...


@dataclass
class DbLedgerBackend:
    name: str = "db"

    def agent_account(self, agent_id: str) -> str:
        return db_ledger.agent_account(agent_id)

    def escrow_account(self, contract_id: str) -> str:
        return db_ledger.escrow_account(contract_id)

    def get_balance(self, session: Session, account: str) -> int:
        return db_ledger.get_balance(session, account)

    def post_transfer(
        self,
        session: Session,
        from_account: str,
        to_account: str,
        amount: int,
        *,
        reason: str,
        contract_id: str | None,
        allow_overdraft: bool = False,
    ) -> str:
        return db_ledger.post_transfer(
            session,
            from_account=from_account,
            to_account=to_account,
            amount=amount,
            reason=reason,
            contract_id=contract_id,
            allow_overdraft=allow_overdraft,
        )


class EvmLedgerBackend:
    def __init__(
        self,
        *,
        name: str,
        web3,
        contract_address: str | None,
        operator_address: str,
        private_key: str | None,
    ) -> None:
        self.name = name
        self._web3 = web3
        self._operator_address = operator_address
        self._private_key = private_key
        artifact = _load_artifact()
        self._contract = self._load_or_deploy_contract(artifact=artifact, contract_address=contract_address)

    def agent_account(self, agent_id: str) -> str:
        return db_ledger.agent_account(agent_id)

    def escrow_account(self, contract_id: str) -> str:
        return db_ledger.escrow_account(contract_id)

    def get_balance(self, _session: Session, account: str) -> int:
        if account.startswith("escrow:"):
            contract_id = account.split(":", 1)[1]
            value = self._contract.functions.escrowOf(_id_key(contract_id, self._web3)).call()
            return int(value)

        value = self._contract.functions.balanceOf(_account_key(account, self._web3)).call()
        return int(value)

    def post_transfer(
        self,
        _session: Session,
        from_account: str,
        to_account: str,
        amount: int,
        *,
        reason: str,
        contract_id: str | None,
        allow_overdraft: bool = False,
    ) -> str:
        if amount <= 0:
            raise ValueError("amount must be positive")
        if from_account == to_account:
            raise ValueError("from_account and to_account must differ")

        tx_id = str(uuid.uuid4())
        tx_key = _id_key(tx_id, self._web3)

        if reason == "faucet":
            tx_hash = self._transact(
                self._contract.functions.faucet(
                    _account_key(to_account, self._web3),
                    amount,
                    tx_key,
                )
            )
            return tx_hash

        if reason == "reserve":
            if contract_id is None:
                raise ValueError("contract_id is required for reserve")
            tx_hash = self._transact(
                self._contract.functions.reserve(
                    _id_key(contract_id, self._web3),
                    _account_key(from_account, self._web3),
                    amount,
                    tx_key,
                )
            )
            return tx_hash

        if reason == "payout":
            if contract_id is None:
                raise ValueError("contract_id is required for payout")
            tx_hash = self._transact(
                self._contract.functions.payout(
                    _id_key(contract_id, self._web3),
                    _account_key(to_account, self._web3),
                    tx_key,
                )
            )
            return tx_hash

        if reason in {"refund", "auto_refund_verification_failed"}:
            if contract_id is None:
                raise ValueError("contract_id is required for refund")
            tx_hash = self._transact(
                self._contract.functions.refund(
                    _id_key(contract_id, self._web3),
                    _account_key(to_account, self._web3),
                    tx_key,
                )
            )
            return tx_hash

        tx_hash = self._transact(
            self._contract.functions.transfer(
                _account_key(from_account, self._web3),
                _account_key(to_account, self._web3),
                amount,
                tx_key,
                allow_overdraft,
            )
        )
        return tx_hash

    def _load_or_deploy_contract(self, *, artifact: dict[str, object], contract_address: str | None):
        if contract_address is not None:
            return self._web3.eth.contract(address=contract_address, abi=artifact["abi"])

        contract_type = self._web3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
        tx_hash = self._transact(contract_type.constructor(self._operator_address))
        receipt = self._web3.eth.get_transaction_receipt(tx_hash)
        address = receipt["contractAddress"]
        return self._web3.eth.contract(address=address, abi=artifact["abi"])

    def _transact(self, call) -> str:
        if self._private_key:
            nonce = self._web3.eth.get_transaction_count(self._operator_address)
            tx = call.build_transaction(
                {
                    "from": self._operator_address,
                    "nonce": nonce,
                    "chainId": self._web3.eth.chain_id,
                    "gas": 2_000_000,
                    "maxFeePerGas": self._web3.eth.gas_price,
                    "maxPriorityFeePerGas": 0,
                }
            )
            signed = self._web3.eth.account.sign_transaction(tx, private_key=self._private_key)
            tx_hash = self._web3.eth.send_raw_transaction(signed.raw_transaction)
        else:
            tx_hash = call.transact({"from": self._operator_address})

        self._web3.eth.wait_for_transaction_receipt(tx_hash)
        return self._web3.to_hex(tx_hash)


def build_ledger_backend(
    mode: str,
    *,
    evm_rpc_url: str | None = None,
    evm_contract_address: str | None = None,
    evm_private_key: str | None = None,
) -> LedgerBackend:
    normalized_mode = mode.strip().lower()
    if normalized_mode == "db":
        return DbLedgerBackend()

    if normalized_mode not in {"evm_local", "evm_rpc"}:
        raise ValueError(f"Unsupported ledger backend: {mode}")

    from web3 import Web3
    from web3.providers.eth_tester import EthereumTesterProvider
    from web3.providers.rpc import HTTPProvider

    if normalized_mode == "evm_local":
        web3 = Web3(EthereumTesterProvider())
        operator_address = web3.eth.accounts[0]
        return EvmLedgerBackend(
            name=normalized_mode,
            web3=web3,
            contract_address=evm_contract_address,
            operator_address=operator_address,
            private_key=None,
        )

    if evm_rpc_url is None:
        raise ValueError("LEDGER_EVM_RPC_URL is required for evm_rpc backend")

    web3 = Web3(HTTPProvider(evm_rpc_url))
    if not web3.is_connected():
        raise ValueError(f"Cannot connect to EVM RPC: {evm_rpc_url}")

    if evm_private_key:
        operator_address = web3.eth.account.from_key(evm_private_key).address
    else:
        if not web3.eth.accounts:
            raise ValueError("EVM RPC has no unlocked accounts; set LEDGER_EVM_PRIVATE_KEY")
        operator_address = web3.eth.accounts[0]

    return EvmLedgerBackend(
        name=normalized_mode,
        web3=web3,
        contract_address=evm_contract_address,
        operator_address=operator_address,
        private_key=evm_private_key,
    )


def _artifact_path() -> Path:
    return Path(__file__).resolve().parent / "a2a_ledger_artifact.json"


def _load_artifact() -> dict[str, object]:
    payload = json.loads(_artifact_path().read_text())
    return {
        "abi": payload["abi"],
        "bytecode": payload["bytecode"],
    }


def _account_key(value: str, web3) -> bytes:
    return web3.keccak(text=value)


def _id_key(value: str, web3) -> bytes:
    return web3.keccak(text=value)
