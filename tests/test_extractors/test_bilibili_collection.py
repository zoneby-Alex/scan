"""Tests for Bilibili collection/playlist expansion."""
from unittest.mock import patch

import pytest

from src.extractors.bilibili import expand_collection, fetch_collection_author


def _mock_season_response(bvids: list[str], total: int = None, code: int = 0):
    if total is None:
        total = len(bvids)
    return type("Response", (), {
        "json": lambda self: {
            "code": code,
            "data": {
                "archives": [{"bvid": b} for b in bvids],
                "page": {"total": total},
            },
        },
    })()


def _mock_series_response(bvids: list[str], total: int = None, code: int = 0):
    return _mock_season_response(bvids, total, code)


def _mock_empty_response():
    return type("Response", (), {
        "json": lambda self: {"code": 0, "data": {"archives": [], "page": {"total": 0}}},
    })()


class TestExpandCollection:
    def test_season_collection(self, monkeypatch):
        calls = []
        def fake_get(url, params=None, **kw):
            calls.append((url, dict(params or {})))
            return _mock_season_response(["BV001", "BV002", "BV003"])
        monkeypatch.setattr("src.extractors.bilibili._retry_get", fake_get)

        urls = expand_collection("https://space.bilibili.com/123456/lists/7890?type=season")
        assert urls == [
            "https://www.bilibili.com/video/BV001",
            "https://www.bilibili.com/video/BV002",
            "https://www.bilibili.com/video/BV003",
        ]
        assert len(calls) == 1
        assert calls[0][1]["season_id"] == 7890

    def test_series_collection(self, monkeypatch):
        def fake_get(url, params=None, **kw):
            return _mock_series_response(["BV100"])
        monkeypatch.setattr("src.extractors.bilibili._retry_get", fake_get)

        urls = expand_collection("https://space.bilibili.com/353711/lists/5833069")
        assert urls == ["https://www.bilibili.com/video/BV100"]

    def test_empty_collection(self, monkeypatch):
        monkeypatch.setattr("src.extractors.bilibili._retry_get",
                           lambda *a, **kw: _mock_empty_response())
        urls = expand_collection("https://space.bilibili.com/123/lists/456?type=season")
        assert urls == []

    def test_non_collection_url_returns_empty(self):
        assert expand_collection("https://www.bilibili.com/video/BV123") == []
        assert expand_collection("https://www.youtube.com/watch?v=abc") == []

    def test_dedup_duplicate_bvids(self, monkeypatch):
        def fake_get(url, params=None, **kw):
            return _mock_season_response(["BV001", "BV001", "BV002"])
        monkeypatch.setattr("src.extractors.bilibili._retry_get", fake_get)

        urls = expand_collection("https://space.bilibili.com/1/lists/1?type=season")
        assert urls == [
            "https://www.bilibili.com/video/BV001",
            "https://www.bilibili.com/video/BV002",
        ]

    def test_pagination(self, monkeypatch):
        page = [0]
        def fake_get(url, params=None, **kw):
            page[0] += 1
            if page[0] == 1:
                return _mock_season_response([f"BV00{i}" for i in range(100)], total=150)
            return _mock_season_response([f"BV0{i}" for i in range(50)], total=150)
        monkeypatch.setattr("src.extractors.bilibili._retry_get", fake_get)

        urls = expand_collection("https://space.bilibili.com/1/lists/1?type=season")
        assert len(urls) == 150
        assert urls[0] == "https://www.bilibili.com/video/BV000"
        assert urls[-1] == "https://www.bilibili.com/video/BV049"

    def test_api_error_returns_empty(self, monkeypatch):
        monkeypatch.setattr("src.extractors.bilibili._retry_get",
                           lambda *a, **kw: _mock_season_response([], code=-404))
        urls = expand_collection("https://space.bilibili.com/1/lists/1?type=season")
        assert urls == []


class TestFetchCollectionAuthor:
    def test_returns_author_name(self, monkeypatch):
        def fake_get(url, params=None, **kw):
            return type("Response", (), {
                "json": lambda self: {"code": 0, "data": {"name": "TestUP"}},
            })()
        monkeypatch.setattr("src.extractors.bilibili._retry_get", fake_get)

        assert fetch_collection_author("https://space.bilibili.com/123456/lists/7890") == "TestUP"

    def test_api_error_returns_empty(self, monkeypatch):
        monkeypatch.setattr("src.extractors.bilibili._retry_get",
                           lambda *a, **kw: (_ for _ in ()).throw(Exception("fail")))
        assert fetch_collection_author("https://space.bilibili.com/123/lists/456") == ""

    def test_non_collection_url_returns_empty(self):
        assert fetch_collection_author("https://www.bilibili.com/video/BV123") == ""
