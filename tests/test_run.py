"""Tests for the 1080p-scaled ease reference distance in scripts.run."""

from core.policy import REFERENCE_SOURCE_H
from scripts.run import REFERENCE_DISTANCE_PX, scaled_duration_ms


def test_scaled_duration_unchanged_at_1080p():
    duration = scaled_duration_ms(REFERENCE_DISTANCE_PX, 320.0, 0.0, 10000.0, REFERENCE_SOURCE_H)
    assert abs(duration - 320.0) < 1e-9


def test_scaled_duration_reference_distance_doubles_at_4k():
    # At 4K the reference distance doubles, so the 1080p reference distance
    # now covers half the (scaled) reference, halving the duration.
    duration = scaled_duration_ms(REFERENCE_DISTANCE_PX, 320.0, 0.0, 10000.0, 2 * REFERENCE_SOURCE_H)
    assert abs(duration - 160.0) < 1e-9
