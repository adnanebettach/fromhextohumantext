import streamlit as st

from interpretation import (
    generate_risk_label,
    describe_risk_flags,
    score_to_bar,
    get_risk_color,
    summarize_risk,
    risk_band,
)
from decoder import (
    decode_transaction,
    DecoderError,
    shorten,
    MAX_UINT256,
)

# ─────────────────────────────────────────────
#  Page config
# ─────────────────────────────────────────────

st.set_page_config(
    page_title="Transaction Decoder: From Hex to Human",
    page_icon="🔍",
    layout="wide",
)

st.title("🔍 Transaction Decoder: From Hex to Human")
st.caption(
    "Paste any Ethereum transaction hash to get a plain-English explanation "
    "of what you signed — including transfers, approvals, NFTs, swaps, and risk flags."
)

# ─────────────────────────────────────────────
#  Sidebar
# ─────────────────────────────────────────────

with st.sidebar:
    st.header("⚙️ Settings")
    show_raw_params = st.toggle("Show raw decoded parameters", value=True)
    show_raw_events = st.toggle("Show raw event table",        value=True)
    show_risk_advice = st.toggle("Show risk advice",           value=True)

    st.divider()
    st.subheader("📚 Transaction Type Legend")
    legend = {
        "erc20_transfer":      "ERC-20 token transfer",
        "erc20_approval":      "ERC-20 spend approval",
        "nft_transfer":        "ERC-721 NFT transfer",
        "nft_approval_for_all":"ERC-721 full collection approval",
        "dex_swap":            "DEX token swap",
        "mint":                "Token minting",
        "burn":                "Token burning",
        "deposit":             "Contract deposit",
        "withdrawal":          "Contract withdrawal",
        "eth_transfer":        "Plain ETH transfer",
        "unknown":             "Unknown / custom function",
    }
    for k, v in legend.items():
        st.markdown(f"- **`{k}`** — {v}")

    st.divider()
    st.caption(
        "Data sources: Etherscan API v2 · 4byte.directory · "
        "on-chain eth_call for token metadata."
    )

# ─────────────────────────────────────────────
#  Mock fallback (for demo / no API key)
# ─────────────────────────────────────────────

def mock_decode(_tx_hash: str) -> dict:
    return {
        "status":        "success",
        "from":          "0xA1b2C3d4E5f6A7b8C9d0E1f2A3b4C5d6E7f8A9b0",
        "to":            "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "value_eth":     0.0,
        "gas_used":      65432,
        "gas_cost_eth":  0.002,
        "tx_type":       "erc20_approval",
        "function_name": "approve(address,uint256)",
        "function_params": {
            "spender": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
            "amount":  "UNLIMITED",
        },
        "events": [
            {
                "type":           "erc20_approval",
                "token_contract": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
                "symbol":         "USDT",
                "from":           "0xA1b2C3d4E5f6A7b8C9d0E1f2A3b4C5d6E7f8A9b0",
                "to":             "0xE592427A0AEce92De3Edee1F18E0157C05861564",
                "token_id":       None,
                "raw_value":      MAX_UINT256,
                "raw_value_str":  str(MAX_UINT256),
                "amount_ui":      "UNLIMITED",
                "decimals":       6,
                "unlimited":      True,
                "is_nft":         False,
            }
        ],
        "risk_flags":    ["unlimited_approval", "contract_interaction"],
        "risk_score":    100,
        "plain_english": (
            "You granted UNLIMITED USDT spending permission to 0xE592…1564 (Uniswap V3 Router). "
            "This approval persists permanently on-chain until you explicitly revoke it."
        ),
        "expected": "You expected to allow a dApp to spend USDT for one specific action.",
        "actual": (
            "You created a persistent, UNLIMITED on-chain approval for USDT. "
            "It remains valid until you revoke it."
        ),
    }


# ─────────────────────────────────────────────
#  UI helpers
# ─────────────────────────────────────────────

def risk_badge(score: int) -> None:
    band  = risk_band(score)
    color = get_risk_color(score)
    bar   = score_to_bar(score)
    st.markdown(
        f"<div style='padding:12px; border-radius:8px; background:{color}22; "
        f"border-left: 4px solid {color};'>"
        f"<b style='font-size:1.1em; color:{color};'>{band}</b>"
        f"<br><code style='color:{color};'>{bar}</code></div>",
        unsafe_allow_html=True,
    )


def event_type_badge(etype: str) -> str:
    badges = {
        "erc20_transfer":      "🔵 Transfer",
        "erc20_approval":      "🟠 Approval",
        "erc20_mint":          "🟢 Mint",
        "erc20_burn":          "🔥 Burn",
        "nft_transfer":        "🖼️ NFT Transfer",
        "nft_mint":            "🟢 NFT Mint",
        "nft_burn":            "🔥 NFT Burn",
        "nft_approval":        "🟠 NFT Approval",
        "approval_for_all":    "🚨 Approval For All",
    }
    return badges.get(etype, f"❓ {etype}")


def format_status(status: str) -> str:
    return "✅ Success" if status == "success" else "❌ Failed"


# ─────────────────────────────────────────────
#  Input
# ─────────────────────────────────────────────

tx_hash = st.text_input(
    "Transaction hash",
    placeholder="0x… (paste an Ethereum transaction hash)",
)

col_btn, col_example, _ = st.columns([1, 2, 4])
with col_btn:
    decode_clicked = st.button("🔍 Decode", type="primary", use_container_width=True)
with col_example:
    st.caption("Example: any 0x… hash from etherscan.io")

# ─────────────────────────────────────────────
#  Empty state
# ─────────────────────────────────────────────

st.divider()

if not decode_clicked:
    st.subheader("Transaction Overview")
    st.info("Waiting for input — paste a transaction hash above and click **Decode**.")
    for section in [
        "Decoded Action",
        "Transfers & Events",
        "Risk Assessment",
        "Expected vs Actual",
        "Plain English Summary",
    ]:
        st.subheader(section)
        st.write("—")
    st.stop()

# ─────────────────────────────────────────────
#  Validation
# ─────────────────────────────────────────────

if not tx_hash or not tx_hash.startswith("0x") or len(tx_hash) < 10:
    st.warning("⚠️ Please paste a valid transaction hash starting with 0x.")
    st.stop()

# ─────────────────────────────────────────────
#  Decode
# ─────────────────────────────────────────────

result:   dict = {}
use_mock: bool = False
error_msg: str = ""

try:
    with st.spinner("Fetching and decoding transaction from Ethereum…"):
        result = decode_transaction(tx_hash)
except DecoderError as e:
    error_msg = str(e)
    use_mock  = True
except Exception as e:
    error_msg = f"Unexpected error: {e}"
    use_mock  = True

if use_mock:
    if "not set" in error_msg or "API_KEY" in error_msg:
        st.warning(
            "⚠️ **No Etherscan API key detected.**  \n"
            "Set `ETHERSCAN_API_KEY` as an environment variable to enable live decoding.  \n"
            "Showing a mock example transaction for demonstration."
        )
    else:
        st.warning(
            f"⚠️ **Live decoding failed** — `{error_msg}`  \n"
            "Showing a mock example transaction as fallback."
        )
    result = mock_decode(tx_hash)

flags      = result.get("risk_flags",  [])
risk_score = result.get("risk_score",  0)
events     = result.get("events",      [])
params     = result.get("function_params", {})

# ─────────────────────────────────────────────
#  1. Transaction Overview
# ─────────────────────────────────────────────

st.subheader("📋 Transaction Overview")

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Status",      format_status(result.get("status", "unknown")))
c2.metric("Type",        result.get("tx_type", "—").replace("_", " ").title())
c3.metric("Value (ETH)", f"{result.get('value_eth', 0):.6f}")
c4.metric("Gas Used",    f"{result.get('gas_used', 0):,}")
c5.metric("Gas Cost",    f"{result.get('gas_cost_eth', 0):.5f} ETH")
c6.metric("Events",      len(events))

st.markdown(
    f"**From:** `{result.get('from', '—')}`  \n"
    f"**To:** `{result.get('to', '—')}`"
)

# ─────────────────────────────────────────────
#  2. Decoded Action
# ─────────────────────────────────────────────

st.divider()
st.subheader("⚙️ Decoded Action")

st.markdown(
    f"**Function:** `{result.get('function_name', '—')}`  &nbsp;|&nbsp;  "
    f"**Type:** `{result.get('tx_type', '—').replace('_', ' ').title()}`"
)

if params and show_raw_params:
    st.markdown("**Decoded Parameters:**")
    # Highlight UNLIMITED in red
    import json
    params_str = json.dumps(params, indent=2)
    if "UNLIMITED" in params_str:
        st.code(params_str, language="json")
        st.error("⚠️ One or more parameters contain **UNLIMITED** values.")
    else:
        st.code(params_str, language="json")
elif not params:
    st.write("No parameters decoded (plain ETH transfer or unknown calldata).")

# ─────────────────────────────────────────────
#  3. Transfers & Events
# ─────────────────────────────────────────────

st.divider()
st.subheader("📦 Transfers & Events")

if events and show_raw_events:
    table_rows = []
    for e in events:
        row = {
            "Type":     event_type_badge(e.get("type", "—")),
            "Token":    e.get("symbol", "—"),
            "Contract": shorten(e.get("token_contract", "")),
            "From":     shorten(e.get("from", "")),
            "To":       shorten(e.get("to", "")),
            "Amount":   e.get("amount_ui", e.get("raw_value_str", "—")),
        }
        if e.get("is_nft") and e.get("token_id"):
            row["Token ID"] = f"#{e['token_id']}"
        else:
            row["Token ID"] = "—"
        table_rows.append(row)
    st.table(table_rows)

    # Highlight unlimited approvals in events
    unlimited_events = [e for e in events if e.get("unlimited")]
    if unlimited_events:
        for ue in unlimited_events:
            st.error(
                f"🚨 **Unlimited approval detected** — `{ue['symbol']}` approved to "
                f"`{shorten(ue['to'])}` with no spending limit."
            )
elif not events:
    st.write("No ERC-20 transfers, NFT movements, or approvals detected in this transaction.")

# ─────────────────────────────────────────────
#  4. Risk Assessment
# ─────────────────────────────────────────────

st.divider()
st.subheader("🛡️ Risk Assessment")

risk_badge(risk_score)
st.caption(summarize_risk(flags, risk_score))

if flags:
    st.markdown("**Detected Risk Flags:**")
    flag_details = describe_risk_flags(flags)
    for fd in flag_details:
        with st.expander(f"`{fd['flag']}`"):
            st.write(fd["description"])
            if show_risk_advice and fd["advice"]:
                st.info(fd["advice"])
else:
    st.success("✅ No risk flags detected for this transaction.")

# ─────────────────────────────────────────────
#  5. Expected vs Actual
# ─────────────────────────────────────────────

st.divider()
st.subheader("🔁 Expected vs Actual")

colA, colB = st.columns(2)
with colA:
    st.markdown("**🧠 What you thought you signed:**")
    st.info(result.get("expected", "—"))
with colB:
    st.markdown("**⛓️ What actually happened on-chain:**")
    band = risk_band(risk_score)
    if "High"   in band: st.error(result.get("actual", "—"))
    elif "Medium" in band: st.warning(result.get("actual", "—"))
    else:                  st.success(result.get("actual", "—"))

# ─────────────────────────────────────────────
#  6. Plain English Summary
# ─────────────────────────────────────────────

st.divider()
st.subheader("📝 Plain English Summary")

band  = risk_band(risk_score)
color = get_risk_color(risk_score)
st.markdown(
    f"<div style='padding:16px; border-radius:8px; background:{color}15; "
    f"border-left:5px solid {color}; font-size:1.05em;'>"
    f"{result.get('plain_english', '—')}"
    f"</div>",
    unsafe_allow_html=True,
)

# ─────────────────────────────────────────────
#  7. Raw Transaction Hash
# ─────────────────────────────────────────────

st.divider()
with st.expander("🔗 View on Etherscan"):
    chain_map = {"1": "etherscan.io", "137": "polygonscan.com", "56": "bscscan.com"}
    import os
    chain = os.getenv("ETHERSCAN_CHAIN_ID", "1")
    explorer = chain_map.get(chain, "etherscan.io")
    url = f"https://{explorer}/tx/{tx_hash}"
    st.markdown(f"[Open transaction on {explorer}]({url})")
    st.code(tx_hash)
