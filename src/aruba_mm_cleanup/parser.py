"""Parse Aruba MM global user table output."""

from __future__ import annotations

import re

from .models import ParseDecision, ParseResult, UserEntry


_MAC_PATTERNS = (
    re.compile(r"(?i)\b([0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b"),
    re.compile(r"(?i)\b[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}\b"),
)
_IPV4_PATTERN = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")


def normalize_mac(value: str) -> str:
    """Return aa:bb:cc:dd:ee:ff form, or an empty string when invalid."""
    text = value.strip().strip(",;()[]{}<>")
    hex_chars = re.sub(r"[^0-9A-Fa-f]", "", text)
    if len(hex_chars) != 12 or not re.fullmatch(r"(?i)[0-9a-f]{12}", hex_chars):
        return ""
    return ":".join(hex_chars[index : index + 2] for index in range(0, 12, 2)).lower()


def parse_global_user_table(output: str, *, role_filter: str = "profiling") -> list[UserEntry]:
    return parse_global_user_table_explained(output, role_filter=role_filter).entries


def parse_global_user_table_explained(output: str, *, role_filter: str = "profiling") -> ParseResult:
    """Extract target user MACs from `show global-user-table list role ...`.

    Aruba user-table output often contains other MAC-looking fields such as
    BSSID or AP details. This parser keeps only the first user MAC-like token
    from the identity columns near the start of each data row.
    """
    role = role_filter.strip().casefold()
    entries: dict[str, UserEntry] = {}
    decisions: list[ParseDecision] = []
    for line_number, line in enumerate(output.splitlines(), start=1):
        stripped = line.strip()
        skip_reason = _skip_reason(stripped)
        if skip_reason:
            if stripped:
                decisions.append(ParseDecision(line_number, "ignored", skip_reason))
            continue

        tokens = stripped.split()
        mac_index, mac, mac_reason = _target_mac_from_tokens(tokens, role_filter=role)
        if not mac:
            decisions.append(ParseDecision(line_number, "ignored", mac_reason or "no_user_mac"))
            continue
        role_matches, role_reason = _row_matches_role(tokens, role, mac_index)
        if role and not role_matches:
            # The command should already filter by role, but this avoids deleting
            # unrelated rows if a device echoes a broader table.
            decisions.append(
                ParseDecision(
                    line_number,
                    "ignored",
                    role_reason,
                    mac=mac,
                    role=_probable_role_token(tokens, mac_index) or _extract_role(tokens, role),
                )
            )
            continue
        if mac in entries:
            decisions.append(ParseDecision(line_number, "ignored", "duplicate_user_mac", mac=mac, role=_extract_role(tokens, role)))
            continue
        entry = UserEntry(
            mac=mac,
            role=_extract_role(tokens, role),
            username=_extract_username(tokens, mac_index),
            ip_address=_extract_ip(tokens),
        )
        entries[mac] = entry
        decisions.append(ParseDecision(line_number, "selected", mac_reason or role_reason or "selected", mac=mac, role=entry.role))
    return ParseResult(entries=list(entries.values()), decisions=decisions)


def _skip_reason(line: str) -> str:
    if not line:
        return "blank"
    lowered = line.casefold()
    if lowered.startswith(("show ", "global user", "users", "total", "----", "mac address", "ip address")):
        return "header_or_command"
    if set(line.replace(" ", "")) <= {"-"}:
        return "separator"
    if not any(pattern.search(line) for pattern in _MAC_PATTERNS):
        return "no_mac_token"
    return ""


def _target_mac_from_tokens(tokens: list[str], *, role_filter: str) -> tuple[int, str, str]:
    candidates = [(index, normalize_mac(token)) for index, token in enumerate(tokens)]
    candidates = [(index, mac) for index, mac in candidates if mac]
    if not candidates:
        return -1, "", "no_mac_token"

    role_index = _role_index(tokens, role_filter)
    if role_index >= 0:
        before_role = [(index, mac) for index, mac in candidates if index < role_index]
        identity_candidates = [(index, mac) for index, mac in before_role if index <= 3]
        if len(identity_candidates) == 1:
            index, mac = identity_candidates[0]
            return index, mac, "selected_identity_mac_before_role"
        if len(identity_candidates) > 1:
            return -1, "", "ambiguous_identity_macs_before_role"
        if len(before_role) == 1:
            index, mac = before_role[0]
            return index, mac, "selected_mac_before_role"
        if len(before_role) > 1:
            return -1, "", "ambiguous_macs_before_role"

    primary_identity_candidates = [(index, mac) for index, mac in candidates if index <= 2]
    if len(primary_identity_candidates) == 1:
        index, mac = primary_identity_candidates[0]
        return index, mac, "selected_identity_mac_without_role_column"
    if len(primary_identity_candidates) > 1:
        return -1, "", "ambiguous_identity_macs_without_role_column"
    fallback_identity_candidates = [(index, mac) for index, mac in candidates if index <= 3]
    if len(fallback_identity_candidates) == 1:
        index, mac = fallback_identity_candidates[0]
        return index, mac, "selected_identity_mac_without_role_column"
    if len(fallback_identity_candidates) > 1:
        return -1, "", "ambiguous_identity_macs_without_role_column"
    return -1, "", "mac_token_outside_identity_columns"


def _row_matches_role(tokens: list[str], role_filter: str, mac_index: int) -> tuple[bool, str]:
    if not role_filter:
        return True, "role_filter_empty"
    role_index = _role_index(tokens, role_filter)
    if role_index >= 0:
        return True, "role_column_matches"
    role_candidate = _probable_role_token(tokens, mac_index)
    if role_candidate:
        if role_candidate.casefold() == role_filter:
            return True, "probable_role_matches"
        return False, "role_mismatch"
    # Some filtered outputs omit a role column. In that shape, accept rows where
    # the user MAC appears in the usual identity columns.
    if 0 <= mac_index <= 3 and _extract_ip(tokens):
        return True, "accepted_filtered_output_without_role_column"
    return False, "role_not_found"


def _role_index(tokens: list[str], role_filter: str) -> int:
    if not role_filter:
        return -1
    for index, token in enumerate(tokens):
        if token.strip().casefold() == role_filter:
            return index
    return -1


def _extract_role(tokens: list[str], role_filter: str) -> str:
    index = _role_index(tokens, role_filter)
    if index >= 0:
        return tokens[index]
    return role_filter


def _probable_role_token(tokens: list[str], mac_index: int) -> str:
    candidate_index = mac_index + 2
    if candidate_index >= len(tokens):
        return ""
    if normalize_mac(tokens[candidate_index - 1]):
        return ""
    token = tokens[candidate_index].strip()
    if not token or token.isdigit() or normalize_mac(token) or _IPV4_PATTERN.fullmatch(token):
        return ""
    if token.casefold() in {"role", "vlan", "bssid", "ap", "essid"}:
        return ""
    return token


def _extract_username(tokens: list[str], mac_index: int) -> str:
    for token in tokens[mac_index + 1 : mac_index + 5]:
        if _IPV4_PATTERN.fullmatch(token):
            continue
        if normalize_mac(token):
            continue
        if token.casefold() in {"role", "vlan"}:
            continue
        return token
    return ""


def _extract_ip(tokens: list[str]) -> str:
    for token in tokens[:6]:
        if _IPV4_PATTERN.fullmatch(token):
            return token
    return ""
