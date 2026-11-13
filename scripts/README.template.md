# Event Ordering, Delayed Messages & Stale Workers — Distributed Systems Lab

A small Python simulation of an order-processing service where each order moves
through versioned states:

```
CREATED (v1)  →  PAID (v2)  →  SHIPPED (v3)
```

Events can arrive **late, out of order, or more than once**, and two workers can
race to update the same order. The simulation shows how a consumer uses
**version numbers** to apply updates in the right order (and ignore duplicates),
and how a storage layer uses **fencing tokens** to reject a stale worker.

No third-party packages are needed — only the Python 3.10+ standard library.

---

## How to run

```bash
git clone <this-repo-url>
cd event-ordering-lab

python3 main.py              # run all scenarios (exit code 0 = all passed)

python3 main.py ordering     # 1. versioned ordering: v2, v1, v2 again
python3 main.py missing      # 2. missing event (v2 lost, v3 arrives)
python3 main.py stale        # 3. stale worker vs. fencing tokens
python3 main.py chaos        # 4. bonus: random delays + duplicates, 5 orders
python3 main.py chaos --seed 7   # try a different random delivery order

python3 -m unittest discover -s tests -t . -v   # 11 unit tests
```

Optional — regenerate the screenshots in `docs/screenshots/`:

```bash
pip install playwright && playwright install chromium
python3 scripts/render_screenshots.py
```

## Project layout

```
main.py                     CLI that runs the four scenarios and prints PASS/FAIL checks
order_sim/
  events.py                 OrderEvent(order_id, version, status) + lifecycle
  processor.py              OrderProcessor: in-order apply, pending buffer, dedup, gap alerts
  fencing.py                LockService (leases + fencing tokens), FencedOrderStore, Worker
  log.py                    tick-stamped logger
tests/                      unittest suite (ordering + fencing)
docs/sample-output.txt      full terminal output of `python3 main.py`
docs/test-output.txt        full output of the unit tests
docs/screenshots/*.png      terminal screenshots of each scenario
```

## Design

### Versioned ordering (`order_sim/processor.py`)

Each order keeps `version` (last applied), `status`, and a `pending` buffer.
For every incoming event:

| Condition | Action |
|---|---|
| `version == current + 1` | **Apply**, then drain the buffer (release any held `current+1`, `current+2`, …) |
| `version > current + 1` | **Buffer** it; record the missing versions in between; start a gap timer |
| `version <= current` | **Duplicate** (already applied) → ignore |
| `version` already in buffer | **Duplicate** (already buffered) → ignore |
| status doesn't match the lifecycle for that version | **Reject** |

`check_stalled(tick, timeout)` reports any order whose gap has been open longer
than `timeout` ticks — the point where a real system would ask the producer (or
replay the log) to resend the missing version.

### Fencing tokens (`order_sim/fencing.py`)

`LockService` grants a time-limited lease and a **monotonically increasing
fencing token** each time a lease is granted. Every write to `FencedOrderStore`
carries the writer's token, and the store remembers the highest token it has
seen per order. A write with `token < highest_token` is rejected. The check
lives in the **store**, not the worker, because a paused worker cannot know its
lease has expired.

---

## 1. Correct event ordering (v2 → v1 → v2 again)

![ordering](docs/screenshots/ordering.png)

```
{{ordering}}
```

v2 arrives first and is **held** (the order stays at v0). When v1 arrives it is
applied, and v2 is immediately **released** from the buffer and applied. The
final applied history is `CREATED → PAID → SHIPPED`.

## 2. Duplicate handling

The same run shows the duplicate case: the second copy of v2 at `t=03` is
logged as `DUP ... already applied (current v2) -> IGNORED`, and the stats show
`APPLIED: 3, DUPLICATE: 1` — each version was applied exactly once. Duplicates
that arrive while the original is still buffered are also ignored
(`test_duplicate_while_buffered_is_ignored`), and the chaos run below injects
6 random duplicates across 5 orders with every one ignored.

## 3. Missing event (later version arrives, earlier one is lost)

![missing](docs/screenshots/missing.png)

```
{{missing}}
```

When v3 arrives while v2 is missing, the order **does not jump** to SHIPPED: it
stays at `v1 CREATED`, v3 sits in the pending buffer, and `v2` is recorded as
missing. After the gap has been open for 3 ticks an `ALERT` asks for a resend.
Once the lost v2 is re-sent, v2 and then the buffered v3 are applied in order.

## 4. Stale worker rejection (fencing tokens)

![stale](docs/screenshots/stale.png)

```
{{stale}}
```

worker-A gets token **1**, then stalls past its lease. worker-B gets token **2**
and updates the order. When worker-A wakes up and tries to write with token 1,
the store rejects it because it has already seen token 2. Scenario 3b runs the
same timeline with fencing turned off: worker-A's stale write overwrites
worker-B's tracking number — the lost update that fencing prevents.

## 5. Bonus: chaos network

![chaos](docs/screenshots/chaos.png)

```
{{chaos}}
```

## Unit tests

```
{{tests}}
```

---

## Reflection: why timestamps alone aren't enough

Timestamps come from each machine's physical clock, and those clocks are never
perfectly synchronized: NTP corrections, clock drift, and leap-second handling
can make one node's clock run ahead of or behind another's, or even jump
backwards, so an event that happened later can carry an earlier timestamp.
Timestamps also say nothing about *causality* or *completeness* — if I receive
an update stamped 10:00:05, I can't tell whether an update stamped 10:00:03 is
still in flight, whereas a version number makes the gap explicit (seeing v3
after v1 proves v2 is missing). Two events can even get identical timestamps,
leaving no deterministic tie-break, and "last-write-wins by timestamp" will
silently discard a real update when clocks are skewed. That's why this lab uses
per-order version numbers for ordering and monotonically increasing fencing
tokens for writer authority: both are logical counters issued by a single
authority, so their order is guaranteed regardless of whose clock is right.
