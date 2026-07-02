from pathlib import Path

from aruba_mm_cleanup.parser import normalize_mac, parse_global_user_table, parse_global_user_table_explained


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_normalize_mac_accepts_common_formats():
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aabb.ccdd.eeff") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("not-a-mac") == ""


def test_parse_global_user_table_extracts_user_mac_only():
    output = """
Global User Table
-----------------
IP              MAC Address          User          Role        VLAN  BSSID
10.1.1.10       aa:bb:cc:00:00:01    user-a        profiling   20    11:22:33:44:55:66
10.1.1.11       aa-bb-cc-00-00-02    user-b        employee    30    22:33:44:55:66:77
10.1.1.12       aabb.cc00.0003       user-c        profiling   20    33:44:55:66:77:88
"""

    entries = parse_global_user_table(output, role_filter="profiling")

    assert [entry.mac for entry in entries] == [
        "aa:bb:cc:00:00:01",
        "aa:bb:cc:00:00:03",
    ]
    assert entries[0].ip_address == "10.1.1.10"
    assert entries[0].username == "user-a"


def test_parse_global_user_table_deduplicates_user_macs():
    output = """
10.1.1.10 aa:bb:cc:00:00:01 user-a profiling 11:22:33:44:55:66
10.1.1.10 aa:bb:cc:00:00:01 user-a profiling 22:33:44:55:66:77
"""

    entries = parse_global_user_table(output, role_filter="profiling")

    assert [entry.mac for entry in entries] == ["aa:bb:cc:00:00:01"]


def test_parse_global_user_table_ignores_trailing_bssid_when_role_is_absent():
    output = """
User             IP              MAC                BSSID
user-a           10.1.1.10       aa:bb:cc:00:00:01  11:22:33:44:55:66
"""

    entries = parse_global_user_table(output, role_filter="profiling")

    assert [entry.mac for entry in entries] == ["aa:bb:cc:00:00:01"]


def test_parse_sanitized_aruba_standard_fixture_ignores_bssid_and_other_roles():
    output = (FIXTURE_DIR / "aruba_global_user_table_standard.txt").read_text(encoding="utf-8")

    entries = parse_global_user_table(output, role_filter="profiling")

    assert [entry.mac for entry in entries] == [
        "aa:bb:cc:00:10:01",
        "aa:bb:cc:00:10:02",
    ]
    assert "11:22:33:44:55:66" not in [entry.mac for entry in entries]
    assert "aa:bb:cc:00:10:03" not in [entry.mac for entry in entries]


def test_parse_sanitized_aruba_compact_fixture_accepts_role_filtered_rows_with_ap_name():
    output = (FIXTURE_DIR / "aruba_global_user_table_filtered_compact.txt").read_text(encoding="utf-8")

    entries = parse_global_user_table(output, role_filter="profiling")

    assert [entry.mac for entry in entries] == [
        "aa:bb:cc:00:20:01",
        "aa:bb:cc:00:20:02",
    ]
    assert "44:55:66:77:88:99" not in [entry.mac for entry in entries]


def test_parse_sanitized_aruba_dotted_fixture_ignores_ap_radio_mac():
    output = (FIXTURE_DIR / "aruba_global_user_table_dotted.txt").read_text(encoding="utf-8")

    entries = parse_global_user_table(output, role_filter="profiling")

    assert [entry.mac for entry in entries] == [
        "aa:bb:cc:00:30:01",
        "aa:bb:cc:00:30:02",
    ]
    assert "66:77:88:99:aa:bb" not in [entry.mac for entry in entries]


def test_parse_explained_records_selected_and_ignored_reasons():
    output = (FIXTURE_DIR / "aruba_global_user_table_mixed_reasoned.txt").read_text(encoding="utf-8")

    result = parse_global_user_table_explained(output, role_filter="profiling")

    assert [entry.mac for entry in result.entries] == [
        "aa:bb:cc:00:40:01",
        "aa:bb:cc:00:40:03",
    ]
    decisions = {(item.action, item.reason, item.mac) for item in result.decisions}
    assert ("selected", "selected_identity_mac_before_role", "aa:bb:cc:00:40:01") in decisions
    assert ("ignored", "role_mismatch", "aa:bb:cc:00:40:02") in decisions
    assert ("ignored", "duplicate_user_mac", "aa:bb:cc:00:40:03") in decisions
    assert any(item.action == "ignored" and item.reason == "role_not_found" for item in result.decisions)
