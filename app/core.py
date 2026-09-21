"""潮位通航窗口核心计算。

基于 PCHIP（分段单调三次 Hermite）插值，求潮高不低于安全水深的全部连续
窗口。阈值交点通过逐段解析求根获得，并四舍五入到整秒。相邻样本间隔超过
max_gap 的区段禁止插值，作为 unknown 区段返回，窗口不会跨越这些区段。

时间一律使用 Unix 秒（float），由 API 层负责与 ISO 8601 互转。
"""

from __future__ import annotations

import numpy as np
from scipy.interpolate import PchipInterpolator

# 求根时把落在区间端点 ±tol 内的根钳到端点上
_ROOT_TOL = 1e-9
# 合并相邻子区间时允许的边界缝隙（秒）
_MERGE_TOL = 1e-6


def _segment_roots(coeffs: np.ndarray, dt: float) -> list[float]:
    """求单个 PCHIP 段在局部坐标 dx ∈ [0, dt] 上的实根。

    coeffs 为 PPoly.c[:, i]，即 [a, b, c, d]，对应
    a*dx**3 + b*dx**2 + c*dx + d = 0。
    """
    c = np.asarray(coeffs, dtype=float)
    peak = float(np.max(np.abs(c)))
    if peak == 0.0:
        # 整段恒等于阈值：没有孤立交点（该段整体视为可用）
        return []
    c = c / peak
    # 丢弃相对可忽略的高次项，避免 np.roots 数值不稳定
    first = 0
    while first < len(c) - 1 and abs(c[first]) < 1e-12:
        first += 1
    c = c[first:]
    if len(c) < 2:
        return []
    roots: list[float] = []
    for r in np.roots(c):
        if abs(r.imag) > 1e-9 * max(1.0, abs(r.real)):
            continue
        x = float(r.real)
        if -_ROOT_TOL <= x <= dt + _ROOT_TOL:
            roots.append(min(max(x, 0.0), dt))
    return roots


def _round_second(x: float) -> float:
    """四舍五入到整秒（阈值交点精确到秒）。"""
    return float(np.floor(x + 0.5))


def _block_windows(
    ts: list[float],
    hs: list[float],
    qlo: float,
    qhi: float,
    threshold: float,
) -> list[tuple[float, float]]:
    """在一个连续样本块（块内间隔均 <= max_gap）内求窗口，并裁剪到 [qlo, qhi]。"""
    lo = max(qlo, ts[0])
    hi = min(qhi, ts[-1])
    if hi <= lo or len(ts) < 2:
        return []

    # 直接对 (潮高 - 安全水深) 做 PCHIP，零点即阈值交点
    shifted = np.asarray(hs, dtype=float) - threshold
    p = PchipInterpolator(np.asarray(ts, dtype=float), shifted, extrapolate=False)

    bounds = {lo, hi}
    for i in range(len(ts) - 1):
        dt = ts[i + 1] - ts[i]
        for r in _segment_roots(p.c[:, i], dt):
            x = _round_second(ts[i] + r)
            if lo < x < hi:
                bounds.add(x)

    pts = sorted(bounds)
    windows: list[list[float]] = []
    for u, v in zip(pts[:-1], pts[1:]):
        mid = 0.5 * (u + v)
        if float(p(mid)) >= 0.0:  # 潮高 >= 安全水深
            if windows and u - windows[-1][1] <= _MERGE_TOL:
                windows[-1][1] = v
            else:
                windows.append([u, v])
    return [(u, v) for u, v in windows if v > u]


def compute_windows(
    times: list[float],
    heights: list[float],
    qstart: float,
    qend: float,
    threshold: float,
    max_gap: float,
) -> tuple[list[tuple[float, float]], list[tuple[float, float, str]]]:
    """计算全部通航窗口与 unknown 区段。

    参数（时间均为 Unix 秒）：
        times      严格递增的样本时刻
        heights    对应潮位高度
        qstart/qend 查询起止时间
        threshold  安全水深 = 吃水 + 最小富余
        max_gap    允许插值的最大相邻样本间隔（秒）

    返回：
        windows  [(start, end), ...]            潮高 >= 安全水深的连续窗口
        unknown  [(start, end, reason), ...]    reason ∈ {gap_exceeded, no_data}
    """
    ts = [float(t) for t in times]
    hs = [float(h) for h in heights]

    windows: list[tuple[float, float]] = []
    unknown: list[tuple[float, float, str]] = []

    # 样本覆盖范围之外无法插值，记为 unknown
    if qstart < ts[0]:
        unknown.append((qstart, min(qend, ts[0]), "no_data"))
    if qend > ts[-1]:
        unknown.append((max(qstart, ts[-1]), qend, "no_data"))

    # 按 max_gap 把样本切成若干连续块，块间间隔即为 unknown 区段
    block_start = 0
    for i in range(len(ts) - 1):
        if ts[i + 1] - ts[i] > max_gap:
            windows.extend(
                _block_windows(ts[block_start : i + 1], hs[block_start : i + 1], qstart, qend, threshold)
            )
            glo, ghi = max(qstart, ts[i]), min(qend, ts[i + 1])
            if ghi > glo:
                unknown.append((glo, ghi, "gap_exceeded"))
            block_start = i + 1
    windows.extend(_block_windows(ts[block_start:], hs[block_start:], qstart, qend, threshold))

    windows.sort()
    unknown.sort()
    return windows, unknown
