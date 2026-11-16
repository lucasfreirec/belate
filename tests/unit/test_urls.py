import pytest

from app.alerts.urls import UnsafeUrl, validate_webhook_url
from app.api.public import html_to_text

DNS = {
    "hooks.example.com": ["93.184.216.34"],
    "v6.example.com": ["2606:2800:220:1:248:1893:25c8:1946"],
    "internal.example.com": ["10.0.0.5"],
    "mixed.example.com": ["93.184.216.34", "127.0.0.1"],
    "metadata.example.com": ["169.254.169.254"],
    "mapped.example.com": ["::ffff:127.0.0.1"],
    "v6-local.example.com": ["::1"],
    "v6-private.example.com": ["fd00::1"],
    "broken.example.com": ["not an address"],
}


def resolve(host: str) -> list[str]:
    # A literal IP address resolves to itself, as with real DNS.
    return DNS.get(host, [host] if host[0].isdigit() or ":" in host else [])


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.example.com/notify",
        "https://hooks.example.com:443/notify?id=3",
        "https://v6.example.com/",
        "https://93.184.216.34/notify",
    ],
)
def test_accepts_https_urls_on_public_hosts(url: str) -> None:
    validate_webhook_url(url, resolve)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://hooks.example.com/notify", "https"),
        ("ftp://hooks.example.com/", "https"),
        ("hooks.example.com/notify", "https"),
        ("https:///notify", "host name"),
        ("https://user:secret@hooks.example.com/", "user name"),
        ("https://hooks.example.com:8443/", "port"),
        ("https://hooks.example.com:99999/", "not valid"),
        ("https://unknown.example.com/", "does not resolve"),
        ("https://internal.example.com/", "private"),
        ("https://mixed.example.com/", "private"),
        ("https://metadata.example.com/latest/meta-data/", "private"),
        ("https://mapped.example.com/", "private"),
        ("https://v6-local.example.com/", "private"),
        ("https://v6-private.example.com/", "private"),
        ("https://broken.example.com/", "private"),
        ("https://127.0.0.1/", "private"),
        ("https://10.1.2.3/", "private"),
        ("https://192.168.1.10/", "private"),
        ("https://172.16.0.1/", "private"),
        ("https://169.254.169.254/", "private"),
        ("https://0.0.0.0/", "private"),
        ("https://[::1]/", "private"),
        ("https://224.0.0.1/", "private"),
    ],
)
def test_refuses_unsafe_urls(url: str, reason: str) -> None:
    with pytest.raises(UnsafeUrl, match=reason):
        validate_webhook_url(url, resolve)


def test_one_private_address_among_several_is_enough_to_refuse() -> None:
    with pytest.raises(UnsafeUrl):
        validate_webhook_url("https://mixed.example.com/", resolve)


def test_html_to_text_strips_markup_and_keeps_line_breaks() -> None:
    html = "<p>P&#233;riode : en semaine.<br><br>Le trafic est <b>interrompu</b>.</p>"

    assert html_to_text(html) == "Période : en semaine.\nLe trafic est interrompu."


def test_html_to_text_neutralises_scripts() -> None:
    text = html_to_text('<img src=x onerror="alert(1)">Bus de remplacement<script>x()</script>')

    assert "<" not in text
    assert "Bus de remplacement" in text
