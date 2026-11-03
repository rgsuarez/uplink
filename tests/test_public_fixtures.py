"""Public fixture expansion preserves parser invariants without private coordinates."""
import ipaddress
import pytest
from offline_agent.public_fixtures import expand_fixture_text


def test_plain_and_empty_text_remain_exact():
    assert expand_fixture_text("") == ""
    assert expand_fixture_text("no address here") == "no address here"
    assert expand_fixture_text(b"") == b""


def test_unknown_public_symbol_is_refused():
    with pytest.raises(ValueError, match="unknown_public_fixture_address"):
        expand_fixture_text("@PUBLIC_IPV4_UNKNOWN@")


def test_protocol_loopback_and_synthetic_management_keep_their_classes():
    assert ipaddress.ip_address(expand_fixture_text("@PUBLIC_IPV4_056@" )).is_loopback
    assert ipaddress.ip_address(expand_fixture_text("@PUBLIC_IPV4_062@" )).is_link_local


def test_binary_fixture_expansion_preserves_bytes():
    value = b"@PUBLIC_IPV4_056@"
    assert isinstance(expand_fixture_text(value), bytes)
    assert expand_fixture_text(value) == expand_fixture_text(value.decode()).encode()
