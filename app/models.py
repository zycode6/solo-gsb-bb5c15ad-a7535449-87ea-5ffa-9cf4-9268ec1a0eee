"""Pydantic 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator


def _require_aware(v: datetime) -> datetime:
    if v.tzinfo is None or v.utcoffset() is None:
        raise ValueError("时间必须带时区（ISO 8601，建议使用 UTC，如 2026-09-21T00:00:00Z）")
    return v.astimezone(timezone.utc)


def _fmt_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class Sample(BaseModel):
    """单个潮位样本。"""

    time: datetime = Field(..., description="采样时刻，ISO 8601（UTC）")
    height: float = Field(..., description="潮位高度（米）")

    _check_time = field_validator("time")(_require_aware)

    @field_serializer("time", when_used="json")
    def _ser_time(self, v: datetime) -> str:
        return _fmt_utc(v)


class ComputeRequest(BaseModel):
    """创建计算任务的请求体。"""

    samples: list[Sample] = Field(..., min_length=2, description="按 UTC 严格递增的潮位样本")
    start: datetime = Field(..., description="查询起始时间，ISO 8601（UTC）")
    end: datetime = Field(..., description="查询结束时间，ISO 8601（UTC）")
    draft: float = Field(..., ge=0, description="船舶吃水（米）")
    min_clearance: float = Field(..., ge=0, description="最小富余水深（米）")
    max_gap_seconds: float = Field(
        ..., gt=0, description="允许插值的最大相邻样本间隔（秒），超过则该区段记为 unknown"
    )

    _check_start = field_validator("start")(_require_aware)
    _check_end = field_validator("end")(_require_aware)

    @model_validator(mode="after")
    def _check_order(self) -> "ComputeRequest":
        if self.end <= self.start:
            raise ValueError("end 必须大于 start")
        times = [s.time for s in self.samples]
        if any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError("samples 必须按时间严格递增（UTC），且不允许重复时刻")
        return self

    @property
    def safe_depth(self) -> float:
        """安全水深 = 吃水 + 最小富余。"""
        return self.draft + self.min_clearance


class Interval(BaseModel):
    start: datetime
    end: datetime

    @field_serializer("start", "end", when_used="json")
    def _ser_bounds(self, v: datetime) -> str:
        return _fmt_utc(v)


class UnknownInterval(Interval):
    reason: Literal["gap_exceeded", "no_data"] = Field(
        ..., description="gap_exceeded=相邻样本间隔超过 max_gap；no_data=超出样本覆盖范围"
    )


class ComputeResult(BaseModel):
    safe_depth: float = Field(..., description="安全水深（米）= draft + min_clearance")
    windows: list[Interval] = Field(..., description="潮高不低于安全水深的连续窗口（交点精确到秒）")
    unknown: list[UnknownInterval] = Field(..., description="禁止/无法插值的区段，窗口不跨越这些区段")


class TaskRecord(BaseModel):
    task_id: str
    status: Literal["done"]
    created_at: datetime
    request: ComputeRequest
    result: ComputeResult

    @field_serializer("created_at", when_used="json")
    def _ser_created(self, v: datetime) -> str:
        return _fmt_utc(v)
