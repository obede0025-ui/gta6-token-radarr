"""V3.1 strict evidence gate for prospective paper-entry RESEARCH.

Pure function: no network, wallets, order routes, portfolio writes or actions.
Requires independently contemporaneous quote and mint-security evidence.
'PASS_RESEARCH_ONLY' is not permission to execute a simulated/real trade:
its paper_entry_permission and live_trade_permission remain BLOCK.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

MIN_LIQUIDITY_USD=Decimal("20000")
MIN_5M_VOLUME_USD=Decimal("250")
MAX_MARK_AGE_SECONDS=120
MAX_EVIDENCE_AGE_SECONDS=120
MAX_ROUNDTRIP_LOSS_PCT=Decimal("3")
MAX_PAIR_AGE_HOURS=Decimal("24")
MAX_STAKE_USDC=Decimal("25")
BLOCKED_EXTENSIONS={"transferhook","nontransferable","permanentdelegate",
    "defaultaccountstate","transferfeeconfig","confidentialtransfermint",
    "confidentialtransferfeeconfig","pausableconfig"}
EXPECTED_PROGRAM="SPL_TOKEN"

def dec(value):
    if value is None or isinstance(value,bool):return None
    try:
        num=Decimal(str(value))
        return num if num.is_finite() else None
    except (ValueError,InvalidOperation,TypeError):return None

def seconds_since(now,value):
    if not isinstance(value,str) or not value:return None
    try:
        d=datetime.fromisoformat(value.replace("Z","+00:00"))
        if d.tzinfo is None:return None
        return Decimal(str((now-d.astimezone(timezone.utc)).total_seconds()))
    except (ValueError,TypeError,OverflowError):return None

def evaluate(candidate,route,security,now=None):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError("now needs timezone")
    now=now.astimezone(timezone.utc)
    reasons=[]
    age=seconds_since(now,candidate.get("scanned_at_utc"))
    if age is None or age<0 or age>MAX_MARK_AGE_SECONDS:
        reasons.append("MARK_STALE_OR_UNKNOWN")
    for field,minimum,code in (
        ("liquidity_usd",MIN_LIQUIDITY_USD,"LOW_OR_UNKNOWN_LIQUIDITY"),
        ("volume_5m_usd",MIN_5M_VOLUME_USD,"LOW_OR_UNKNOWN_VOLUME")):
        value=dec(candidate.get(field))
        if value is None or value<minimum:reasons.append(code)
    buys=dec(candidate.get("buys_m5"))
    sells=dec(candidate.get("sells_m5"))
    if buys is None or sells is None or buys<=sells or sells<0:
        reasons.append("BUY_PRESSURE_NOT_VERIFIED")
    pair_age=dec(candidate.get("pair_age_hours"))
    if pair_age is None or not 0<=pair_age<=MAX_PAIR_AGE_HOURS:
        reasons.append("PAIR_AGE_INVALID")
    if not isinstance(route,dict) or route.get("mint")!=candidate.get("mint"):
        reasons.append("QUOTE_EVIDENCE_MISSING_OR_WRONG_MINT")
    else:
        qage=seconds_since(now,route.get("checked_utc"))
        if qage is None or qage<0 or qage>MAX_EVIDENCE_AGE_SECONDS:
            reasons.append("QUOTE_EVIDENCE_STALE")
        if route.get("buy_quote_only") is not True or route.get("sell_quote_only") is not True:
            reasons.append("BOTH_QUOTE_ONLY_ROUTES_REQUIRED")
        if route.get("buy_status")!="OK" or route.get("sell_status")!="OK":
            reasons.append("BUY_SELL_ROUTE_UNAVAILABLE")
        stake=dec(route.get("stake_usdc"))
        if stake is None or stake<=0 or stake>MAX_STAKE_USDC:
            reasons.append("STAKE_INVALID")
        out=dec(route.get("immediate_sell_usdc"))
        if out is None or out<=0 or stake is None or stake<=0:
            reasons.append("REVERSAL_QUOTE_INVALID")
        elif out<stake*(Decimal("1")-MAX_ROUNDTRIP_LOSS_PCT/100):
            reasons.append("ROUNDTRIP_COST_EXCESSIVE")
        # Guard against quote evidence for another discovery pair:
        # a routing aggregator may use multiple pools, so record the
        # discovery pair identifier on quote evidence, not route identity.
        if route.get("discovery_pair")!=candidate.get("pair"):
            reasons.append("DISCOVERY_PAIR_EVIDENCE_MISMATCH")
    if not isinstance(security,dict) or security.get("mint")!=candidate.get("mint"):
        reasons.append("SECURITY_EVIDENCE_MISSING_OR_WRONG_MINT")
    else:
        sage=seconds_since(now,security.get("checked_utc"))
        if sage is None or sage<0 or sage>MAX_EVIDENCE_AGE_SECONDS:
            reasons.append("SECURITY_EVIDENCE_STALE")
        if security.get("program")!=EXPECTED_PROGRAM:
            reasons.append("TOKEN_PROGRAM_UNVERIFIED")
        if security.get("mint_authority") is not None:
            reasons.append("MINT_AUTHORITY_NOT_DISABLED")
        if security.get("freeze_authority") is not None:
            reasons.append("FREEZE_AUTHORITY_NOT_DISABLED")
        exts=security.get("extensions")
        if not isinstance(exts,list):
            reasons.append("TOKEN_EXTENSIONS_UNKNOWN")
        elif any(not isinstance(v,str) or v.lower().replace("_","").replace("-","") in BLOCKED_EXTENSIONS for v in exts):
            reasons.append("TOKEN_EXTENSIONS_RISK")
        if security.get("security_gate")!="BASIC_CHECKS_CLEAR":
            reasons.append("TOKEN_SECURITY_NOT_CLEARED")
    reasons=sorted(set(reasons))
    return {
        "mint":candidate.get("mint"),"pair":candidate.get("pair"),
        "evidence_state":"PASS_RESEARCH_ONLY" if not reasons else "BLOCKED",
        "reasons":reasons,
        "paper_entry_permission":"BLOCK",
        "live_trade_permission":"BLOCK",
        "execution_verified":False,
        "limitations":[
            "This is a prospective pre-entry research classification, not an executable fill.",
            "No LP lock, holder concentration, honeypot or transaction-inclusion test.",
            "A passing quote route is not guaranteed to execute.",
            "No active paper engine imports this module until separately validated."
        ]
    }
