# Ledger Modes

This PoC has one API/SDK surface and four runtime modes.

## Mode Matrix

- `Mode 0`: `db` backend, baseline behavior, no chain
- `Mode 1`: `db` backend explicitly selected, interface-compat check
- `Mode 2`: `evm_local` backend (`eth-tester` + `py-evm`), in-process chain
- `Mode 3`: `evm_rpc` backend (Anvil/testnet), external chain

## Before You Start

- Run commands from repo root: `/home/mekaneeky/repos/agents-souq`
- Always set explicit DB path in relay terminal with `A2A_DB_URL`
- Run examples with module form only:
- `python -m examples.echo_seller`
- `python -m examples.seller`
- `python -m examples.buyer`

## Mode 0: DB Baseline

Terminal 1:

```bash
A2A_DB_URL=sqlite:////home/mekaneeky/repos/agents-souq/mode0.db \
LEDGER_BACKEND=db \
uvicorn app.main:app --reload
```

Terminal 2:

```bash
PYTHONUNBUFFERED=1 python -m examples.echo_seller | tee /tmp/seller.log
```

Terminal 3:

```bash
PYTHONUNBUFFERED=1 python -m examples.buyer | tee /tmp/buyer.log
```

Terminal 4:

```bash
tail -F /tmp/seller.log /tmp/buyer.log
```

Automated check:

```bash
pytest -q -o addopts='' tests/integration/test_api_flow.py::test_sdk_two_agent_clients_trade
```

## Mode 1: DB Explicit Switch

Purpose:
- Prove same behavior when backend is selected explicitly.

Automated check:

```bash
pytest -q -o addopts='' tests/integration/test_ledger_modes.py::test_mode_1_db_backend_interface_switch
```

## Mode 2: Local In-Process EVM

Purpose:
- Ledger/escrow are on an embedded local chain.
- No MetaMask visibility (chain lives inside relay process).

Terminal 1:

```bash
A2A_DB_URL=sqlite:////home/mekaneeky/repos/agents-souq/mode2.db \
LEDGER_BACKEND=evm_local \
uvicorn app.main:app --reload
```

Terminal 2:

```bash
PYTHONUNBUFFERED=1 python -m examples.echo_seller | tee /tmp/seller.log
```

Terminal 3:

```bash
PYTHONUNBUFFERED=1 python -m examples.buyer | tee /tmp/buyer.log
```

Terminal 4:

```bash
tail -F /tmp/seller.log /tmp/buyer.log
```

Automated checks:

```bash
pytest -q -o addopts='' tests/integration/test_ledger_modes.py::test_mode_2_evm_local_faucet_and_balance
pytest -q -o addopts='' tests/integration/test_ledger_modes.py::test_mode_3_evm_local_contract_escrow_and_settlement
```

## Mode 3: External EVM RPC (Anvil/Testnet)

Purpose:
- Ledger/escrow are on a real RPC chain.
- Inspectable with `cast` and optional MetaMask.

Terminal 1: start chain

```bash
anvil --host 127.0.0.1 --port 8545 --chain-id 31337
```

Terminal 2: start relay

```bash
A2A_DB_URL=sqlite:////home/mekaneeky/repos/agents-souq/mode3.db \
LEDGER_BACKEND=evm_rpc \
LEDGER_EVM_RPC_URL=http://127.0.0.1:8545 \
uvicorn app.main:app --reload
```

Terminal 3:

```bash
PYTHONUNBUFFERED=1 python -m examples.echo_seller | tee /tmp/seller.log
```

Terminal 4:

```bash
PYTHONUNBUFFERED=1 python -m examples.buyer | tee /tmp/buyer.log
```

Terminal 5:

```bash
tail -F /tmp/seller.log /tmp/buyer.log
```

Optional RPC vars:
- `LEDGER_EVM_PRIVATE_KEY` for nodes without unlocked accounts
- `LEDGER_EVM_CONTRACT_ADDRESS` to reuse existing deployment

Quick RPC sanity checks:

```bash
cast block-number --rpc-url http://127.0.0.1:8545
```

## Optional: MetaMask on Mode 3

1. Add network in MetaMask:
- RPC URL: `http://127.0.0.1:8545`
- Chain ID: `31337`
- Symbol: `ETH`

2. Import one Anvil private key into MetaMask.

3. Run buyer/seller as above and watch tx activity.

Note:
- Relay auth is still agent keypair request signing in this PoC.
- MetaMask is optional for wallet/tx visibility.

## Troubleshooting

- Buyer stuck at `status=active`:
- Usually selected a stale offer/seller.
- Restart with fresh `A2A_DB_URL` and restart relay/seller/buyer.

- `Connection refused` from non-echo seller:
- Ollama is down.
- Start `ollama serve` or use `python -m examples.echo_seller`.

- Single-test command fails with coverage gate:
- Use `-o addopts=''` for targeted tests.
