import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.services.ledger import agent_account, escrow_account, get_balance, post_transfer


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def test_account_name_helpers() -> None:
    assert agent_account("a1") == "agent:a1"
    assert escrow_account("c1") == "escrow:c1"


def test_post_transfer_creates_balanced_entries(session: Session) -> None:
    post_transfer(session, "mint", "agent:buyer", 100, reason="faucet", contract_id=None, allow_overdraft=True)
    tx_id = post_transfer(session, "agent:buyer", "escrow:contract-1", 25, reason="reserve", contract_id="contract-1")

    buyer_balance = get_balance(session, "agent:buyer")
    escrow_balance = get_balance(session, "escrow:contract-1")

    assert tx_id
    assert buyer_balance == 75
    assert escrow_balance == 25


def test_post_transfer_rejects_insufficient_funds(session: Session) -> None:
    with pytest.raises(ValueError):
        post_transfer(session, "agent:buyer", "escrow:contract-1", 10, reason="reserve", contract_id="contract-1")


def test_post_transfer_rejects_non_positive_amount(session: Session) -> None:
    with pytest.raises(ValueError):
        post_transfer(session, "a", "b", 0, reason="x", contract_id=None)


def test_post_transfer_rejects_same_account(session: Session) -> None:
    with pytest.raises(ValueError):
        post_transfer(session, "a", "a", 1, reason="x", contract_id=None)


def test_overdraft_allowed(session: Session) -> None:
    post_transfer(session, "mint", "agent:buyer", 1, reason="seed", contract_id=None, allow_overdraft=True)
    post_transfer(session, "system", "agent:buyer", 3, reason="grant", contract_id=None, allow_overdraft=True)
    assert get_balance(session, "agent:buyer") == 4
