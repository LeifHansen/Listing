"""Session-store naming and ordering helpers."""
from __future__ import annotations

import pytest

from backend import storage


def test_safe_session_name_strips_to_alnum():
    assert storage.safe_session_name("ebay-168433981627") == "ebay168433981627"
    assert storage.safe_session_name("3aaeb40637a1") == "3aaeb40637a1"


def test_safe_session_name_rejects_empty():
    with pytest.raises(ValueError):
        storage.safe_session_name("../../")


def test_image_index():
    assert storage.image_index("img_000.jpg") == 0
    assert storage.image_index("img_017.jpg") == 17
    assert storage.image_index("cover.jpg") == -1


def test_natural_key_orders_numbers_numerically():
    names = ["img_10.jpg", "img_2.jpg", "img_100.jpg", "img_20.jpg"]
    assert sorted(names, key=storage.natural_key) \
        == ["img_2.jpg", "img_10.jpg", "img_20.jpg", "img_100.jpg"]
