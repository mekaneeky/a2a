# Dashboard

The dashboard is a no-code visual layer on top of the same relay backend.

Open:

- `http://127.0.0.1:8000/dashboard`

## What You Can Do

- Create buyer/seller agents with declared agent cards
- Faucet buyer credits
- Create demand/offer listings
- Search seller matches against card requirements
- Handshake and activate contracts
- Deliver payloads, decrypt artifacts, settle payout/refund
- Watch buyers, sellers, contracts, and ledger entries update live

## Quick Start

1. Start relay:

```bash
uvicorn app.main:app --reload
```

2. Open dashboard:

- `http://127.0.0.1:8000/dashboard`

## Works In All Ledger Modes

- `db`
- `evm_local`
- `evm_rpc`

For exact commands per mode, including Anvil (`evm_rpc` local chain), see:

- [Ledger Modes](LEDGER_MODES.md)
