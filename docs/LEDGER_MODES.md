# Ledger Modes

One API and one dashboard, four runtime modes.

## Mode Map

- `Mode 0` `db`: SQLite-backed internal ledger
- `Mode 1` `evm_local`: in-process EVM ledger (`eth-tester`)
- `Mode 2` `evm_rpc + anvil`: external local RPC node (recommended chain demo mode)
- `Mode 3` `evm_rpc + testnet`: external public RPC endpoint

## Shared Notes

- Run from repo root: `/home/mekaneeky/repos/agents-souq`
- Use module execution only for examples:
  - `python -m examples.echo_seller`
  - `python -m examples.seller`
  - `python -m examples.buyer`
- Open GUI at `http://127.0.0.1:8000/dashboard`

---

## Mode 0 DB

### Relay

```bash
A2A_DB_URL=sqlite:////home/mekaneeky/repos/agents-souq/mode0.db \
LEDGER_BACKEND=db \
uvicorn app.main:app --reload
```

### Optional script agents

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

### Automated checks

```bash
pytest -q -o addopts='' tests/integration/test_dashboard_ui.py::test_dashboard_ui_can_run_end_to_end_trade[db]
pytest -q -o addopts='' tests/integration/test_ledger_modes.py::test_mode_1_db_backend_interface_switch
```

---

## Mode 1 EVM Local

This uses an embedded chain inside the relay process.

### Relay

```bash
A2A_DB_URL=sqlite:////home/mekaneeky/repos/agents-souq/mode1.db \
LEDGER_BACKEND=evm_local \
uvicorn app.main:app --reload
```

### Optional script agents

```bash
PYTHONUNBUFFERED=1 python -m examples.echo_seller | tee /tmp/seller.log
PYTHONUNBUFFERED=1 python -m examples.buyer | tee /tmp/buyer.log
```

### Automated checks

```bash
pytest -q -o addopts='' tests/integration/test_dashboard_ui.py::test_dashboard_ui_can_run_end_to_end_trade[evm_local]
pytest -q -o addopts='' tests/integration/test_ledger_modes.py::test_mode_2_evm_local_faucet_and_balance
pytest -q -o addopts='' tests/integration/test_ledger_modes.py::test_mode_3_evm_local_contract_escrow_and_settlement
```

---

## Mode 2 EVM RPC Plus Anvil

This is the main local-chain mode if you want external chain visibility.

### Terminal 1 start Anvil

```bash
anvil --host 127.0.0.1 --port 8545 --chain-id 31337
```

### Terminal 2 start relay

```bash
A2A_DB_URL=sqlite:////home/mekaneeky/repos/agents-souq/mode2.db \
LEDGER_BACKEND=evm_rpc \
LEDGER_EVM_RPC_URL=http://127.0.0.1:8545 \
uvicorn app.main:app --reload
```

### Terminal 3 open dashboard

- `http://127.0.0.1:8000/dashboard`

### Optional script agents

Terminal 4:

```bash
PYTHONUNBUFFERED=1 python -m examples.echo_seller | tee /tmp/seller.log
```

Terminal 5:

```bash
PYTHONUNBUFFERED=1 python -m examples.buyer | tee /tmp/buyer.log
```

Terminal 6:

```bash
tail -F /tmp/seller.log /tmp/buyer.log
```

### Optional MetaMask (local)

1. Add custom network:
- RPC URL `http://127.0.0.1:8545`
- Chain ID `31337`
- Symbol `ETH`
2. Import one Anvil private key.
3. Run buyer/seller or dashboard actions and watch tx updates.

### Automated check when Anvil is running

```bash
LEDGER_EVM_RPC_URL=http://127.0.0.1:8545 \
pytest -q -o addopts='' tests/integration/test_ledger_modes_rpc.py::test_mode_2_evm_rpc_with_anvil
```

---

## Mode 3 EVM RPC Plus Testnet

Same interface as Mode 2, but use a public/private testnet RPC.

### Relay

```bash
A2A_DB_URL=sqlite:////home/mekaneeky/repos/agents-souq/mode3.db \
LEDGER_BACKEND=evm_rpc \
LEDGER_EVM_RPC_URL=https://your-testnet-rpc.example \
LEDGER_EVM_PRIVATE_KEY=0x... \
uvicorn app.main:app --reload
```

Optional:
- `LEDGER_EVM_CONTRACT_ADDRESS` to reuse a pre-deployed contract.

Notes:
- API auth still uses agent keypair signatures, not wallet signatures.
- MetaMask remains optional for visibility and manual wallet checks.

---

## Troubleshooting

- `anvil: command not found`:
  - Install Foundry and ensure `anvil` is on `PATH`.
- Buyer waits at `status=active`:
  - Seller is not delivering, or a stale offer was selected.
  - Use fresh DB path and restart relay + agents.
- Seller logs `Connection refused`:
  - Non-echo seller cannot reach Ollama.
  - Start `ollama serve` or switch to `python -m examples.echo_seller`.
- Single-test commands fail due coverage gate:
  - Keep `-o addopts=''` in targeted test commands.
