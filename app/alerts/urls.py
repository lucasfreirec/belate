"""Check that a webhook URL is safe to call from the server.

Anyone holding the API token chooses where notifications are sent. Without this check, they
could point the server at its own network: the database, the Redis instance, a cloud
metadata endpoint. The rule is: https only, default port, and every address the host name
resolves to must be a public one.

The check runs when a subscription is created and again before each notification, because
a host name can be made to resolve to a different address later. A small window remains
between the check and the connection; redirects are refused for the same reason.
"""

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

Resolver = Callable[[str], list[str]]


class UnsafeUrl(ValueError):
    """The URL must not be called. The message says why and is safe to show to the user."""


def resolve_host(host: str) -> list[str]:
    """Return every IP address the host name resolves to."""
    try:
        results = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return []
    return sorted({str(result[4][0]) for result in results})


def validate_webhook_url(url: str, resolve: Resolver = resolve_host) -> None:
    """Raise `UnsafeUrl` unless `url` is an https URL on a public host."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeUrl("the URL is not valid") from exc

    if parts.scheme != "https":
        raise UnsafeUrl("the URL must start with https://")
    if not parts.hostname:
        raise UnsafeUrl("the URL has no host name")
    if parts.username or parts.password:
        raise UnsafeUrl("the URL must not contain a user name or a password")
    if port not in (None, 443):
        raise UnsafeUrl("the URL must use the default https port")

    addresses = resolve(parts.hostname)
    if not addresses:
        raise UnsafeUrl("the host name does not resolve")
    for address in addresses:
        if not _is_public(address):
            raise UnsafeUrl("the host resolves to a private or reserved address")


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    # ::ffff:127.0.0.1 is the IPv4 loopback written as IPv6.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast
