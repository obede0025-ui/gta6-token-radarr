"""Read-only Token-2022 metadata checks; never authorizes trading.

Inspects only the Solana jsonParsed mint account supplied by the caller.
No HTTP requests, wallet access, database writes, or interpretation of remote URI.
A passing metadata audit is NOT a mint security pass.
"""
from collections import Counter

KNOWN_METADATA_EXTENSIONS = {"metadatapointer", "tokenmetadata"}


def audit_metadata_extensions(mint, extensions):
    """Return conservative metadata findings for a Token-2022 mint.

    Only the *metadata* portion is evaluated. Other extensions and their
    permissions must be audited independently. Missing/ambiguous properties
    fail closed; no success state grants a trade permission.
    """
    result = {
        "status": "BLOCK_INCOMPLETE",
        "reasons": [],
        "metadata_pointer_immutable": False,
        "token_metadata_immutable": False,
        "metadata_mint_matches": False,
        "unreviewed_extensions": [],
        "trade_approval": "BLOCK",
    }
    reasons = result["reasons"]
    if not isinstance(extensions, list):
        reasons.append("EXTENSIONS_MISSING_OR_INVALID")
        return result

    def normalized(name):
        return name.lower().replace("_", "").replace("-", "")

    entries = {}
    counts = Counter()
    for item in extensions:
        if not isinstance(item, dict) or not isinstance(item.get("extension"), str):
            reasons.append("EXTENSION_UNKNOWN_OR_INVALID")
            continue
        name = normalized(item["extension"])
        counts[name] += 1
        if name in KNOWN_METADATA_EXTENSIONS:
            entries[name] = item.get("state")
        else:
            result["unreviewed_extensions"].append(item["extension"])

    if result["unreviewed_extensions"]:
        reasons.append("NON_METADATA_EXTENSIONS_REQUIRE_SEPARATE_AUDIT")
    for name in KNOWN_METADATA_EXTENSIONS:
        if counts[name] != 1:
            reasons.append("MISSING_OR_DUPLICATE_" + name.upper())

    pointer = entries.get("metadatapointer")
    metadata = entries.get("tokenmetadata")
    if not isinstance(pointer, dict):
        reasons.append("METADATA_POINTER_STATE_MISSING")
    else:
        if "authority" not in pointer or pointer["authority"] is not None:
            reasons.append("METADATA_POINTER_AUTHORITY_ACTIVE_OR_UNKNOWN")
        else:
            result["metadata_pointer_immutable"] = True
        if pointer.get("metadataAddress") != mint:
            reasons.append("METADATA_POINTER_ADDRESS_MISMATCH")

    if not isinstance(metadata, dict):
        reasons.append("TOKEN_METADATA_STATE_MISSING")
    else:
        if "updateAuthority" not in metadata or metadata["updateAuthority"] is not None:
            reasons.append("TOKEN_METADATA_UPDATE_AUTHORITY_ACTIVE_OR_UNKNOWN")
        else:
            result["token_metadata_immutable"] = True
        if metadata.get("mint") != mint:
            reasons.append("TOKEN_METADATA_MINT_MISMATCH")
        elif isinstance(pointer, dict) and pointer.get("metadataAddress") == mint:
            result["metadata_mint_matches"] = True
        if not isinstance(metadata.get("additionalMetadata"), list):
            reasons.append("ADDITIONAL_METADATA_UNVERIFIED")

    result["reasons"] = sorted(set(reasons))
    if not result["reasons"]:
        result["status"] = "METADATA_READ_ONLY_CHECKS_CLEAR"
    return result
