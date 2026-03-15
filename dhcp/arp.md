# ARP-like IP Conflict Detection

## Why

A real DHCP server never hands out an IP blindly. Before making an offer it
sends an **ARP probe** ("who has this IP?") on the local network. If anything
replies the address is already taken and the server skips it.

Our custom DHCP tracks leases in memory and persists them to `leases.json`,
but that state can drift (e.g. after a crash). This module adds the same
safety net.

---

## Two modes

### Loopback simulation (`loopback: true` in `dhcp_config.yaml`)

Real ARP/ICMP cannot distinguish "a process is bound to 127.0.0.x" from
"that loopback address simply exists", because the Windows loopback adapter
answers for the entire `127.0.0.0/8` range.

**Solution — custom UDP probe on port 6701:**

```
DHCP Server                          Active Client (e.g. OriginServer)
     │                                         │
     │  UDP  "ARP_PROBE" → 127.0.0.13:6701     │
     │────────────────────────────────────────>│  ProbeListener bound to
     │                                         │  127.0.0.13:6701
     │  UDP  "ARP_REPLY" ←───────────────────  │
     │                                         │
  IP is in use — skip it
```

If **no reply arrives within 300 ms** the address is considered free and the
server offers it.

Each client starts a `ProbeListener` thread (in `dhcp_helper.py`) immediately
after a successful DORA exchange. The listener binds to
`{assigned_ip}:6701` — not `0.0.0.0` — so only the process that owns that IP
ever receives the probe.

### Real network (`loopback: false`)

A standard **ICMP echo (ping)** is sent via scapy with a 500 ms timeout.
If any host replies the IP is in use.

---

## Files changed

| File | Change |
|------|--------|
| `dhcp/arp_probe.py` | **New.** `probe_ip_loopback()`, `probe_ip_real()`, `ProbeListener` class |
| `dhcp/dhcp_server.py` | Added `_advance_pool_ip()`, `_probe_ip()`. Refactored `get_next_available_ip()` to probe outside the lock and retry up to 10 times on conflict |
| `dhcp/dhcp_helper.py` | `VirtualNetworkInterface` creates a `ProbeListener` on init, starts it after DORA, stops it on release |

---

## Probe port

`ARP_PROBE_PORT = 6701` (defined in `arp_probe.py`).

Chosen to be adjacent to the DHCP port (6700) and not clash with any
well-known service.

---

## Conflict flow (loopback)

```
1. Server receives DISCOVER from new client
2. get_next_available_ip() finds a candidate from the pool (lock held)
3. Lock released — probe_ip_loopback(candidate) sends UDP to candidate:6701
4a. Reply received  → log warning, loop back to step 2 with next IP
4b. No reply (timeout) → offer the IP to the client
5. Client completes DORA, starts ProbeListener bound to its new IP
6. Future probes for that IP will now receive a reply
```

---

## Limitations

- The ProbeListener starts **after** the DORA handshake completes. There is a
  brief window between the OFFER and the client binding where a second
  simultaneous DISCOVER for the same IP would not be blocked. The existing
  lease-table check is the guard for this race.
- In real-network mode, a host that blocks ICMP will appear free even if it
  is using the IP. This matches the behaviour of most real DHCP servers.
