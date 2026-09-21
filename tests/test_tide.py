"""核心计算与 API 的测试。"""

import math
import os

import pytest

os.environ["DATABASE_PATH"] = "/tmp/test_tide_windows.db"

from fastapi.testclient import TestClient

from app.core import compute_windows
from app.main import app

T0 = 1_800_000_000.0  # 任意基准时刻（整秒）


def make_sine_samples(period=12 * 3600.0, step=600.0, n=145, mean=3.0, amp=2.0):
    times = [T0 + i * step for i in range(n)]
    heights = [mean + amp * math.sin(2 * math.pi * (t - T0) / period) for t in times]
    return times, heights


# ---------------------------------------------------------------- core


def test_sine_windows_match_analytic_crossings():
    """正弦潮位：窗口边界应与解析交点一致（精确到秒）。"""
    period = 12 * 3600.0
    times, heights = make_sine_samples(period=period)
    threshold = 4.0  # sin >= 0.5 -> 相位 ∈ [π/6, 5π/6]
    windows, unknown = compute_windows(
        times, heights, T0, T0 + period, threshold, max_gap=3600.0
    )
    assert unknown == []
    assert len(windows) == 1
    start, end = windows[0]
    # 解析交点：sin(2πt/T) = 0.5
    exp_start = T0 + period / 12
    exp_end = T0 + 5 * period / 12
    assert abs(start - exp_start) <= 1.0
    assert abs(end - exp_end) <= 1.0
    # 交点必须是整秒
    assert start == math.floor(start)
    assert end == math.floor(end)


def test_gap_creates_unknown_and_windows_do_not_cross():
    """间隔超过 max_gap 的区段返回 unknown，窗口不得跨越。"""
    times = [T0 + i * 600.0 for i in range(10)]  # 0..5400
    gap_start = times[-1]
    times += [gap_start + 6 * 3600.0 + i * 600.0 for i in range(10)]
    heights = [5.0] * 20  # 全部高于阈值
    windows, unknown = compute_windows(
        times, heights, T0, times[-1], threshold=4.0, max_gap=3600.0
    )
    assert unknown == [(gap_start, gap_start + 6 * 3600.0, "gap_exceeded")]
    assert windows == [(T0, gap_start), (gap_start + 6 * 3600.0, times[-1])]
    for ws, we in windows:
        for us, ue, _ in unknown:
            assert we <= us or ws >= ue  # 窗口与 unknown 不重叠


def test_all_below_threshold_no_windows():
    times = [T0 + i * 600.0 for i in range(10)]
    windows, unknown = compute_windows(
        times, [1.0] * 10, T0, times[-1], threshold=4.0, max_gap=3600.0
    )
    assert windows == []
    assert unknown == []


def test_query_outside_coverage_is_unknown():
    times = [T0 + i * 600.0 for i in range(10)]
    windows, unknown = compute_windows(
        times, [5.0] * 10, T0 - 3600.0, times[-1] + 3600.0, 4.0, max_gap=3600.0
    )
    assert windows == [(T0, times[-1])]
    assert unknown == [
        (T0 - 3600.0, T0, "no_data"),
        (times[-1], times[-1] + 3600.0, "no_data"),
    ]


def test_window_clipped_to_query_range():
    times, heights = make_sine_samples()
    windows, _ = compute_windows(
        times, heights, T0 + 3600.0, T0 + 7200.0, threshold=3.0, max_gap=3600.0
    )
    assert windows, "应至少有一个窗口"
    for ws, we in windows:
        assert ws >= T0 + 3600.0
        assert we <= T0 + 7200.0


def test_single_sample_block_yields_no_windows():
    """孤立单点块无法插值，不产生窗口。"""
    times = [T0, T0 + 600.0, T0 + 600.0 + 7200.0]  # 第三点为孤立点
    heights = [5.0, 5.0, 5.0]
    windows, unknown = compute_windows(
        times, heights, T0, times[-1], 4.0, max_gap=3600.0
    )
    assert windows == [(T0, T0 + 600.0)]
    assert unknown == [(T0 + 600.0, times[-1], "gap_exceeded")]


# ---------------------------------------------------------------- api


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def _payload():
    return {
        "samples": [
            {"time": "2026-09-21T00:00:00Z", "height": 2.0},
            {"time": "2026-09-21T03:00:00Z", "height": 5.0},
            {"time": "2026-09-21T06:00:00Z", "height": 2.0},
        ],
        "start": "2026-09-21T00:00:00Z",
        "end": "2026-09-21T06:00:00Z",
        "draft": 3.0,
        "min_clearance": 0.5,
        "max_gap_seconds": 4 * 3600,
    }


def test_create_and_get_task(client):
    r = client.post("/tasks", json=_payload())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "done"
    assert body["result"]["safe_depth"] == 3.5
    assert len(body["result"]["windows"]) == 1
    w = body["result"]["windows"][0]
    assert w["start"].endswith("Z") and w["end"].endswith("Z")

    task_id = body["task_id"]
    r2 = client.get(f"/tasks/{task_id}")
    assert r2.status_code == 200
    assert r2.json()["task_id"] == task_id
    assert r2.json()["result"] == body["result"]


def test_get_missing_task_404(client):
    assert client.get("/tasks/does-not-exist").status_code == 404


def test_unsorted_samples_rejected(client):
    p = _payload()
    p["samples"] = [p["samples"][1], p["samples"][0], p["samples"][2]]
    assert client.post("/tasks", json=p).status_code == 422


def test_end_before_start_rejected(client):
    p = _payload()
    p["end"] = "2026-09-20T23:00:00Z"
    assert client.post("/tasks", json=p).status_code == 422


def test_naive_time_rejected(client):
    p = _payload()
    p["start"] = "2026-09-21T00:00:00"
    assert client.post("/tasks", json=p).status_code == 422
