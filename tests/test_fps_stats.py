from __future__ import annotations

import pytest

from fpsprobe.analysis.fps_stats import describe, histogram, p95_p50_ratio


def test_empty_sequence_is_safe():
    stats = describe([])
    assert stats["count"] == 0
    assert all(stats[key] is None for key in ("min", "max", "mean", "median", "p95", "stdev", "jitter"))
    assert histogram([], 4)["buckets"] == []
    assert p95_p50_ratio(stats) is None


def test_single_value():
    stats = describe([1000])
    assert stats["count"] == 1
    assert stats["min"] == stats["max"] == 1000.0
    assert stats["stdev"] == 0.0
    assert stats["jitter"] == 0.0
    assert p95_p50_ratio(stats) == 1.0


def test_zero_mean_returns_none_jitter():
    stats = describe([0, 0, 0])
    assert stats["mean"] == 0.0
    assert stats["jitter"] is None
    assert p95_p50_ratio(stats) is None


def test_negative_and_mixed_deltas_do_not_crash():
    stats = describe([-100, 0, 100])
    assert stats["count"] == 3
    assert stats["min"] == -100.0
    assert stats["max"] == 100.0
    assert stats["mean"] == 0.0
    assert stats["jitter"] is None


def test_histogram_all_equal_and_single_bucket():
    result = histogram([1000, 1000, 1000], 8)
    assert result["total"] == 3
    assert result["bucket_count"] == 1
    assert result["buckets"][0]["count"] == 3
    single = histogram([1000, 2000], 1)
    assert single["bucket_count"] == 1
    assert single["buckets"][0]["count"] == 2


def test_histogram_counts_sum_to_total_and_respect_bucket_limit():
    values = [500, 700, 1000, 1200, 1500, 1500, 2000]
    for buckets in (2, 3, 8):
        result = histogram(values, buckets)
        assert result["bucket_count"] <= buckets
        assert sum(bucket["count"] for bucket in result["buckets"]) == len(values)
        assert result["total"] == len(values)
        assert result["min"] == 500.0
        assert result["max"] == 2000.0


def test_histogram_rejects_invalid_bucket_count():
    with pytest.raises(ValueError):
        histogram([1], 0)
    with pytest.raises(ValueError):
        histogram([1], True)
