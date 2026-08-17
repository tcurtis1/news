"""Tests for dynamic progressive thumbnail resolution and background photo warming."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.search import enhance_hits_with_thumbnails


client = TestClient(app)


def test_enhance_hits_with_thumbnails():
    import asyncio
    hits = [
        {"title": "Test Story 1", "url": "https://example.com/story1", "image_url": "https://example.com/pic1.jpg"},
        {"title": "Test Story 2", "url": "https://example.com/story2", "image_url": None},
    ]
    enhanced = asyncio.run(enhance_hits_with_thumbnails(hits, max_fetch=5))
    assert len(enhanced) == 2
    assert enhanced[0]["image_url"] == "https://example.com/pic1.jpg"


def test_api_thumbnails_batch():
    res = client.post(
        "/api/thumbnails",
        json={
            "items": [
                {"url": "https://example.com/article-1", "title": "Article 1"},
                {"url": "https://example.com/article-2", "title": "Article 2"},
            ]
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert "thumbnails" in data
    assert isinstance(data["thumbnails"], dict)


def test_api_thumbnail_single():
    res = client.get("/api/thumbnail?url=https://example.com/article-test&title=Test")
    assert res.status_code == 200
    data = res.json()
    assert "image_url" in data
