import os
import re
import requests
import streamlit as st

try:
    from eth_abi import decode as abi_decode
except ImportError:
    try:
        from eth_abi import decode_abi as abi_decode
    except ImportError:
        abi_decode = None

# ─────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────

ETHERSCAN_API     = "https://api.etherscan.io/v2/api"
ETHERSCAN_API_KEY = (
    os.getenv("ETHERSCAN_API_KEY")
    or (st.secrets.get("ETHERSCAN_API_KEY") if st is not None else "")
)

CHAIN_ID = (
    os.getenv("ETHERSCAN_CHAIN_ID")
    or (st.secrets.get("ETHERSCAN_CHAIN_ID") if st is not None else "1")
)
MAX_UINT256       = 2**256 - 1
ZERO_ADDRESS      = "0x" + "0" * 40

# Known event topics
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
APPROVAL_TOPIC = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
# ERC-721 ApprovalForAll
APPROVAL_FOR_ALL_TOPIC = "0x17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31"

# In-memory caches
_SELECTOR_CACHE: dict[str, str]  = {}
_TOKEN_CACHE:    dict[str, dict] = {}
_ABI_CACHE:      dict[str, list] = {}

# Known parameter names per function base-name
PARAM_NAMES: dict[str, list[str]] = {
    "approve":                   ["spender", "amount"],
    "transfer":                  ["to", "amount"],
    "transferfrom":              ["from", "to", "amount"],
    "safetransferfrom":          ["from", "to", "tokenId"],
    "mint":                      ["to", "amount"],
    "burn":                      ["from", "amount"],
    "deposit":                   ["amount"],
    "withdraw":                  ["amount"],
    "swapexacttokensfortokens":  ["amountIn", "amountOutMin", "path", "to", "deadline"],
    "swaptokensforexacttokens":  ["amountOut", "amountInMax", "path", "to", "deadline"],
    "swapexactethfortokens":     ["amountOutMin", "path", "to", "deadline"],
    "swaptokensforexacteth":     ["amountOut", "amountInMax", "path", "to", "deadline"],
    "swapexacttokensforeth":     ["amountIn", "amountOutMin", "path", "to", "deadline"],
    "exactinputsingle":          ["params"],
    "exactoutputsingle":         ["params"],
    "multicall":                 ["deadline", "data"],
    "setapprovalforall":         ["operator", "approved"],
}

# Risk score weights
RISK_WEIGHTS: dict[str, int] = {
    "unlimited_approval":        80,
    "approval_for_all":          75,
    "unknown_function":          60,
    "contract_interaction":      20,
    "nft_transfer":              15,
    "large_approval":            40,
    "zero_address_interaction":  50,
    "multi_token_drain":         70,
    "limited_approval":          10,
}


class DecoderError(Exception):
    pass


# ─────────────────────────────────────────────
#  Etherscan API helpers
# ─────────────────────────────────────────────

def _check_key() -> None:
    if not ETHERSCAN_API_KEY:
        raise DecoderError("ETHERSCAN_API_KEY is not set.")


def _etherscan_get(params: dict) -> dict | None:
    _check_key()
    params = {**params, "apikey": ETHERSCAN_API_KEY}
    r = requests.get(ETHERSCAN_API, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    result = data.get("result")

    # Allow None for "tx not found" instead of raising
    if result is None:
        return None

    if not isinstance(result, dict):
        raise DecoderError(f"Etherscan unexpected response: {data}")
    return result



def _etherscan_raw(params: dict) -> str:
    """Return raw string result (used for ABI and eth_call)."""
    _check_key()
    params = {**params, "apikey": ETHERSCAN_API_KEY}
    r = requests.get(ETHERSCAN_API, params=params, timeout=15)
    r.raise_for_status()
    return r.json().get("result", "")


def _eth_call(to: str, data: str) -> str:
    if not ETHERSCAN_API_KEY:
        return "0x"
    try:
        r = requests.get(
            ETHERSCAN_API,
            params={
                "chainid": CHAIN_ID,
                "module":  "proxy",
                "action":  "eth_call",
                "to":      to,
                "data":    data,
                "tag":     "latest",
                "apikey":  ETHERSCAN_API_KEY,
            },
            timeout=10,
        )
        return r.json().get("result", "0x") or "0x"
    except Exception:
        return "0x"


def get_transaction(tx_hash: str) -> dict:
    return _etherscan_get({
        "chainid": CHAIN_ID,
        "module":  "proxy",
        "action":  "eth_getTransactionByHash",
        "txhash":  tx_hash,
    })


def get_receipt(tx_hash: str) -> dict:
    return _etherscan_get({
        "chainid": CHAIN_ID,
        "module":  "proxy",
        "action":  "eth_getTransactionReceipt",
        "txhash":  tx_hash,
    })


# ─────────────────────────────────────────────
#  ABI fetching
# ─────────────────────────────────────────────

def get_contract_abi(address: str) -> list:
    """
    Fetch the verified ABI for a contract from Etherscan.
    Returns an empty list if the contract is unverified or on error.
    """
    addr = address.lower()
    if addr in _ABI_CACHE:
        return _ABI_CACHE[addr]
    try:
        raw = _etherscan_raw({
            "chainid": CHAIN_ID,
            "module":  "contract",
            "action":  "getabi",
            "address": address,
        })
        if raw and raw != "Contract source code not verified":
            import json
            abi = json.loads(raw)
            _ABI_CACHE[addr] = abi
            return abi
    except Exception:
        pass
    _ABI_CACHE[addr] = []
    return []


def _abi_function_map(abi: list) -> dict[str, dict]:
    """Build selector → ABI entry map from a contract ABI."""
    import hashlib
    result = {}
    for entry in abi:
        if entry.get("type") != "function":
            continue
        name   = entry.get("name", "")
        inputs = entry.get("inputs", [])
        types  = ",".join(i.get("type", "") for i in inputs)
        sig    = f"{name}({types})"
        selector = "0x" + hashlib.sha3_256(sig.encode()).hexdigest()[:8]
        try:
            from web3 import Web3
            selector = Web3.keccak(text=sig).hex()[:10]
        except Exception:
            pass
        result[selector.lower()] = {
            "name":   name,
            "sig":    sig,
            "inputs": inputs,
        }
    return result


# ─────────────────────────────────────────────
#  Token metadata
# ─────────────────────────────────────────────

def get_token_metadata(address: str) -> dict:
    addr = address.lower()
    if addr in _TOKEN_CACHE:
        return _TOKEN_CACHE[addr]

    symbol, decimals, is_nft = "???", 18, False

    # symbol() → 0x95d89b41
    try:
        raw = _eth_call(address, "0x95d89b41")
        if raw and raw != "0x" and len(raw) > 10:
            b = bytes.fromhex(raw[2:])
            if abi_decode:
                try:
                    symbol = abi_decode(["string"], b)[0]
                except Exception:
                    symbol = b.rstrip(b"\x00").decode("utf-8", errors="ignore").strip()
    except Exception:
        pass

    # decimals() → 0x313ce567
    try:
        raw = _eth_call(address, "0x313ce567")
        if raw and raw != "0x":
            decimals = int(raw, 16)
    except Exception:
        pass

    # supportsInterface(ERC721) → 0x01ffc9a7 with interfaceId 0x80ac58cd
    try:
        raw = _eth_call(address, "0x01ffc9a780ac58cd00000000000000000000000000000000000000000000000000000000")
        if raw and raw != "0x" and int(raw, 16) == 1:
            is_nft   = True
            decimals = 0
    except Exception:
        pass

    meta = {"symbol": symbol, "decimals": decimals, "is_nft": is_nft}
    _TOKEN_CACHE[addr] = meta
    return meta


# ─────────────────────────────────────────────
#  Function signature + parameter decoding
# ─────────────────────────────────────────────

def decode_function_signature(calldata: str, contract_address: str = "") -> str:
    if not calldata or calldata in ("0x", "") or len(calldata) < 10:
        return "ETH transfer"

    selector = calldata[:10].lower()
    if selector in _SELECTOR_CACHE:
        return _SELECTOR_CACHE[selector]

    # 1) Try verified ABI first
    if contract_address:
        abi = get_contract_abi(contract_address)
        if abi:
            fmap = _abi_function_map(abi)
            if selector in fmap:
                sig = fmap[selector]["sig"]
                _SELECTOR_CACHE[selector] = sig
                return sig

    # 2) Fall back to 4byte.directory
    try:
        resp    = requests.get(
            f"https://www.4byte.directory/api/v1/signatures/?hex_signature={selector}",
            timeout=10,
        ).json()
        results = resp.get("results", [])
        if results:
            sig = results[0]["text_signature"]
            _SELECTOR_CACHE[selector] = sig
            return sig
    except Exception:
        pass

    _SELECTOR_CACHE[selector] = "Unknown function"
    return "Unknown function"


def _split_types(types_str: str) -> list[str]:
    types, depth, current = [], 0, ""
    for ch in types_str:
        if   ch == "(":                 depth += 1; current += ch
        elif ch == ")":                 depth -= 1; current += ch
        elif ch == "," and depth == 0: types.append(current.strip()); current = ""
        else:                           current += ch
    if current.strip():
        types.append(current.strip())
    return types


def _format_value(val, typ: str = "") -> object:
    if isinstance(val, bytes):
        return "0x" + val.hex()
    if isinstance(val, (list, tuple)):
        return [_format_value(v, "") for v in val]
    if isinstance(val, int) and "uint" in typ:
        if val >= MAX_UINT256 - 10**18:
            return "UNLIMITED"
        return str(val)
    return val


def decode_function_params(calldata: str, function_sig: str) -> dict:
    if not abi_decode:
        return {}
    if not calldata or calldata == "0x" or len(calldata) <= 10:
        return {}
    if function_sig in ("ETH transfer", "Unknown function", ""):
        return {}
    try:
        m = re.match(r"[^(]+\((.+)\)$", function_sig)
        if not m:
            return {}
        types_str = m.group(1)
        if not types_str:
            return {}
        types   = _split_types(types_str)
        fn_name = function_sig.split("(")[0].lower()
        names   = PARAM_NAMES.get(fn_name, [f"param{i}" for i in range(len(types))])
        raw     = bytes.fromhex(calldata[10:])
        decoded = abi_decode(types, raw)
        return {
            (names[i] if i < len(names) else f"param{i}"): _format_value(v, types[i])
            for i, v in enumerate(decoded)
        }
    except Exception:
        return {}


# ─────────────────────────────────────────────
#  Event parsing (ERC-20, ERC-721, Approval, ApprovalForAll)
# ─────────────────────────────────────────────

def _is_zero(addr: str) -> bool:
    return addr.lower().replace("0x", "").lstrip("0") == ""


def parse_events(receipt: dict) -> list[dict]:
    events = []
    for log in receipt.get("logs", []):
        topics  = log.get("topics", [])
        if not topics:
            continue
        t0      = topics[0].lower()
        address = log.get("address", "")
        meta    = get_token_metadata(address)

        # ── ERC-20 or ERC-721 Transfer ──────────────────────────
        if t0 == TRANSFER_TOPIC:
            # ERC-721: 3 indexed topics (from, to, tokenId)
            if len(topics) == 4:
                from_addr = "0x" + topics[1][-40:]
                to_addr   = "0x" + topics[2][-40:]
                token_id  = str(int(topics[3], 16))
                sub_type  = (
                    "nft_mint"   if _is_zero(from_addr) else
                    "nft_burn"   if _is_zero(to_addr)   else
                    "nft_transfer"
                )
                events.append({
                    "type":           sub_type,
                    "token_contract": address,
                    "symbol":         meta["symbol"],
                    "from":           from_addr,
                    "to":             to_addr,
                    "token_id":       token_id,
                    "raw_value":      0,
                    "raw_value_str":  "N/A",
                    "amount_ui":      f"Token ID #{token_id}",
                    "decimals":       0,
                    "unlimited":      False,
                    "is_nft":         True,
                })

            # ERC-20: 2 indexed topics + data
            elif len(topics) >= 3:
                from_addr = "0x" + topics[1][-40:]
                to_addr   = "0x" + topics[2][-40:]
                value_int = int(log.get("data", "0x0"), 16)
                dec       = meta["decimals"] or 18
                amount_ui = f"{value_int / (10**dec):,.6f}" if dec else str(value_int)
                sub_type  = (
                    "erc20_mint"  if _is_zero(from_addr) else
                    "erc20_burn"  if _is_zero(to_addr)   else
                    "erc20_transfer"
                )
                events.append({
                    "type":           sub_type,
                    "token_contract": address,
                    "symbol":         meta["symbol"],
                    "from":           from_addr,
                    "to":             to_addr,
                    "token_id":       None,
                    "raw_value":      value_int,
                    "raw_value_str":  str(value_int),
                    "amount_ui":      amount_ui,
                    "decimals":       dec,
                    "unlimited":      False,
                    "is_nft":         False,
                })

        # ── ERC-20 or ERC-721 Approval ──────────────────────────
        elif t0 == APPROVAL_TOPIC and len(topics) >= 3:
            owner    = "0x" + topics[1][-40:]
            spender  = "0x" + topics[2][-40:]

            # ERC-721 single-token approval: tokenId in topics[3]
            if len(topics) == 4:
                token_id = str(int(topics[3], 16))
                events.append({
                    "type":           "nft_approval",
                    "token_contract": address,
                    "symbol":         meta["symbol"],
                    "from":           owner,
                    "to":             spender,
                    "token_id":       token_id,
                    "raw_value":      0,
                    "raw_value_str":  "N/A",
                    "amount_ui":      f"Token ID #{token_id}",
                    "decimals":       0,
                    "unlimited":      False,
                    "is_nft":         True,
                })
            else:
                value_int = int(log.get("data", "0x0"), 16)
                dec       = meta["decimals"] or 18
                unlimited = value_int >= MAX_UINT256 - 10**18
                amount_ui = "UNLIMITED" if unlimited else f"{value_int / (10**dec):,.6f}"
                events.append({
                    "type":           "erc20_approval",
                    "token_contract": address,
                    "symbol":         meta["symbol"],
                    "from":           owner,
                    "to":             spender,
                    "token_id":       None,
                    "raw_value":      value_int,
                    "raw_value_str":  str(value_int),
                    "amount_ui":      amount_ui,
                    "decimals":       dec,
                    "unlimited":      unlimited,
                    "is_nft":         False,
                })

        # ── ERC-721 ApprovalForAll ──────────────────────────────
        elif t0 == APPROVAL_FOR_ALL_TOPIC and len(topics) >= 3:
            owner    = "0x" + topics[1][-40:]
            operator = "0x" + topics[2][-40:]
            approved = bool(int(log.get("data", "0x0"), 16))
            events.append({
                "type":           "approval_for_all",
                "token_contract": address,
                "symbol":         meta["symbol"],
                "from":           owner,
                "to":             operator,
                "token_id":       None,
                "raw_value":      int(approved),
                "raw_value_str":  "Approved" if approved else "Revoked",
                "amount_ui":      "ALL NFTs" if approved else "Revoked",
                "decimals":       0,
                "unlimited":      approved,
                "is_nft":         True,
            })

    return events


# ─────────────────────────────────────────────
#  Classification
# ─────────────────────────────────────────────

def classify_transaction(function_sig: str, events: list[dict]) -> str:
    sig = (function_sig or "").lower()

    if sig.startswith("approve("):            return "erc20_approval"
    if sig.startswith("setapprovalforall("):  return "nft_approval_for_all"
    if "swap" in sig:                         return "dex_swap"
    if sig.startswith("transfer("):          return "erc20_transfer"
    if sig.startswith("transferfrom("):      return "erc20_transfer"
    if sig.startswith("safetransferfrom("):  return "nft_transfer"
    if sig.startswith("mint("):              return "mint"
    if sig.startswith("burn("):              return "burn"
    if sig.startswith("deposit("):           return "deposit"
    if sig.startswith("withdraw("):          return "withdrawal"
    if sig == "eth transfer":                return "eth_transfer"

    # Infer from events when function name is unclear
    types = {e["type"] for e in events}
    if "approval_for_all" in types:          return "nft_approval_for_all"
    if "nft_transfer" in types:              return "nft_transfer"
    if "nft_mint"     in types:              return "mint"
    if "erc20_approval" in types:            return "erc20_approval"
    if "erc20_transfer" in types or "erc20_mint" in types or "erc20_burn" in types:
        return "erc20_transfer"

    return "unknown"


# ─────────────────────────────────────────────
#  Risk scoring
# ─────────────────────────────────────────────

def compute_risk(
    tx_type: str,
    events: list[dict],
    function_params: dict,
    to_addr: str,
) -> tuple[list[str], int]:
    """
    Returns (flags, score).
    Score 0–100: 0–30 Low, 31–60 Medium, 61–100 High.
    """
    flags: list[str] = []

    # Approval checks
    if tx_type in ("erc20_approval", "nft_approval_for_all"):
        unlimited = (
            function_params.get("amount") == "UNLIMITED"
            or any(e.get("unlimited") for e in events)
        )
        if tx_type == "nft_approval_for_all":
            flags.append("approval_for_all")
        elif unlimited:
            flags.append("unlimited_approval")
        else:
            amount_str = function_params.get("amount", "0")
            try:
                amount_int = int(amount_str)
                if amount_int > 10**24:
                    flags.append("large_approval")
                else:
                    flags.append("limited_approval")
            except (ValueError, TypeError):
                flags.append("limited_approval")

    # Contract interaction
    if tx_type not in ("eth_transfer",):
        flags.append("contract_interaction")

    # Unknown function
    if function_params == {} and tx_type == "unknown":
        flags.append("unknown_function")

    # NFT transfer risk
    if tx_type == "nft_transfer":
        flags.append("nft_transfer")

    # Zero address interaction (other than mint/burn)
    if tx_type not in ("mint", "burn", "erc20_mint", "erc20_burn", "nft_mint", "nft_burn"):
        if _is_zero(to_addr):
            flags.append("zero_address_interaction")

    # Multi-token drain heuristic (>3 transfers of different tokens)
    transfer_contracts = {
        e["token_contract"]
        for e in events
        if e["type"] in ("erc20_transfer", "nft_transfer")
    }
    if len(transfer_contracts) >= 3:
        flags.append("multi_token_drain")

    # Compute score
    score = min(100, sum(RISK_WEIGHTS.get(f, 0) for f in flags))

    return flags, score


def risk_band(score: int) -> str:
    if score >= 61: return "🔴 High Risk"
    if score >= 31: return "🟡 Medium Risk"
    return "🟢 Low Risk"


# ─────────────────────────────────────────────
#  Plain English
# ─────────────────────────────────────────────

def shorten(addr: str) -> str:
    if not isinstance(addr, str) or not addr.startswith("0x") or len(addr) < 10:
        return addr or "—"
    return addr[:6] + "…" + addr[-4:]


def build_plain_english(
    tx: dict,
    tx_type: str,
    function_sig: str,
    events: list[dict],
    function_params: dict,
    risk_flags: list[str],
    status: str,
) -> str:
    if status == "failed":
        return (
            "This transaction was reverted (failed on-chain). "
            "No state was changed — your assets are safe, but gas was consumed."
        )

    from_s    = shorten(tx.get("from", "—"))
    to_s      = shorten(tx.get("to", "—"))
    value_eth = int(tx.get("value", "0x0"), 16) / 10**18

    approvals  = [e for e in events if "approval" in e["type"]]
    transfers  = [e for e in events if "transfer" in e["type"] and "approval" not in e["type"]]
    mints      = [e for e in events if "mint"     in e["type"]]
    burns      = [e for e in events if "burn"     in e["type"]]

    if tx_type == "erc20_approval":
        symbol    = approvals[0]["symbol"] if approvals else "tokens"
        spender   = shorten(function_params.get("spender", tx.get("to", "—")))
        amount    = function_params.get("amount", "?")
        unlimited = "unlimited_approval" in risk_flags
        if unlimited:
            return (
                f"You granted UNLIMITED {symbol} spending permission to {spender}. "
                "This approval persists permanently on-chain until you explicitly revoke it — "
                "that contract can drain ALL your tokens at any time."
            )
        return (
            f"You approved {amount} {symbol} to be spendable by {spender}. "
            "This permission remains valid until fully consumed or revoked."
        )

    if tx_type == "nft_approval_for_all":
        symbol   = approvals[0]["symbol"] if approvals else "NFTs"
        operator = shorten(function_params.get("operator", tx.get("to", "—")))
        approved = function_params.get("approved", True)
        if approved:
            return (
                f"You granted full control over ALL your {symbol} NFTs to {operator}. "
                "This operator can transfer every token in this collection on your behalf."
            )
        return f"You revoked the approval for {operator} to manage your {symbol} NFTs."

    if tx_type == "dex_swap":
        if len(transfers) >= 2:
            return (
                f"You swapped {transfers[0]['amount_ui']} {transfers[0]['symbol']} "
                f"→ {transfers[-1]['amount_ui']} {transfers[-1]['symbol']} "
                "via a DEX router."
            )
        return (
            f"You called `{function_sig}` on {to_s}. "
            "Tokens were exchanged via a DEX router — check the Transfers table for details."
        )

    if tx_type == "nft_transfer":
        if transfers:
            t = transfers[0]
            return (
                f"You transferred {t['symbol']} NFT #{t.get('token_id', '?')} "
                f"from {shorten(t['from'])} to {shorten(t['to'])}."
            )

    if tx_type in ("erc20_transfer",):
        if transfers:
            t = transfers[0]
            return (
                f"You sent {t['amount_ui']} {t['symbol']} "
                f"from {shorten(t['from'])} to {shorten(t['to'])}."
            )

    if tx_type == "mint":
        if mints:
            t = mints[0]
            return f"You minted {t['amount_ui']} {t['symbol']} to {shorten(t['to'])}."
        return f"You called `{function_sig}` to mint tokens on {to_s}."

    if tx_type == "burn":
        if burns:
            t = burns[0]
            return f"You burned {t['amount_ui']} {t['symbol']} from {shorten(t['from'])}."
        return f"You called `{function_sig}` to burn tokens on {to_s}."

    if tx_type in ("deposit", "withdrawal"):
        verb = "deposited into" if tx_type == "deposit" else "withdrew from"
        amt  = f"{transfers[0]['amount_ui']} {transfers[0]['symbol']} " if transfers else ""
        return f"You {verb} {amt}contract {to_s} via `{function_sig}`."

    if tx_type == "eth_transfer":
        return f"You sent {value_eth:.6f} ETH from {from_s} to {to_s}."

    return (
        f"You called `{function_sig}` on contract {to_s}. "
        "The decoder could not produce a more specific explanation for this interaction."
    )


# ─────────────────────────────────────────────
#  Expected vs Actual
# ─────────────────────────────────────────────

def build_expected_actual(
    tx_type: str,
    events: list[dict],
    function_params: dict,
    risk_flags: list[str],
    status: str,
) -> tuple[str, str]:
    if status == "failed":
        return (
            "You expected the transaction to execute successfully.",
            "The transaction reverted on-chain. No state was changed.",
        )

    approvals = [e for e in events if "approval" in e["type"]]
    transfers = [e for e in events if "transfer" in e["type"] and "approval" not in e["type"]]

    if tx_type == "erc20_approval":
        symbol    = approvals[0]["symbol"] if approvals else "tokens"
        unlimited = "unlimited_approval" in risk_flags
        return (
            f"You expected to allow a dApp to spend {symbol} for one specific action.",
            f"You created a {'UNLIMITED' if unlimited else 'limited'} persistent on-chain "
            f"approval for {symbol}. It remains valid until explicitly revoked.",
        )

    if tx_type == "nft_approval_for_all":
        symbol = approvals[0]["symbol"] if approvals else "NFTs"
        return (
            f"You expected to allow a specific app to move one {symbol} NFT.",
            f"You granted FULL collection-wide control over all your {symbol} NFTs. "
            "Any token in this collection can now be transferred by that operator.",
        )

    if tx_type == "dex_swap":
        actual = (
            f"You swapped {transfers[0]['amount_ui']} {transfers[0]['symbol']} "
            f"→ {transfers[-1]['amount_ui']} {transfers[-1]['symbol']}."
            if len(transfers) >= 2
            else "Tokens were exchanged. See the Transfers table."
        )
        return "You expected to swap one token for another at a specific rate.", actual

    if tx_type == "nft_transfer":
        actual = (
            f"You transferred NFT #{transfers[0].get('token_id', '?')} "
            f"({transfers[0]['symbol']}) to {shorten(transfers[0]['to'])}."
            if transfers else "An NFT was transferred — see the Transfers table."
        )
        return "You expected to send an NFT to a specific address.", actual

    if tx_type in ("erc20_transfer",):
        actual = (
            f"You sent {transfers[0]['amount_ui']} {transfers[0]['symbol']} "
            f"to {shorten(transfers[0]['to'])}."
            if transfers else "A token transfer was executed."
        )
        return "You expected to send tokens to a specific address.", actual

    if tx_type == "mint":
        return (
            "You expected to receive newly minted tokens.",
            "Tokens were minted and credited to your address.",
        )

    if tx_type == "burn":
        return (
            "You expected to permanently destroy tokens.",
            "Tokens were burned (sent to zero address) and removed from circulation.",
        )

    return (
        "You expected the transaction to do what the interface described.",
        "The transaction executed. Check the decoded action and events for details.",
    )


# ─────────────────────────────────────────────
#  Main entry point
# ─────────────────────────────────────────────

def decode_transaction(tx_hash: str) -> dict:
    tx      = get_transaction(tx_hash)
    receipt = get_receipt(tx_hash)

    
    if tx is None or receipt is None:
        raise DecoderError("Transaction not found on this chain (Etherscan returned null result).")

    if not isinstance(tx, dict):
        raise DecoderError(f"Unexpected transaction shape: {type(tx)}")
    if not isinstance(receipt, dict):
        raise DecoderError(f"Unexpected receipt shape: {type(receipt)}")

    # Status
    status_hex = receipt.get("status")
    ok     = int(status_hex, 16) == 1 if isinstance(status_hex, str) and status_hex.startswith("0x") else True
    status = "success" if ok else "failed"

    # Basic fields
    value_eth       = int(tx.get("value", "0x0"), 16) / 10**18
    calldata        = tx.get("input", "0x")
    contract_addr   = tx.get("to", "")
    gas_used        = int(receipt.get("gasUsed", "0x0"), 16)
    gas_price       = int(tx.get("gasPrice", "0x0"), 16)
    gas_cost_eth    = (gas_used * gas_price) / 10**18

    # Decode
    function_sig    = decode_function_signature(calldata, contract_addr)
    function_params = decode_function_params(calldata, function_sig)
    events          = parse_events(receipt)
    tx_type         = classify_transaction(function_sig, events)
    risk_flags, risk_score = compute_risk(tx_type, events, function_params, contract_addr)
    plain_english   = build_plain_english(
        tx, tx_type, function_sig, events, function_params, risk_flags, status
    )
    expected, actual = build_expected_actual(
        tx_type, events, function_params, risk_flags, status
    )

    return {
        "status":          status,
        "from":            tx.get("from", "—"),
        "to":              tx.get("to",   "—"),
        "value_eth":       value_eth,
        "gas_used":        gas_used,
        "gas_cost_eth":    gas_cost_eth,
        "tx_type":         tx_type,
        "function_name":   function_sig,
        "function_params": function_params,
        "events":          events,
        "risk_flags":      risk_flags,
        "risk_score":      risk_score,
        "plain_english":   plain_english,
        "expected":        expected,
        "actual":          actual,
    }
