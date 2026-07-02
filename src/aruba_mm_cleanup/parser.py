"""Parse Aruba MM global user table output."""

from __future__ import annotations

import re

from .models import UserEntry


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
    """Extract target user MACs from `show global-user-table list role ...`.

    Aruba user-table output often contains other MAC-looking fields such as
    BSSID or AP details. This parser keeps only the first user MAC-like token
    from the identity columns near the start of each data row.
    """
    role = role_filter.strip().casefold()
    entries: dict[str, UserEntry] = {}
    for line in output.splitlines():
        stripped = line.strip()
        if not _looks_like_data_row(stripped):
            continue

        tokens = stripped.split()
        mac_index, mac = _target_mac_from_tokens(tokens, role_filter=role)
        if not mac:
            continue
        if role and not _row_matches_role(tokens, role, mac_index):
            # The command should already filter by role, but this avoids deleting
            # unrelated rows if a device echoes a broader table.
            continue
        if mac in entries:
            continue
        entries[mac] = UserEntry(
            mac=mac,
            role=_extract_role(tokens, role),
            username=_extract_username(tokens, mac_index),
            ip_address=_extract_ip(tokens),
        )
    return list(entries.values())


def _looks_like_data_row(line: str) -> bool:
    if not line:
        return False
    lowered = line.casefold()
    if lowered.startswith(("show ", "global user", "users", "total", "----", "mac address", "ip address")):
        return False
    if set(line.replace(" ", "")) <= {"-"}:
        return False
    return any(pattern.search(line) for pattern in _MAC_PATTERNS)


def _target_mac_from_tokens(tokens: list[str], *, role_filter: str) -> tuple[int, str]:
    candidates = [(index, normalize_mac(token)) for index, token in enumerate(tokens)]
    candidates = [(index, mac) for index, mac in candidates if mac]
    if not candidates:
        return -1, ""

    role_index = _role_index(tokens, role_filter)
    for index, mac in candidates:
        if role_index >= 0 and index < role_index:
            return index, mac
    for index, mac in candidates:
        if index <= 3:
            return index, mac
    return -1, ""


def _row_matches_role(tokens: list[str], role_filter: str, mac_index: int) -> bool:
    if not role_filter:
        return True
    role_index = _role_index(tokens, role_filter)
    if role_index >= 0:
        return True
    role_candidate = _probable_role_token(tokens, mac_index)
    if role_candidate:
        return role_candidate.casefold() == role_filter
    # Some filtered outputs omit a role column. In that shape, accept rows where
    # the user MAC appears in the usual identity columns.
    return 0 <= mac_index <= 3


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
