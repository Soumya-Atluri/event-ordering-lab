import unittest

from order_sim import (APPLIED, BUFFERED, DUPLICATE, REJECTED, OrderEvent,
                       OrderProcessor, SimLog, event)


def quiet() -> OrderProcessor:
    return OrderProcessor(SimLog(sink=lambda _line: None))


class OrderingTests(unittest.TestCase):
    def test_v2_v1_v2_again(self):
        p = quiet()
        self.assertEqual(p.receive(event("A", 2)), BUFFERED)
        self.assertEqual(p.state("A").version, 0)          # did not jump ahead
        self.assertEqual(p.receive(event("A", 1)), APPLIED)
        self.assertEqual(p.state("A").version, 2)          # v2 released
        self.assertEqual(p.receive(event("A", 2)), DUPLICATE)
        self.assertEqual(p.state("A").history, ["CREATED", "PAID"])

    def test_duplicate_while_buffered_is_ignored(self):
        p = quiet()
        p.receive(event("A", 3))
        self.assertEqual(p.receive(event("A", 3)), DUPLICATE)
        self.assertEqual(len(p.state("A").pending), 1)

    def test_missing_version_is_recorded_and_order_waits(self):
        p = quiet()
        p.receive(event("B", 1), tick=1)
        p.receive(event("B", 3), tick=2)
        st = p.state("B")
        self.assertEqual((st.version, st.status), (1, "CREATED"))
        self.assertEqual(st.missing_versions(), [2])
        self.assertEqual(p.check_stalled(tick=5, timeout=3), [("B", [2])])

    def test_gap_closes_when_missing_event_arrives(self):
        p = quiet()
        for v in (3, 1, 2):
            p.receive(event("C", v))
        st = p.state("C")
        self.assertEqual(st.history, ["CREATED", "PAID", "SHIPPED"])
        self.assertEqual(st.pending, {})
        self.assertEqual(p.check_stalled(tick=99, timeout=1), [])

    def test_only_later_version_on_fresh_order(self):
        p = quiet()
        p.receive(event("E", 3))
        self.assertIsNone(p.state("E").status)
        self.assertEqual(p.state("E").missing_versions(), [1, 2])

    def test_invalid_status_rejected(self):
        p = quiet()
        self.assertEqual(p.receive(OrderEvent("F", 1, "SHIPPED")), REJECTED)
        self.assertEqual(p.state("F").version, 0)

    def test_orders_are_independent(self):
        p = quiet()
        p.receive(event("X", 2))
        p.receive(event("Y", 1))
        self.assertEqual(p.state("X").version, 0)
        self.assertEqual(p.state("Y").version, 1)


if __name__ == "__main__":
    unittest.main()
