# ─────────────────────────────────────────────
#  Risk labels, descriptions, and scoring logic
#  Used by app.py for all UI risk rendering
# ─────────────────────────────────────────────

# Human-readable descriptions for every possible flag
RISK_DESCRIPTIONS: dict[str, str] = {
    "unlimited_approval": (
        "You granted unlimited token spending to a contract. "
        "This permission persists on-chain indefinitely until you manually revoke it. "
        "A malicious or compromised contract could drain your entire token balance."
    ),
    "large_approval": (
        "You approved a very large (but not unlimited) token amount to a contract. "
        "While not the maximum, this is significantly above what a single action requires. "
        "Consider revoking and re-approving only the exact amount needed."
    ),
    "limited_approval": (
        "You approved a specific, limited token amount to a contract. "
        "This is the safer pattern — the approval will be fully consumed after use. "
        "Still verify you trust the contract before signing."
    ),
    "approval_for_all": (
        "You granted full operator control over your entire NFT collection to a contract. "
        "That operator can transfer, list, or sell ALL your NFTs without further confirmation. "
        "This is the highest-risk approval pattern for NFT holders."
    ),
    "nft_transfer": (
        "An NFT was transferred in this transaction. "
        "Verify the recipient address is correct — NFT transfers are irreversible."
    ),
    "contract_interaction": (
        "Your transaction directly called a smart contract. "
        "Always verify the contract address and its reputation before signing. "
        "Unverified contracts may contain malicious logic."
    ),
    "unknown_function": (
        "The function selector could not be resolved to a known signature. "
        "This may be a custom, obfuscated, or very uncommon contract function. "
        "Treat with extreme caution — never sign unknown calldata blindly."
    ),
    "zero_address_interaction": (
        "The transaction targets the zero address (0x000…000). "
        "Outside of intentional mint/burn operations, this is highly suspicious "
        "and may indicate an attempt to destroy your assets permanently."
    ),
    "multi_token_drain": (
        "This transaction moved three or more different tokens simultaneously. "
        "This pattern is common in multi-asset drainer contracts and phishing attacks. "
        "Verify each token movement carefully before proceeding."
    ),
    "erc1155_transfer": (
        "An ERC-1155 multi-token transfer was detected. "
        "ERC-1155 tokens can represent both fungible and non-fungible assets in a single contract. "
        "Verify the token IDs and quantities are what you expected."
    ),
    "permit_approval": (
        "A gasless permit (ERC-2612) was executed, granting token spending permission "
        "via an off-chain signature replayed on-chain. This bypasses the normal approve() flow "
        "and can be harder to detect — always verify the spender address."
    ),
    "multicall": (
        "A multicall bundle was executed, combining multiple contract calls into one transaction. "
        "While efficient, this can obscure individual actions. Review each sub-action carefully "
        "to ensure nothing unexpected was included."
    ),
    "flash_loan": (
        "A flash loan was executed — tokens were borrowed and repaid within a single transaction. "
        "Flash loans are used for arbitrage, liquidations, and collateral swaps. "
        "Malicious flash loans can manipulate prices or exploit vulnerable contracts."
    ),
    "contract_creation": (
        "A new smart contract was deployed in this transaction. "
        "The contract code is now permanently on-chain. Verify the deployer is trusted "
        "and the contract source is verified on the block explorer."
    ),
}

# Advice to display alongside each flag in the UI
RISK_ADVICE: dict[str, str] = {
    "unlimited_approval":       "👉 Revoke this approval on revoke.cash or Etherscan as soon as possible.",
    "large_approval":           "👉 Consider revoking and re-approving only the exact amount you need.",
    "limited_approval":         "👉 No immediate action needed — this approval expires after use.",
    "approval_for_all":         "👉 Revoke this immediately unless you fully trust this operator.",
    "nft_transfer":             "👉 Double-check the recipient address is correct.",
    "contract_interaction":     "👉 Verify the contract address on Etherscan before signing.",
    "unknown_function":         "👉 Do not proceed unless you sourced this transaction yourself.",
    "zero_address_interaction": "👉 Stop and verify this is intentional before signing.",
    "multi_token_drain":        "👉 Do not sign — this matches known drainer contract patterns.",
    "erc1155_transfer":         "👉 Verify the token IDs and quantities before confirming.",
    "permit_approval":          "👉 Check the spender address — permits can be replayed from phishing signatures.",
    "multicall":                "👉 Inspect each sub-call individually before signing.",
    "flash_loan":               "👉 Ensure the flash loan logic is from a trusted source.",
    "contract_creation":        "👉 Verify the contract source code is published and audited.",
}

# Color theme per risk band (used by app.py for styling)
RISK_COLORS: dict[str, str] = {
    "🔴 High Risk":   "#FF4B4B",
    "🟡 Medium Risk": "#FFA500",
    "🟢 Low Risk":    "#21C55D",
}

# Score thresholds
HIGH_THRESHOLD   = 61
MEDIUM_THRESHOLD = 31


# ─────────────────────────────────────────────
#  Core functions
# ─────────────────────────────────────────────

def risk_band(score: int) -> str:
    """Return a labelled risk band string from a numeric score (0–100)."""
    if score >= HIGH_THRESHOLD:
        return "🔴 High Risk"
    if score >= MEDIUM_THRESHOLD:
        return "🟡 Medium Risk"
    return "🟢 Low Risk"


def generate_risk_label(risk_flags: list[str]) -> str:
    """
    Legacy entry point kept for backward compatibility.
    Derives a risk band from flags without a numeric score.
    Prefer risk_band(score) when a score is available.
    """
    if any(f in risk_flags for f in (
        "unlimited_approval", "approval_for_all",
        "unknown_function",   "zero_address_interaction", "multi_token_drain",
    )):
        return "🔴 High Risk"
    if len(risk_flags) > 0:
        return "🟡 Medium Risk"
    return "🟢 Low Risk"


def describe_risk_flags(risk_flags: list[str]) -> list[dict]:
    """
    Return a list of dicts with flag, description, and advice
    for every flag in risk_flags, ready to render in the UI.
    """
    return [
        {
            "flag":        flag,
            "description": RISK_DESCRIPTIONS.get(flag, "No description available."),
            "advice":      RISK_ADVICE.get(flag, ""),
        }
        for flag in risk_flags
    ]


def score_to_bar(score: int) -> str:
    """
    Return a simple ASCII progress bar string representing the risk score.
    Example: score=75 → '███████░░░ 75/100'
    """
    filled = round(score / 10)
    empty  = 10 - filled
    return f"{'█' * filled}{'░' * empty}  {score}/100"


def get_risk_color(score: int) -> str:
    """Return the hex color string for the current risk band."""
    return RISK_COLORS[risk_band(score)]


def summarize_risk(risk_flags: list[str], risk_score: int) -> str:
    """
    Return a one-sentence plain English risk summary for the Plain English section.
    """
    band = risk_band(risk_score)

    if not risk_flags:
        return "No suspicious patterns were detected in this transaction."

    if "multi_token_drain" in risk_flags:
        return (
            f"⛔ {band} — This transaction moves multiple tokens at once, "
            "a pattern associated with asset drainer contracts."
        )
    if "unlimited_approval" in risk_flags:
        return (
            f"🔴 {band} — You granted unlimited spending permission to a contract. "
            "Revoke this approval immediately if you did not intend to."
        )
    if "approval_for_all" in risk_flags:
        return (
            f"🔴 {band} — You gave full NFT collection control to an operator. "
            "Revoke this immediately unless you fully trust that address."
        )
    if "unknown_function" in risk_flags:
        return (
            f"🔴 {band} — The function called could not be identified. "
            "Never sign unknown calldata from an untrusted source."
        )
    if "zero_address_interaction" in risk_flags:
        return (
            f"🔴 {band} — This transaction targets the zero address, "
            "which is irreversible and potentially destructive."
        )
    if "large_approval" in risk_flags:
        return (
            f"🟡 {band} — You approved a large token amount. "
            "Consider limiting approvals to the exact amount required."
        )
    if "nft_transfer" in risk_flags:
        return (
            f"🟡 {band} — An NFT was transferred. "
            "Confirm the recipient address is correct — this cannot be undone."
        )
    if "contract_interaction" in risk_flags:
        return (
            f"🟡 {band} — This transaction interacts with a smart contract. "
            "Verify the contract address before signing."
        )
    if "permit_approval" in risk_flags:
        return (
            f"🟡 {band} — A gasless permit was used to approve token spending. "
            "Verify the spender is legitimate — permits can be replayed from phishing signatures."
        )
    if "multicall" in risk_flags:
        return (
            f"🟡 {band} — A multicall bundle was executed. "
            "Review each sub-action to ensure nothing unexpected was included."
        )
    if "flash_loan" in risk_flags:
        return (
            f"🟡 {band} — A flash loan was executed within this transaction. "
            "Ensure the logic is from a trusted and audited source."
        )
    if "erc1155_transfer" in risk_flags:
        return (
            f"🟡 {band} — ERC-1155 multi-token assets were transferred. "
            "Verify token IDs and quantities are correct."
        )
    if "contract_creation" in risk_flags:
        return (
            f"🟡 {band} — A new smart contract was deployed. "
            "Verify the deployer and check if the source is published."
        )

    return f"{band} — Score: {risk_score}/100. Review the flags below for details."
