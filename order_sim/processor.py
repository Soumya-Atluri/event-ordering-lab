"""Consumer that applies versioned order events strictly in order.

Rules
-----
* An event is applied only when ``event.version == current_version + 1``.
* An event from the future (``version > current_version + 1``) is held in a
  per-order *pending buffer*; the missing versions in between are recorded.
* An event whose version is already applied, or already buffered, is a
  duplicate and is ignored (idempotent delivery).
* After every apply, the buffer is drained so held events are released as
  soon as the gap closes.
"""
from dataclasses import dataclass, field

from .events import LIFECYCLE, OrderEvent
from .log import SimLog

APPLIED = "APPLIED"
BUFFERED = "BUFFERED"
DUPLICATE = "DUPLICATE"
REJECTED = "REJECTED"


@dataclass
class OrderState:
    order_id: str
    version: int = 0           # 0 = nothing applied yet
    status: str | None = None
    pending: dict[int, OrderEvent] = field(default_factory=dict)
    waiting_since: int | None = None   # tick at which a gap was first seen
    history: list[str] = field(default_factory=list)

    def missing_versions(self) -> list[int]:
        if not self.pending:
            return []
        highest = max(self.pending)
        return [v for v in range(self.version + 1, highest) if v not in self.pending]


class OrderProcessor:
    def __init__(self, log: SimLog | None = None):
        self.log = log or SimLog()
        self.orders: dict[str, OrderState] = {}
        self.stats = {APPLIED: 0, BUFFERED: 0, DUPLICATE: 0, REJECTED: 0}

    # ------------------------------------------------------------------ API
    def state(self, order_id: str) -> OrderState:
        return self.orders.setdefault(order_id, OrderState(order_id))

    def receive(self, ev: OrderEvent, tick: int = 0) -> str:
        st = self.state(ev.order_id)
        self.log(tick, "RECV", f"{ev}")

        if LIFECYCLE.get(ev.version) != ev.status:
            return self._count(REJECTED, tick,
                               f"{ev} is not a valid lifecycle step -> REJECTED")

        if ev.version <= st.version:
            return self._count(DUPLICATE, tick,
                               f"{ev} already applied (current v{st.version}) -> IGNORED")

        if ev.version in st.pending:
            return self._count(DUPLICATE, tick,
                               f"{ev} already buffered -> IGNORED")

        if ev.version == st.version + 1:
            self._apply(st, ev, tick)
            self._drain(st, tick)
            return APPLIED

        # Future event: hold it and record the gap.
        st.pending[ev.version] = ev
        if st.waiting_since is None:
            st.waiting_since = tick
        self.stats[BUFFERED] += 1
        missing = ", ".join(f"v{v}" for v in st.missing_versions())
        self.log(tick, "BUFFER", f"{ev} held; order stays at "
                 f"v{st.version} {st.status or '(none)'}; missing: {missing}")
        return BUFFERED

    def check_stalled(self, tick: int, timeout: int) -> list[tuple[str, list[int]]]:
        """Report orders whose gap has been open longer than ``timeout`` ticks.

        In a real system this is where you would request a resend / replay
        from the producer or the log.
        """
        stalled = []
        for st in self.orders.values():
            if st.waiting_since is not None and tick - st.waiting_since >= timeout:
                missing = st.missing_versions()
                stalled.append((st.order_id, missing))
                self.log(tick, "ALERT", f"{st.order_id} waiting "
                         f"{tick - st.waiting_since} ticks for "
                         f"{', '.join(f'v{v}' for v in missing)} -> request resend")
        return stalled

    # ------------------------------------------------------------ internals
    def _apply(self, st: OrderState, ev: OrderEvent, tick: int) -> None:
        before = f"v{st.version} {st.status or '(none)'}"
        st.version, st.status = ev.version, ev.status
        st.history.append(ev.status)
        self.stats[APPLIED] += 1
        self.log(tick, "APPLY", f"{ev.order_id}: {before} -> v{st.version} {st.status}")

    def _drain(self, st: OrderState, tick: int) -> None:
        while st.version + 1 in st.pending:
            nxt = st.pending.pop(st.version + 1)
            self.log(tick, "RELEASE", f"{nxt} released from buffer")
            self._apply(st, nxt, tick)
        if not st.pending:
            st.waiting_since = None

    def _count(self, outcome: str, tick: int, msg: str) -> str:
        self.stats[outcome] += 1
        self.log(tick, outcome if outcome != DUPLICATE else "DUP", msg)
        return outcome
