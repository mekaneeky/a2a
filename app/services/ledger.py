from __future__ import annotations

from uuid import uuid4

from sqlmodel import Session, func, select

from app.models import LedgerEntry


def agent_account(agent_id: str) -> str:
    return f"agent:{agent_id}"


def escrow_account(contract_id: str) -> str:
    return f"escrow:{contract_id}"


def get_balance(session: Session, account: str) -> int:
    query = select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(LedgerEntry.account == account)
    value = session.exec(query).one()
    return int(value)


def post_transfer(
    session: Session,
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

    if not allow_overdraft:
        balance = get_balance(session, from_account)
        if balance < amount:
            raise ValueError("insufficient funds")

    tx_id = str(uuid4())
    session.add(
        LedgerEntry(
            tx_id=tx_id,
            account=from_account,
            amount=-amount,
            reason=reason,
            contract_id=contract_id,
        )
    )
    session.add(
        LedgerEntry(
            tx_id=tx_id,
            account=to_account,
            amount=amount,
            reason=reason,
            contract_id=contract_id,
        )
    )
    session.commit()
    return tx_id
