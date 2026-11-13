#!/usr/bin/env python3
"""Distributed order-processing simulation.

Usage:
    python3 main.py                 # run every scenario
    python3 main.py ordering        # 1. v2, v1, v2-again
    python3 main.py missing         # 2. later version while earlier is missing
    python3 main.py stale           # 3. stale worker vs fencing tokens
    python3 main.py chaos --seed 7  # 4. bonus: random delays + duplicates
"""
import argparse
import random
import sys
import textwrap

from order_sim import (BUFFERED, DUPLICATE, LIFECYCLE, FencedOrderStore,
                       LockService, OrderProcessor, SimLog, StoredOrder, Worker,
                       event)

LOG = SimLog()


def banner(title: str) -> None:
    LOG.raw("")
    LOG.raw("=" * 78)
    LOG.raw(f"  {title}")
    LOG.raw("=" * 78)


def check(label: str, ok: bool) -> bool:
    LOG.raw(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


# --------------------------------------------------------------------------
# 1. Versioned event ordering: deliver v2, v1, v2 again
# --------------------------------------------------------------------------
def scenario_ordering() -> bool:
    banner("SCENARIO 1: Versioned ordering  (delivery: v2, v1, v2 again, v3)")
    p = OrderProcessor(LOG)
    deliveries = [event("A-100", 2), event("A-100", 1),
                  event("A-100", 2), event("A-100", 3)]
    outcomes = [p.receive(ev, tick) for tick, ev in enumerate(deliveries, start=1)]
    st = p.state("A-100")

    LOG.raw("")
    LOG.raw(f"  Final state : v{st.version} {st.status}")
    LOG.raw(f"  Applied in  : {' -> '.join(st.history)}")
    LOG.raw(f"  Stats       : {p.stats}")
    return all([
        check("v2 arriving first was buffered, not applied", outcomes[0] == BUFFERED),
        check("applied order is CREATED -> PAID -> SHIPPED",
              st.history == ["CREATED", "PAID", "SHIPPED"]),
        check("second copy of v2 was ignored as a duplicate", outcomes[2] == DUPLICATE),
        check("each version applied exactly once", p.stats["APPLIED"] == 3),
    ])


# --------------------------------------------------------------------------
# 2. Missing event: v3 arrives while v2 never shows up
# --------------------------------------------------------------------------
def scenario_missing() -> bool:
    banner("SCENARIO 2: Missing event  (v1 delivered, v2 lost, v3 delivered)")
    p = OrderProcessor(LOG)
    p.receive(event("B-200", 1), tick=1)
    p.receive(event("B-200", 3), tick=2)    # v2 is missing
    st = p.state("B-200")

    LOG.raw("")
    LOG.raw(f"  State while v2 is missing : v{st.version} {st.status}")
    LOG.raw(f"  Pending buffer            : {sorted(st.pending)}")
    LOG.raw(f"  Missing versions recorded : {st.missing_versions()}")
    LOG.raw("")
    stalled = []
    for tick in range(3, 7):                # time passes, nothing arrives
        stalled = p.check_stalled(tick, timeout=3) or stalled

    ok = all([
        check("order did NOT jump to SHIPPED", st.status == "CREATED" and st.version == 1),
        check("v3 is held as pending", 3 in st.pending),
        check("v2 is detected as missing", st.missing_versions() == [2]),
        check("stalled gap raised an alert", stalled == [("B-200", [2])]),
    ])

    LOG.raw("")
    LOG.raw("  -- producer re-sends the lost v2 --")
    p.receive(event("B-200", 2), tick=7)
    LOG.raw("")
    LOG.raw(f"  Final state : v{st.version} {st.status}")
    LOG.raw(f"  Applied in  : {' -> '.join(st.history)}")
    return ok and all([
        check("after v2 arrives, v3 is released and applied in order",
              st.history == ["CREATED", "PAID", "SHIPPED"] and not st.pending),
    ])


# --------------------------------------------------------------------------
# 3. Stale worker: fencing tokens
# --------------------------------------------------------------------------
def _run_two_workers(enforce: bool) -> tuple[StoredOrder, str]:
    lock = LockService(lease_ticks=5, log=LOG)
    store = FencedOrderStore(LOG, enforce_fencing=enforce)
    store.orders["C-300"] = StoredOrder(status="PAID")
    a, b = Worker("worker-A"), Worker("worker-B")

    a.token = lock.acquire(a.name, tick=1)
    LOG(2, "PAUSE", "worker-A stalls (long GC pause / network partition) "
        "before writing...")
    lock.acquire(b.name, tick=3)            # denied: A's lease still valid
    b.token = lock.acquire(b.name, tick=7)  # A's lease expired at t=6
    b.update(store, "C-300", "SHIPPED", "TRK-B-9001", tick=8)
    LOG(10, "RESUME", "worker-A wakes up, still believes it holds the lock")
    a.update(store, "C-300", "SHIPPED", "TRK-A-1234", tick=10)
    after_stale_write = store.orders["C-300"].tracking
    b.update(store, "C-300", "SHIPPED", "TRK-B-9001-rev2", tick=11)
    return store.orders["C-300"], after_stale_write


def scenario_stale() -> bool:
    banner("SCENARIO 3: Stale worker  (lease + fencing tokens, store enforces)")
    order, after_a = _run_two_workers(enforce=True)
    LOG.raw("")
    LOG.raw(f"  Final record : status={order.status} tracking={order.tracking} "
            f"last_writer={order.last_writer} highest_token={order.highest_token}")
    LOG.raw(f"  Accepted writes (worker:token) : {order.writes}")
    ok = all([
        check("worker-B (newer token=2) updated the order",
              order.writes[0] == "worker-B:2"),
        check("worker-A (older token=1) was rejected",
              not any(w.startswith("worker-A") for w in order.writes)),
        check("worker-B's data survived worker-A's stale attempt",
              after_a == "TRK-B-9001"),
        check("holder of the newest token can keep writing",
              order.tracking == "TRK-B-9001-rev2"),
    ])

    banner("SCENARIO 3b: Same timeline WITHOUT fencing (for contrast)")
    bad, bad_after_a = _run_two_workers(enforce=False)
    LOG.raw("")
    LOG.raw(f"  Tracking right after worker-A's stale write : {bad_after_a}")
    LOG.raw(f"  Accepted writes (worker:token)              : {bad.writes}")
    LOG.raw("  -> without fencing, the stale worker-A overwrote worker-B's newer "
            "data (lost update).")
    return ok


# --------------------------------------------------------------------------
# 4. Bonus: random delays, reordering, and duplicates across many orders
# --------------------------------------------------------------------------
def scenario_chaos(seed: int) -> bool:
    banner(f"SCENARIO 4 (bonus): Chaos network, seed={seed}  "
           "(random order + duplicates, 5 orders)")
    rng = random.Random(seed)
    msgs = [event(f"D-{n}", v) for n in range(400, 405) for v in LIFECYCLE]
    msgs += [rng.choice(msgs) for _ in range(6)]           # at-least-once dupes
    # each message gets a random network delay; deliver by arrival time
    arrivals = sorted((rng.randint(1, 20), i, m) for i, m in enumerate(msgs))
    order = ", ".join(f"{m.order_id}:v{m.version}" for _, _, m in arrivals)
    for line in textwrap.wrap(order, 74, initial_indent="  delivery order: ",
                              subsequent_indent=" " * 18):
        LOG.raw(line)
    LOG.raw("")
    p = OrderProcessor(SimLog(sink=lambda _l: None))     # quiet inner log
    for tick, _, m in arrivals:
        p.receive(m, tick)
    for oid, st in sorted(p.orders.items()):
        LOG.raw(f"  {oid}: v{st.version} {st.status:<8} history={st.history}")
    LOG.raw(f"  Stats: {p.stats}")
    return check("every order converged to CREATED -> PAID -> SHIPPED",
                 all(s.history == ["CREATED", "PAID", "SHIPPED"]
                     for s in p.orders.values()))


SCENARIOS = {
    "ordering": scenario_ordering,
    "missing": scenario_missing,
    "stale": scenario_stale,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario", nargs="?", default="all",
                    choices=["all", *SCENARIOS, "chaos"])
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    results = []
    for name, fn in SCENARIOS.items():
        if args.scenario in ("all", name):
            results.append(fn())
    if args.scenario in ("all", "chaos"):
        results.append(scenario_chaos(args.seed))

    LOG.raw("")
    LOG.raw(f"SUMMARY: {sum(results)}/{len(results)} scenarios passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
