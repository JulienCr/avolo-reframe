"""Tests for the Box -> Rect boundary conversion (adapters/detector.py)."""

from adapters.detector import Box, to_source_rect


def test_to_source_rect_scales_every_optional_field():
    box = Box(
        x=0.1,
        y=0.2,
        w=0.3,
        h=0.4,
        score=0.9,
        anchor=(0.5, 0.6),
        crown=0.15,
        crown_margin=0.05,
        bust=(0.2, 0.25, 0.3, 0.35),
    )
    r = to_source_rect(box, source_w=1000, source_h=500)

    assert (r.x, r.y, r.w, r.h) == (100.0, 100.0, 300.0, 200.0)
    assert r.anchor == (500.0, 300.0)
    assert r.crown == 75.0
    assert r.crown_margin == 25.0
    assert r.bust == (200.0, 125.0, 300.0, 175.0)


def test_to_source_rect_keeps_none_fields_none():
    box = Box(x=0.0, y=0.0, w=1.0, h=1.0, score=1.0)
    r = to_source_rect(box, source_w=1920, source_h=1080)

    assert r.anchor is None
    assert r.crown is None
    assert r.crown_margin is None
    assert r.bust is None
