import os
import streamlit as st

from interpretation import generate_risk_label
from decoder import decode_transaction, DecoderError

# ----------------------------
# Page setup
# ----------------------------

st.set_page_config(page_title="Transaction Decoder", layout="wide")

st.title("Transaction Decoder: From Hex to Human")
st.caption("Translate raw Ethereum transactions into a human-readable explanation.")

# ----------------------------
# Input
# ----------------------------

tx_hash = st.text_input(
    "Transaction hash",
    placeholder="0x… (paste an Ethereum transaction hash)",
)

col1, col2 = st.columns([1, 3])
with col1:
    decode_clicked = st.button("Decode", type="primary")

# ----------------------------
# Helpers
# ----------------------------

def risk_badge(risk_flags) -> None:
    """Display badge using interpretation.generate_risk_label."""
    label = generate_risk_label(risk_flags)
    if "High" in label:
        st.error(label)
    elif "Medium" in label:
        st.warning(label)
    else:
        st.success(label)


def mock_decode(_tx_hash: str) -> dict:
    """
    Fallback decoded output when no API key is set or Etherscan fails.
    Matches your previous mock structure.
    """
    return {
        "status": "success",
        "from": "0xA1b2…c3D4",
        "to": "0xDeF0…1234",
        "value_eth": 0.0,
        "function_name": "approve(address,uint256)",
        "function_params": {
            "spender": "0xUniswapRouter…",
            "amount": "UNLIMITED",
        },
        "events": [
            {
                "type": "approval",
                "token": "USDT",
                "owner": "0xA1b2…c3D4",
                "spender": "0xUniswapRouter…",
                "amount": "UNLIMITED",
            },
        ],
        "risk_flags": ["unlimited_approval", "contract_interaction"],
        "plain_english": (
            "You approved UNLIMITED USDT spending to 0xUniswapRouter…. "
            "This permission remains active until you revoke it."
        ),
        "expected": "You expected to allow a dApp to spend tokens for one action.",
        "actual": "You granted a persistent (unlimited) spending permission until revoked.",
    }

# ----------------------------
# Output
# ----------------------------

st.divider()

if not decode_clicked:
    # Empty state so it matches your slide layout
    st.subheader("Transaction Overview")
    st.info("Waiting for input… paste a transaction hash and click **Decode**.")

    st.subheader("Decoded Action")
    st.write("—")

    st.subheader("Transfers & Approvals")
    st.write("—")

    st.subheader("Risk Flags")
    st.write("—")

    st.subheader("Expected vs Actual")
    st.write("—")

    st.subheader("Plain English Summary")
    st.write("—")

else:
    if not tx_hash or not tx_hash.startswith("0x") or len(tx_hash) < 10:
        st.warning("Please paste a valid transaction hash starting with 0x.")
        st.stop()

    use_mock = False
    try:
        with st.spinner("Decoding transaction from Ethereum…"):
            result = decode_transaction(tx_hash)
    except (DecoderError, Exception) as e:
        st.warning(f"Live decoding failed ({e}); falling back to example mock.")
        use_mock = True

    if use_mock:
        with st.spinner("Loading mock example…"):
            result = mock_decode(tx_hash)

    # 1) Transaction Overview
    st.subheader("Transaction Overview")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Status", result.get("status", "unknown"))
    c2.metric("Value (ETH)", str(result.get("value_eth", "—")))
    c3.write(f"**From:** {result.get('from', '—')}")
    c4.write(f"**To:** {result.get('to', '—')}")

    # 2) Decoded Action
    st.subheader("Decoded Action")
    st.write(f"**Function:** `{result.get('function_name', '—')}`")
    params = result.get("function_params", {})
    if params:
        st.write("**Parameters:**")
        st.json(params)
    else:
        st.write("No parameters decoded yet.")

    # 3) Transfers & Approvals
    st.subheader("Transfers & Approvals")
    events = result.get("events", [])
    if events:
        # Show selected fields and use the stringified value
        table_rows = [
            {
                "token_contract": e.get("token_contract"),
                "from": e.get("from"),
                "to": e.get("to"),
                "amount_raw": e.get("raw_value_str", str(e.get("raw_value"))),
            }
            for e in events
        ]
        st.table(table_rows)
    else:
        st.write("No ERC‑20 transfers detected.")


    # 4) Risk Flags
    st.subheader("Risk Flags")
    flags = result.get("risk_flags", [])
    risk_badge(flags)
    if flags:
        st.write("Flags:", ", ".join([f"`{x}`" for x in flags]))
    else:
        st.write("No risk flags.")

    # 5) Expected vs Actual
    st.subheader("Expected vs Actual")
    colA, colB = st.columns(2)
    with colA:
        st.write("**Expected (user intent):**")
        st.write(result.get("expected", "—"))
    with colB:
        st.write("**Actual (on-chain effect):**")
        st.write(result.get("actual", "—"))

    # 6) Plain English Summary
    st.subheader("Plain English Summary")
    st.write(result.get("plain_english", "—"))
