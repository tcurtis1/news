import asyncio
import unittest

import app.coalesce as coalesce


class CoalescedTests(unittest.IsolatedAsyncioTestCase):
    """app.coalesce.coalesced() is the single-flight helper used by
    pulse.py/trends.py/search.py to stop concurrent requests for the same
    not-yet-cached resource from each firing an independent upstream fetch
    (RSS/Reddit/Trends scrape) — the "cache stampede" bug already fixed the
    same way in the finance repo's app/cache.get_or_fetch()."""

    def setUp(self):
        coalesce._inflight.clear()

    def tearDown(self):
        coalesce._inflight.clear()

    async def test_concurrent_calls_share_one_fetch(self):
        calls = []

        async def slow_fetch():
            calls.append(1)
            await asyncio.sleep(0.05)
            return {"value": 42}

        results = await asyncio.gather(
            *[coalesce.coalesced("k", slow_fetch) for _ in range(5)]
        )

        self.assertEqual(len(calls), 1)
        for r in results:
            self.assertEqual(r["value"], 42)
        self.assertEqual(coalesce._inflight, {})

    async def test_exception_propagates_to_every_waiter(self):
        async def failing_fetch():
            await asyncio.sleep(0.02)
            raise RuntimeError("upstream down")

        results = await asyncio.gather(
            *[coalesce.coalesced("k", failing_fetch) for _ in range(3)],
            return_exceptions=True,
        )
        for r in results:
            self.assertIsInstance(r, RuntimeError)
        self.assertEqual(coalesce._inflight, {})

    async def test_different_keys_never_share_a_fetch(self):
        calls = []

        async def fetch():
            calls.append(1)
            return calls[-1]

        await asyncio.gather(
            coalesce.coalesced("a", fetch), coalesce.coalesced("b", fetch)
        )
        self.assertEqual(len(calls), 2)

    async def test_one_callers_own_timeout_does_not_kill_the_shared_fetch(self):
        """The real bug this has to avoid: a caller wrapping coalesced() in
        its own asyncio.wait_for(budget) must not cancel the underlying
        fetch out from under a different, still-waiting caller with a
        longer budget. Without asyncio.shield() inside coalesced(), this
        would raise CancelledError in the long-budget caller too."""
        started = asyncio.Event()
        finished = asyncio.Event()

        async def slow_fetch():
            started.set()
            await asyncio.sleep(0.15)
            finished.set()
            return "done"

        async def short_budget_caller():
            try:
                return await asyncio.wait_for(
                    coalesce.coalesced("k", slow_fetch), timeout=0.05
                )
            except asyncio.TimeoutError:
                return "timed-out"

        async def long_budget_caller():
            await started.wait()
            return await asyncio.wait_for(
                coalesce.coalesced("k", slow_fetch), timeout=1.0
            )

        short_result, long_result = await asyncio.gather(
            short_budget_caller(), long_budget_caller()
        )

        self.assertEqual(short_result, "timed-out")
        self.assertEqual(long_result, "done")
        self.assertTrue(finished.is_set())
        self.assertEqual(coalesce._inflight, {})

    async def test_inflight_cleared_even_if_every_waiter_gives_up(self):
        async def slow_fetch():
            await asyncio.sleep(0.1)
            return "done"

        async def caller():
            return await asyncio.wait_for(
                coalesce.coalesced("k", slow_fetch), timeout=0.02
            )

        with self.assertRaises(asyncio.TimeoutError):
            await caller()

        # The underlying task is still running in the background (nobody
        # cancelled it, only the caller's own wait). Give it time to finish
        # and confirm it self-cleans from the registry via its done callback.
        await asyncio.sleep(0.15)
        self.assertEqual(coalesce._inflight, {})


if __name__ == "__main__":
    unittest.main()
