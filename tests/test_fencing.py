import unittest

from order_sim import FencedOrderStore, LockService, SimLog, StoredOrder, Worker


def setup(enforce=True):
    log = SimLog(sink=lambda _line: None)
    store = FencedOrderStore(log, enforce_fencing=enforce)
    store.orders["O"] = StoredOrder(status="PAID")
    return LockService(lease_ticks=5, log=log), store


class FencingTests(unittest.TestCase):
    def test_tokens_increase_and_lease_is_exclusive(self):
        lock, _ = setup()
        self.assertEqual(lock.acquire("A", tick=0), 1)
        self.assertIsNone(lock.acquire("B", tick=2))       # still leased
        self.assertEqual(lock.acquire("B", tick=5), 2)     # expired

    def test_newer_token_accepted_older_rejected(self):
        lock, store = setup()
        a = Worker("A", lock.acquire("A", tick=0))
        b = Worker("B", lock.acquire("B", tick=6))
        self.assertTrue(b.update(store, "O", "SHIPPED", "TRK-B", tick=7))
        self.assertFalse(a.update(store, "O", "SHIPPED", "TRK-A", tick=8))
        self.assertEqual(store.orders["O"].tracking, "TRK-B")
        self.assertEqual(store.orders["O"].highest_token, 2)

    def test_same_token_can_write_repeatedly(self):
        lock, store = setup()
        b = Worker("B", lock.acquire("B", tick=0))
        self.assertTrue(b.update(store, "O", "SHIPPED", "t1", tick=1))
        self.assertTrue(b.update(store, "O", "SHIPPED", "t2", tick=2))

    def test_without_fencing_stale_write_wins(self):
        lock, store = setup(enforce=False)
        a = Worker("A", lock.acquire("A", tick=0))
        b = Worker("B", lock.acquire("B", tick=6))
        b.update(store, "O", "SHIPPED", "TRK-B", tick=7)
        a.update(store, "O", "SHIPPED", "TRK-A", tick=8)
        self.assertEqual(store.orders["O"].tracking, "TRK-A")  # lost update


if __name__ == "__main__":
    unittest.main()
