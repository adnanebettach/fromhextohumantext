import os
import requests

ETHERSCAN_API = "https://api.etherscan.io/v2/api"
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")
CHAIN_ID = os.getenv("ETHERSCAN_CHAIN_ID", "1")  # 1 = Ethereum mainnet


class DecoderError(Exception):
    pass


def _check_key():
    if not ETHERSCAN_API_KEY:
        raise DecoderError("ETHERSCAN_API_KEY is not set. Export it in your environment first.")


def _etherscan_get(params: dict) -> dict:
    _check_key()
    params = {**params, "apikey": ETHERSCAN_API_KEY}
    r = requests.get(ETHERSCAN_API, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    # For v2 proxy endpoints, when the call succeeds `result` is the tx/receipt dict.
    result = data.get("result")
    if not isinstance(result, dict):
        # Only treat cases where result is not an object as an error
        raise DecoderError(f"Etherscan error or unexpected shape: {data}")
    return result



def get_transaction(tx_hash: str) -> dict:
    """
    eth_getTransactionByHash via v2 proxy API.
    https://docs.etherscan.io/api-reference/endpoint/ethgettransactionbyhash
    """
    return _etherscan_get(
        {
            "chainid": CHAIN_ID,
            "module": "proxy",
            "action": "eth_getTransactionByHash",
            "txhash": tx_hash,
        }
    )


def get_receipt(tx_hash: str) -> dict:
    """
    eth_getTransactionReceipt via v2 proxy API.
    """
    return _etherscan_get(
        {
            "chainid": CHAIN_ID,
            "module": "proxy",
            "action": "eth_getTransactionReceipt",
            "txhash": tx_hash,
        }
    )


# -----------------------------
# Simple decoding helpers
# -----------------------------

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def decode_function_signature(calldata: str) -> str:
    """Resolve function selector using 4byte.directory."""
    if not calldata or calldata == "0x":
        return "ETH transfer"
    selector = calldata[:10]  # 0x + 4 bytes
    url = f"https://www.4byte.directory/api/v1/signatures/?hex_signature={selector}"
    resp = requests.get(url, timeout=10).json()
    results = resp.get("results", [])
    if results:
        return results[0]["text_signature"]
    return "Unknown function"


def parse_erc20_transfers(receipt: dict) -> list[dict]:
    """Extract ERC‑20 Transfer events from logs."""
    transfers = []
    for log in receipt.get("logs", []):
        topics = [t.lower() for t in log.get("topics", [])]
        if not topics:
            continue
        if topics[0] != TRANSFER_TOPIC:
            continue
        from_addr = "0x" + topics[1][-40:]
        to_addr = "0x" + topics[2][-40:]
        value_int = int(log.get("data", "0x0"), 16)

        transfers.append(
            {
                "token_contract": log.get("address", ""),
                "from": from_addr,
                "to": to_addr,
                # keep original int if you want, but add a string field for the UI
                "raw_value": value_int,
                "raw_value_str": str(value_int),
                "type": "transfer",
            }
        )
    return transfers



def classify_transaction(function_sig: str, transfers: list[dict]) -> str:
    """Very simple taxonomy classifier for v1."""
    sig_low = (function_sig or "").lower()
    if sig_low.startswith("approve("):
        return "erc20_approval"
    if sig_low.startswith("transferfrom("):
        return "erc20_transfer_from"
    if sig_low.startswith("transfer(") and len(transfers) == 1:
        return "erc20_transfer"
    if "swap" in sig_low:
        return "dex_swap"
    if transfers:
        return "erc20_transfer"
    return "unknown"


def shorten(addr: str) -> str:
    if not isinstance(addr, str) or not addr.startswith("0x") or len(addr) < 10:
        return addr or "—"
    return addr[:6] + "…" + addr[-4:]


def build_plain_english(tx: dict, tx_type: str, function_sig: str, transfers: list[dict]) -> str:
    from_addr = tx.get("from", "—")
    to_addr = tx.get("to", "—")
    value_wei = int(tx.get("value", "0x0"), 16)
    value_eth = value_wei / 10**18

    if tx_type == "erc20_approval":
        spender = shorten(to_addr)
        return (
            f"You approved contract {spender} to spend your tokens. "
            "This permission may remain active until you revoke it."
        )
    if tx_type == "erc20_transfer" and transfers:
        t = transfers[0]
        return (
            f"You sent tokens from {shorten(t['from'])} to {shorten(t['to'])} "
            f"via token contract {shorten(t['token_contract'])}."
        )
    if tx_type == "dex_swap":
        return (
            "You interacted with a swap-like function. "
            "Tokens were likely exchanged via a DEX router or aggregator."
        )
    if value_eth > 0 and function_sig == "ETH transfer":
        return (
            f"You sent {value_eth:.6f} ETH from {shorten(from_addr)} "
            f"to {shorten(to_addr)}."
        )
    return (
        "This transaction calls "
        f"`{function_sig}` on {shorten(to_addr)}. "
        "The decoder cannot yet provide a more detailed explanation."
    )


def infer_risk_flags(tx_type: str) -> list[str]:
    """Heuristic v1 flags used by interpretation.generate_risk_label."""
    flags: list[str] = []
    if tx_type == "erc20_approval":
        flags.append("unlimited_approval")
        flags.append("contract_interaction")
    if tx_type in {"dex_swap", "erc20_transfer_from"}:
        flags.append("contract_interaction")
    return flags


def decode_transaction(tx_hash: str) -> dict:
    """
    High-level API used by app.py.

    Returns a dict compatible with your existing mock structure.
    """
    tx = get_transaction(tx_hash)
    receipt = get_receipt(tx_hash)

    if not isinstance(tx, dict):
        raise DecoderError(f"Unexpected tx type: {type(tx)}")
    if not isinstance(receipt, dict):
        raise DecoderError(f"Unexpected receipt type: {type(receipt)}")

    status_hex = receipt.get("status")
    # Some RPCs return status as hex string; default to success if missing
    if isinstance(status_hex, str) and status_hex.startswith("0x"):
        ok = int(status_hex, 16) == 1
    else:
        ok = True
    status = "success" if ok else "failed"

    value_wei = int(tx.get("value", "0x0"), 16)
    value_eth = value_wei / 10**18

    calldata = tx.get("input", "0x")
    function_sig = decode_function_signature(calldata)
    transfers = parse_erc20_transfers(receipt)
    tx_type = classify_transaction(function_sig, transfers)
    plain_english = build_plain_english(tx, tx_type, function_sig, transfers)
    risk_flags = infer_risk_flags(tx_type)

    if tx_type == "erc20_approval":
        expected = "You expected to approve tokens for a single action."
        actual = (
            "You created an on-chain approval that stays valid until you revoke or change it."
        )
    else:
        expected = "You expected the transaction to do what the interface described."
        actual = "The decoded effects match a basic pattern for this type."

    return {
        "status": status,
        "from": tx.get("from", "—"),
        "to": tx.get("to", "—"),
        "value_eth": value_eth,
        "function_name": function_sig,
        "function_params": {},  # can be extended later
        "events": transfers,
        "risk_flags": risk_flags,
        "plain_english": plain_english,
        "expected": expected,
        "actual": actual,
    }
