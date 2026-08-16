"""Who is calling, and whether we believe them.

The rate limiter is only as good as the identity it counts against. That
identity used to be `X-Forwarded-For`'s left-most entry, taken on faith —
which means a caller could send a different value on every request and mint
an unlimited number of fresh quotas. The limit looked enforced and was not.

The rule here is the standard one, and it is the only one that holds: **the
socket peer is the only unforgeable identity**, so a forwarded header is
believed exactly when the peer is a proxy the operator has vouched for.

    no trusted proxies      -> peer address, always
    peer is trusted         -> left-most X-Forwarded-For entry
    peer is not trusted     -> peer address, and the header is logged

That last line matters operationally: a forwarded header arriving from an
untrusted peer is either a misconfiguration or someone probing, and both are
worth seeing in the log.

`ipaddress` is stdlib, so none of this adds a dependency.
"""

from __future__ import annotations

import ipaddress
import logging

logger = logging.getLogger(__name__)

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_networks(raw: str) -> list[Network]:
    """Parse a comma-separated list of IPs or CIDRs. Bad entries are dropped.

    A malformed entry must not take the process down at import time — a typo
    in an environment variable would otherwise turn a rate-limit tweak into
    an outage — but it must be loud, because silently trusting nothing looks
    identical to a working configuration until the day it matters.
    """
    networks: list[Network] = []
    for entry in raw.split(","):
        text = entry.strip()
        if not text:
            continue
        try:
            networks.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            logger.warning("ignoring unparseable network %r in configuration", text)
    return networks


def _address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def in_networks(candidate: str, networks: list[Network]) -> bool:
    """True when `candidate` is an address inside any of `networks`."""
    if not networks:
        return False
    address = _address(candidate)
    if address is None:
        return False
    return any(address in network for network in networks)


def resolve_client(
    peer: str | None,
    forwarded_for: str | None,
    trusted_proxies: list[Network],
) -> tuple[str, bool]:
    """Return `(identity, forwarded_header_was_trusted)`.

    `peer` is the socket address — unforgeable. `forwarded_for` is the raw
    header, which is a claim.
    """
    peer_address = (peer or "unknown").strip() or "unknown"
    if not forwarded_for:
        return peer_address, False

    if not in_networks(peer_address, trusted_proxies):
        # Someone sent a forwarded header from an address we have not vouched
        # for. Nothing is trusted; the peer pays for the request.
        logger.warning(
            "untrusted_forwarded_header peer=%s header_present=true",
            peer_address,
        )
        return peer_address, False

    # The left-most entry is the original client, per convention. Everything
    # after it is the proxy chain.
    claimed = forwarded_for.split(",")[0].strip()
    if _address(claimed) is None:
        logger.warning(
            "malformed_forwarded_header peer=%s value_len=%d", peer_address, len(claimed)
        )
        return peer_address, False
    return claimed, True
