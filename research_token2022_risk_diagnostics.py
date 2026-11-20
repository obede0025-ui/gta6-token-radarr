"""Conservative, offline Token-2022 risk diagnostics (NOT trading authorization).

Weighted risk points are policy triage weights, not calibrated probabilities.
Unverified checks remain UNKNOWN and contribute only to the possible worst
case. Even a zero lower bound cannot establish that a token is tradable.
"""
from decimal import Decimal, InvalidOperation

WEIGHTS = {
    "program": 10,
    "mint_authority": 10,
    "freeze_authority": 10,
    "metadata": 10,
    "other_extensions": 15,
    "liquidity": 15,
    "sell_route": 15,
    "holder_concentration": 10,
    "lp_control": 5,
}
assert sum(WEIGHTS.values()) == 100

HIGH_RISK_EXTENSIONS = {
    "permanentdelegate", "transferhook", "nontransferable",
    "defaultaccountstate", "transferfeeconfig", "confidentialtransfermint",
    "confidentialtransferfeeconfig", "pausableconfig", "mintcloseauthority",
}
METADATA_ONLY = {"metadatapointer", "tokenmetadata"}
MIN_LIQUIDITY_USD = Decimal("20000")


def _decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
        return amount if amount.is_finite() else None
    except (ValueError, InvalidOperation, TypeError):
        return None


def assess_token2022_risk(security, market=None):
    """Assess evidence only, with fail-closed UNKNOWN values.

    market is optional and MUST refer to the same mint and a contemporaneous
    observation; no remote request is made here. Valid market data includes:
    mint, liquidity_usd, sell_quote_available (strict boolean).
    Merely obtaining a quote does not prove fills, holder safety or LP lock.
    """
    security = security if isinstance(security, dict) else {}
    market = market if isinstance(market, dict) else {}
    checks = {}

    def put(name, status, reason):
        checks[name] = {
            "status": status,
            "weight": WEIGHTS[name],
            "reason": reason,
        }

    program = security.get("program")
    if program == "TOKEN_2022":
        put("program", "PASS", "Canonical Token-2022 program recognized")
    elif program == "UNKNOWN" or program is None:
        put("program", "UNKNOWN", "Mint program not verified")
    else:
        put("program", "FAIL", "Not a Token-2022 mint")

    for field, name in (("mint_authority", "mint_authority"),
                        ("freeze_authority", "freeze_authority")):
        if field not in security or security[field] == "UNKNOWN":
            put(name, "UNKNOWN", "Authority unverified")
        elif security[field] is None:
            put(name, "PASS", "Authority disabled")
        else:
            put(name, "FAIL", "Authority active or invalid")

    audit = security.get("metadata_audit")
    if not isinstance(audit, dict):
        put("metadata", "UNKNOWN", "Metadata not audited")
    elif audit.get("status") == "METADATA_READ_ONLY_CHECKS_CLEAR":
        put("metadata", "PASS", "Local metadata checks clear; no market certification")
    else:
        reasons = audit.get("reasons")
        known = isinstance(reasons, list) and bool(reasons)
        put("metadata", "FAIL" if known else "UNKNOWN",
            "Metadata audit flagged: " + ",".join(str(x) for x in reasons[:4])
            if known else "Metadata audit incomplete")

    ext = security.get("extensions")
    if not isinstance(ext, list) or not ext or not all(isinstance(x, str) for x in ext):
        put("other_extensions", "UNKNOWN", "Complete extension set not verified")
    else:
        kinds = {x.lower().replace("_", "").replace("-", "") for x in ext}
        risky = sorted(kinds & HIGH_RISK_EXTENSIONS)
        other = sorted(kinds - METADATA_ONLY - HIGH_RISK_EXTENSIONS)
        if risky:
            put("other_extensions", "FAIL", "Potentially restrictive extensions: " + ",".join(risky))
        elif other:
            put("other_extensions", "UNKNOWN", "Other extensions require separate audit: " + ",".join(other))
        elif isinstance(audit, dict) and audit.get("status") == "METADATA_READ_ONLY_CHECKS_CLEAR":
            put("other_extensions", "PASS", "Only metadata extensions reported in parsed mint")
        else:
            put("other_extensions", "UNKNOWN", "Extension review incomplete")

    # Do not mix an arbitrary market snapshot with the mint being assessed.
    market_valid = (isinstance(security.get("mint"), str)
                    and market.get("mint") == security["mint"])
    liq = _decimal(market.get("liquidity_usd")) if market_valid else None
    if liq is None or liq < 0:
        put("liquidity", "UNKNOWN", "Contemporaneous liquidity evidence unavailable")
    elif liq < MIN_LIQUIDITY_USD:
        put("liquidity", "FAIL", "Liquidity below research threshold of $20,000")
    else:
        put("liquidity", "PASS", "Quoted liquidity meets research threshold; not LP proof")

    sell = market.get("sell_quote_available") if market_valid else None
    if sell is True:
        put("sell_route", "PASS", "Sell quote observed; not proof of executable fill")
    elif sell is False:
        put("sell_route", "FAIL", "Sell quote unavailable for reported observation")
    else:
        put("sell_route", "UNKNOWN", "Sell quote observation unavailable")

    put("holder_concentration", "UNKNOWN", "Holder distribution has not been verified")
    put("lp_control", "UNKNOWN", "Liquidity-pool control and lock not verified")

    failed = sum(x["weight"] for x in checks.values() if x["status"] == "FAIL")
    unknown = sum(x["weight"] for x in checks.values() if x["status"] == "UNKNOWN")
    passed = sum(x["weight"] for x in checks.values() if x["status"] == "PASS")
    return {
        "mode": "TOKEN2022_OFFLINE_RISK_TRIAGE",
        "status": "RESEARCH_ONLY_BLOCKED",
        "checks": checks,
        "verified_weight": passed,
        "risk_points_min": failed,
        "risk_points_max": failed + unknown,
        "unknown_weight": unknown,
        "policy_weight_total": 100,
        "paper_trade_approval": "BLOCK",
        "live_trade_approval": "BLOCK",
        "limitations": [
            "Heuristic evidence weights, not estimated loss probabilities",
            "Missing data never counts as PASS",
            "No holder, LP, sell fill or transaction execution verification",
            "Does not change existing token security gate or historical ledger",
        ],
    }
