"""
Tests for the cross-platform linker's guard logic. These deliberately cover
only the paths that don't call the LLM (the API is exercised live, not in unit
tests): the linker must short-circuit to [] when fewer than two platforms are
present, so it never wastes an API call on input that can't possibly link
across platforms.
"""

from src import link


def test_single_platform_returns_empty_without_api_call():
    items = [
        {"name": "#a", "platform": "tiktok", "category": "other"},
        {"name": "#b", "platform": "tiktok", "category": "other"},
    ]
    # All one platform -> must return [] immediately (no cross-platform link
    # possible). If this hit the API it would raise/hang without a key.
    assert link.link_trends(items) == []


def test_empty_input_returns_empty():
    assert link.link_trends([]) == []
