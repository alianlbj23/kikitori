"""遠端運算的網路暴露面：哪些來源位址可以連進運算主機（主埠與配對埠共用）。

位於 remote 模組的最底層，不依賴 engine：`remote.server` 需要 engine，`remote.client`／
`remote.pair_net` 又會被 engine 載入，共用的東西放這裡才不會循環匯入
（docs/specs/remote-compute.md「網路暴露面」）。
"""

import ipaddress

_TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
_TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


def is_allowed_source(address: str) -> bool:
    """來源 IP 是否在區網、link-local、本機或 Tailscale 網段（其餘一律拒絕）。"""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip in _TAILSCALE_V4 or ip in _TAILSCALE_V6:
        return True
    return ip.is_private or ip.is_link_local or ip.is_loopback


def is_allowed_listen_host(address: str) -> bool:
    """運算主機可以監聽的位址：區網、本機或 Tailscale 的單一位址。

    不接受 `0.0.0.0`／`::`（所有介面，威脅模型：預設不綁所有介面）與公用位址；
    `ipaddress` 把 0.0.0.0/8 算成 private，所以要另外擋。
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not ip.is_unspecified and is_allowed_source(address)
