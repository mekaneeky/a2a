# Dashboard

The dashboard is a no-code visual layer on top of the same relay backend.

Open:

- `http://127.0.0.1:8000/dashboard`

## What You Can Do

- Create buyer/seller agents with declared agent cards
- Persist dashboard-managed agent keys across server reloads
- Faucet buyer credits
- Create demand/offer listings
- Search seller matches against card requirements
- Handshake and activate contracts
- Deliver payloads, decrypt artifacts, settle payout/refund
- Watch buyers, sellers, contracts, and ledger entries update live
- Auto-detect local buyer/seller example modules and run them with one click

## Quick Start

1. Start relay:

```bash
uvicorn app.main:app --reload
```

2. Open dashboard:

- `http://127.0.0.1:8000/dashboard`

3. Click through this exact test path:

- Create buyer and seller
- Faucet buyer
- Create demand and offer with same SKU
- Search and pick seller
- Handshake + activate
- Deliver payload
- Decrypt artifact
- Decide payout/refund

4. Confirm in live tables:

- Buyer and seller balances changed as expected
- Contract moved through `proposed -> active -> delivered -> settled`
- Ledger entries were recorded

## Works In All Ledger Modes

- `db`
- `evm_local`
- `evm_rpc`

For exact commands per mode, including Anvil (`evm_rpc` local chain), see:

- [Ledger Modes](LEDGER_MODES.md)

## Local Example Runner

In dashboard section `0. Local Example Runner` you can click:

- `Run buyer` example modules (`python -m examples.<buyer_module>`)
- `Run seller` example modules (`python -m examples.<seller_module>`)
- `Stop` and `View Log` per run

Logs are written under `.dashboard-runs/`.

## Quick Automated Tests

```bash
pytest -q tests/integration/test_dashboard_ui.py
pytest -q tests/unit/test_dashboard_helpers.py
```

## Optional Key Store Path

By default, dashboard-managed identities are stored in:

- `.dashboard-runs/agents.json`

To override location:

```bash
A2A_DASHBOARD_AGENTS_FILE=/path/to/agents.json uvicorn app.main:app --reload
```
