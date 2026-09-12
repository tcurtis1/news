"""Unit tests for Kalshi prediction market integration."""

import unittest
from unittest.mock import AsyncMock

import httpx

from app.bias import lean_for
from app.places import resolve_place
from app.search import portal_links
from app.trends import (
    PLATFORM_LABELS,
    PLATFORM_NOTES,
    PLATFORM_ORDER,
    _fetch_kalshi,
)


class KalshiIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def test_platform_constants(self):
        self.assertIn("kalshi", PLATFORM_ORDER)
        self.assertIn("kalshi", PLATFORM_LABELS)
        self.assertEqual(PLATFORM_LABELS["kalshi"], "Kalshi")
        self.assertIn("kalshi", PLATFORM_NOTES)
        self.assertEqual(PLATFORM_NOTES["kalshi"], "24h volume · Regulated")

    def test_place_platform_geo(self):
        place = resolve_place("US")
        pg = place.platform_geo()
        self.assertIn("kalshi", pg)
        self.assertEqual(pg["kalshi"]["scope"], "global")
        summary = place.coverage_summary()
        self.assertIn("kalshi", summary["global"])

    def test_portal_links(self):
        links = portal_links("fed rate cut")
        names = [pl.name for pl in links]
        self.assertIn("Kalshi", names)
        kalshi_link = next(pl for pl in links if pl.name == "Kalshi")
        self.assertIn("kalshi.com/markets?query=", kalshi_link.url)

    def test_bias_domain_check(self):
        info = lean_for("Kalshi Market", "https://kalshi.com/markets/kxratecut")
        self.assertEqual(info["lean"], "unclear")

    async def test_fetch_kalshi_mocked(self):
        mock_response_data = {
            "events": [
                {
                    "title": "Will OpenAI IPO in 2026?",
                    "series_ticker": "KXOAIIPO",
                    "event_ticker": "KXOAIIPO-26",
                    "category": "Financials",
                    "markets": [
                        {
                            "volume_24h_fp": "12500.50",
                            "volume_fp": "450000.00",
                            "last_price_dollars": "0.45",
                        },
                        {
                            "volume_24h_fp": "2500.00",
                            "volume_fp": "50000.00",
                            "last_price_dollars": "0.55",
                        },
                    ],
                },
                {
                    "title": "Will Fed cut interest rates in September?",
                    "series_ticker": "KXFEDRATE",
                    "event_ticker": "KXFEDRATE-26SEP",
                    "category": "Economics",
                    "markets": [
                        {
                            "volume_24h_fp": "5000.00",
                            "volume_fp": "100000.00",
                        }
                    ],
                },
            ]
        }

        mock_resp = httpx.Response(200, json=mock_response_data, request=httpx.Request("GET", "https://api.elections.kalshi.com"))
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get.return_value = mock_resp

        items = await _fetch_kalshi(client)
        self.assertEqual(len(items), 2)
        # Verify first item (highest volume)
        top = items[0]
        self.assertEqual(top.rank, 1)
        self.assertEqual(top.title, "Will OpenAI IPO in 2026?")
        self.assertEqual(top.platform, "kalshi")
        self.assertEqual(top.url, "https://kalshi.com/markets/kxoaiipo")
        self.assertIn("$15K 24h vol", top.snippet)
        self.assertIn("Financials", top.snippet)
        self.assertEqual(top.extra["volume24hr"], 15000.50)
        self.assertEqual(top.extra["volume"], 500000.00)

        # Verify second item
        second = items[1]
        self.assertEqual(second.rank, 2)
        self.assertEqual(second.title, "Will Fed cut interest rates in September?")
        self.assertEqual(second.url, "https://kalshi.com/markets/kxfedrate")
        self.assertIn("$5K 24h vol", second.snippet)
        self.assertIn("Economics", second.snippet)

    async def test_fetch_kalshi_error_handling(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get.side_effect = httpx.RequestError("Network connection timeout")

        items = await _fetch_kalshi(client)
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
