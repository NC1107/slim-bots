import pytest

from slimbots.client import socket_url


def test_https_becomes_wss():
    assert socket_url("https://my.space") == "wss://my.space/ws"


@pytest.mark.parametrize(
    "authority", ["localhost:8080", "127.0.0.1:8080", "[::1]:8080"]
)
def test_http_loopback_allowed(authority, capsys):
    url = socket_url(f"http://{authority}")
    assert url == f"ws://{authority}/ws"
    assert "loopback only" in capsys.readouterr().err


def test_http_off_loopback_refused():
    with pytest.raises(RuntimeError, match="https"):
        socket_url("http://example.com")


def test_unknown_scheme_refused():
    with pytest.raises(RuntimeError):
        socket_url("ftp://example.com")
