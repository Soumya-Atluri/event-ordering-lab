"""Lease-based lock service with fencing tokens, plus a store that enforces them.

A lease alone is not safe: a worker can pause (GC, VM migration, network
partition) past its lease expiry, wake up, and still *believe* it holds the
lock. The fencing token fixes this because the **storage layer** - not the
worker - decides whether a write is allowed: every write carries the token,
and the store rejects any token lower than the highest one it has seen.
"""
from dataclasses import dataclass, field

from .log import SimLog


class LockService:
    """Hands out leases with a monotonically increasing fencing token."""

    def __init__(self, lease_ticks: int, log: SimLog):
        self.lease_ticks = lease_ticks
        self.log = log
        self.next_token = 1
        self.holder: str | None = None
        self.expires_at = -1

    def acquire(self, worker: str, tick: int) -> int | None:
        if self.holder is not None and tick < self.expires_at:
            self.log(tick, "LOCK", f"{worker} denied: lease held by {self.holder} "
                     f"until t={self.expires_at}")
            return None
        if self.holder is not None:
            self.log(tick, "LOCK", f"lease of {self.holder} expired at t={self.expires_at}")
        token = self.next_token
        self.next_token += 1
        self.holder, self.expires_at = worker, tick + self.lease_ticks
        self.log(tick, "LOCK", f"{worker} acquired lease, fencing token={token}, "
                 f"expires t={self.expires_at}")
        return token


@dataclass
class StoredOrder:
    status: str
    tracking: str | None = None
    last_writer: str | None = None
    highest_token: int = 0
    writes: list[str] = field(default_factory=list)


class FencedOrderStore:
    """Storage that refuses writes carrying a stale fencing token."""

    def __init__(self, log: SimLog, enforce_fencing: bool = True):
        self.log = log
        self.enforce = enforce_fencing
        self.orders: dict[str, StoredOrder] = {}

    def write(self, order_id: str, status: str, tracking: str,
              worker: str, token: int, tick: int) -> bool:
        order = self.orders[order_id]
        if self.enforce and token < order.highest_token:
            self.log(tick, "REJECT", f"{worker} write with token={token} rejected: "
                     f"store has already seen token={order.highest_token} (stale worker)")
            return False
        order.highest_token = max(order.highest_token, token)
        order.status, order.tracking, order.last_writer = status, tracking, worker
        order.writes.append(f"{worker}:{token}")
        self.log(tick, "WRITE", f"{worker} (token={token}) set {order_id} -> "
                 f"{status}, tracking={tracking}")
        return True


@dataclass
class Worker:
    name: str
    token: int | None = None

    def update(self, store: FencedOrderStore, order_id: str, status: str,
               tracking: str, tick: int) -> bool:
        assert self.token is not None, "worker never acquired a lease"
        return store.write(order_id, status, tracking, self.name, self.token, tick)
