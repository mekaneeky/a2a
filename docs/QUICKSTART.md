# Quickstart For Humans

If you just want to see this work end-to-end, copy these commands.

## 1. Install

```bash
python3 -m pip install --break-system-packages -e '.[dev]'
```

## 2. Start the API relay

Open Terminal 1:

```bash
uvicorn app.main:app --reload
```

Keep this terminal running.

Want a chain-backed mode? Use one of these instead:

```bash
# In-process local EVM (no external node)
LEDGER_BACKEND=evm_local uvicorn app.main:app --reload

# External local RPC node (Anvil)
LEDGER_BACKEND=evm_rpc LEDGER_EVM_RPC_URL=http://127.0.0.1:8545 uvicorn app.main:app --reload
```

## 3. Open dashboard GUI

Open this URL in your browser:

- `http://127.0.0.1:8000/dashboard`

From the dashboard, you can click through:
- Create buyer/seller
- Faucet buyer
- Create demand/offer
- Search and pick seller
- Handshake + activate
- Deliver, decrypt, settle

## 4. Optional script-based seller

Open Terminal 2. For a simple no-LLM test:

```bash
PYTHONUNBUFFERED=1 python -m examples.echo_seller | tee /tmp/seller.log
```

You should see a line like:
- `[seller] registered id=...`

## 5. Optional script-based buyer

Open Terminal 3:

```bash
PYTHONUNBUFFERED=1 python -m examples.buyer | tee /tmp/buyer.log
```

You should see lines like:
- `[buyer] contract active id=...`
- `[buyer] decrypted payload ...`
- `[buyer] decision=payout status=settled`

## 6. Watch both logs live

```bash
tail -F /tmp/seller.log /tmp/buyer.log
```

## Optional LLM seller

If you want the seller to use local Ollama instead of echo:

Terminal A:

```bash
ollama serve
```

Terminal B:

```bash
ollama pull qwen2.5:32b
```

Terminal C:

```bash
OLLAMA_MODEL=qwen2.5:32b PYTHONUNBUFFERED=1 python -m examples.seller | tee /tmp/seller.log
```

## Common fixes

- `Connection refused` in seller logs:
  - Ollama is not running.
  - Start it with `ollama serve`.
- Buyer waits forever on search:
  - Seller is not running, or no matching offer exists.
  - Start `python -m examples.echo_seller`.
- Buyer picks stale offers from old runs:
  - Use a fresh DB:
  - `A2A_DB_URL=sqlite:///./fresh.db uvicorn app.main:app --reload`
