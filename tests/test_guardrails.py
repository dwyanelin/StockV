"""Unit tests for rate limiting, in-flight deduplication, daily quota, and tiered caching."""
from __future__ import annotations

import threading
import types
import unittest
from unittest import mock

from app.guardrails import CACHE_TTL_BY_KIND, RateLimiter, ResponseCache


class RateLimiterTests(unittest.TestCase):
    def test_user_4s_cooldown(self) -> None:
        rl = RateLimiter(user_cooldown=4.0, group_cmd_cooldown=30.0, daily_limit=1500)
        self.assertTrue(rl.try_acquire("U1", None, "P", "2330", now=100.0).allowed)
        # Same user within 4s blocked even on different command/stock
        r = rl.try_acquire("U1", None, "K", "AAPL", now=103.5)
        self.assertFalse(r.allowed)
        self.assertEqual(r.reason, "user_cooldown")
        # Different user is unaffected
        self.assertTrue(rl.try_acquire("U2", None, "K", "AAPL", now=103.5).allowed)
        # Same user allowed once 4s elapsed
        self.assertTrue(rl.try_acquire("U1", None, "K", "AAPL", now=104.0).allowed)

    def test_group_30s_same_stock_cmd_cooldown(self) -> None:
        rl = RateLimiter(user_cooldown=3.0, group_cmd_cooldown=30.0, daily_limit=1500)
        self.assertTrue(rl.try_acquire("U1", "G1", "K", "2330", now=100.0).allowed)
        # Another user in the same group requesting same (K, 2330) within 30s -> blocked
        r = rl.try_acquire("U2", "G1", "K", "2330", now=115.0)
        self.assertFalse(r.allowed)
        self.assertEqual(r.reason, "group_cooldown")
        # Different command on same stock in same group -> allowed
        self.assertTrue(rl.try_acquire("U2", "G1", "T", "2330", now=115.0).allowed)
        # Same command on same stock in a DIFFERENT group -> allowed
        self.assertTrue(rl.try_acquire("U3", "G2", "K", "2330", now=115.0).allowed)
        # Same group after 30s cooldown expires -> allowed again
        self.assertTrue(rl.try_acquire("U2", "G1", "K", "2330", now=130.0).allowed)

    def test_concurrent_clicks_atomic_dedup(self) -> None:
        rl = RateLimiter(user_cooldown=3.0, group_cmd_cooldown=30.0, daily_limit=1500)
        results: list[bool] = []
        barrier = threading.Barrier(10)

        def worker() -> None:
            barrier.wait()
            results.append(rl.try_acquire("U1", "G1", "KW", "7822", now=200.0).allowed)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(results), 1)

    def test_release_group_slot_on_error(self) -> None:
        rl = RateLimiter(user_cooldown=3.0, group_cmd_cooldown=30.0, daily_limit=1500)
        self.assertTrue(rl.try_acquire("U1", "G1", "T", "2330", now=100.0).allowed)
        rl.release_group_slot("G1", "T", "2330")
        self.assertTrue(rl.try_acquire("U2", "G1", "T", "2330", now=101.0).allowed)

    def test_daily_hard_cap_and_rollover(self) -> None:
        rl = RateLimiter(user_cooldown=0.0, group_cmd_cooldown=0.0, daily_limit=2)
        with mock.patch.object(RateLimiter, "_today_key", return_value="2026-11-26"):
            self.assertTrue(rl.try_acquire("U1", None, "P", "2330", now=1.0).allowed)
            self.assertTrue(rl.try_acquire("U2", None, "P", "2330", now=2.0).allowed)
            r = rl.try_acquire("U3", None, "P", "2330", now=3.0)
            self.assertFalse(r.allowed)
            self.assertEqual(r.reason, "daily_limit")
        # Next day resets counter automatically
        with mock.patch.object(RateLimiter, "_today_key", return_value="2026-11-27"):
            self.assertTrue(rl.try_acquire("U3", None, "P", "2330", now=4.0).allowed)


class ResponseCacheTests(unittest.TestCase):
    def test_tiered_ttls(self) -> None:
        self.assertEqual(CACHE_TTL_BY_KIND["#"], 10.0)
        self.assertEqual(CACHE_TTL_BY_KIND["P"], 10.0)
        self.assertEqual(CACHE_TTL_BY_KIND["K"], 30.0)
        self.assertEqual(CACHE_TTL_BY_KIND["KW"], 30.0)
        self.assertEqual(CACHE_TTL_BY_KIND["KM"], 30.0)
        self.assertEqual(CACHE_TTL_BY_KIND["T"], 600.0)
        self.assertEqual(CACHE_TTL_BY_KIND["D"], 600.0)
        self.assertEqual(CACHE_TTL_BY_KIND["F"], 600.0)

    def test_prune_removes_stale_entries(self) -> None:
        rl = RateLimiter(user_cooldown=4.0, group_cmd_cooldown=30.0, daily_limit=5000)
        # Seed >500 user entries and >1000 group entries with old timestamps
        for i in range(505):
            rl._user_ts[f"U{i}"] = 1.0
        for i in range(1005):
            rl._group_ts[(f"G{i}", "P", "2330")] = 1.0
        self.assertTrue(rl.try_acquire("U_NEW", "G_NEW", "P", "2330", now=1000.0).allowed)
        self.assertEqual(len(rl._user_ts), 1)
        self.assertEqual(len(rl._group_ts), 1)

    def test_get_set_and_expiry(self) -> None:
        cache = ResponseCache()
        cache.set("P", "2330", ["P_MSG"], now=100.0)
        cache.set("K", "2330", ["K_MSG"], now=100.0)
        cache.set("T", "2330", ["T_MSG"], now=100.0)

        self.assertEqual(cache.get("P", "2330", now=109.9), ["P_MSG"])
        self.assertIsNone(cache.get("P", "2330", now=110.0))

        self.assertEqual(cache.get("K", "2330", now=129.9), ["K_MSG"])
        self.assertIsNone(cache.get("K", "2330", now=130.0))

        self.assertEqual(cache.get("T", "2330", now=699.9), ["T_MSG"])
        self.assertIsNone(cache.get("T", "2330", now=700.0))

    def test_cache_eviction_at_max_entries(self) -> None:
        cache = ResponseCache(max_entries=2)
        cache.set("P", "1101", ["A"], now=100.0)   # expires 110
        cache.set("K", "2330", ["B"], now=101.0)   # expires 131
        cache.set("T", "3443", ["C"], now=115.0)   # triggers expired eviction of 1101
        self.assertIsNone(cache.get("P", "1101", now=115.0))
        self.assertEqual(cache.get("K", "2330", now=115.0), ["B"])
        # Adding a 3rd active entry evicts the oldest remaining (2330)
        cache.set("T", "7822", ["D"], now=116.0)
        self.assertIsNone(cache.get("K", "2330", now=116.0))
        self.assertEqual(cache.get("T", "7822", now=116.0), ["D"])


class MainIntegrationGuardrailTests(unittest.TestCase):
    def test_group_spam_silently_dropped_and_cache_reused(self) -> None:
        from app import main
        from app.stock import tw_directory

        main.rate_limiter.reset()
        main.response_cache.clear()
        tw_directory._cache = tw_directory._build_index(
            [tw_directory.TwStock("2330", "台積電", ".TW", "TWSE")]
        )

        ev1 = types.SimpleNamespace(
            source=types.SimpleNamespace(type="group", user_id="U1", group_id="G1"),
            reply_token="tok1",
        )
        ev2 = types.SimpleNamespace(
            source=types.SimpleNamespace(type="group", user_id="U2", group_id="G1"),
            reply_token="tok2",
        )
        ev_dm = types.SimpleNamespace(
            source=types.SimpleNamespace(type="user", user_id="U2"),
            reply_token="tok3",
        )

        with mock.patch.object(main, "_handle_command", return_value=["CARD"]) as handle_mock, \
             mock.patch.object(main, "_start_loading"), \
             mock.patch.object(main, "_reply") as reply_mock:
            # 1st group request: K台積電 -> canonical key 2330 -> executes & caches
            main._execute_and_reply(ev1, "K", "台積電", is_group=True)
            self.assertEqual(handle_mock.call_count, 1)
            self.assertEqual(reply_mock.call_count, 1)

            # 2nd group request: U2 asks K2330 within 30s -> silently dropped!
            main._execute_and_reply(ev2, "K", "2330", is_group=True)
            self.assertEqual(handle_mock.call_count, 1)
            self.assertEqual(reply_mock.call_count, 1)

            # 3rd request: U2 asks K2330 in 1-on-1 DM -> allowed AND served from cache!
            main._execute_and_reply(ev_dm, "K", "2330", is_group=False)
            self.assertEqual(handle_mock.call_count, 1)  # cache hit!
            self.assertEqual(reply_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
