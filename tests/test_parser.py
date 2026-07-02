from aruba_mm_cleanup.parser import normalize_mac, parse_global_user_table


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

