"""FastAPI 应用：潮位通航窗口计算任务的创建与查询。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from . import db
from .core import compute_windows
from .models import ComputeRequest, ComputeResult, Interval, TaskRecord, UnknownInterval


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时完成数据库初始化
    db.init_db()
    yield


app = FastAPI(
    title="潮位通航窗口 API",
    version="1.0.0",
    description=(
        "基于 PCHIP 单调插值计算潮高不低于安全水深（吃水+最小富余）的全部连续通航窗口；"
        "相邻样本间隔超过 max_gap_seconds 的区段禁止插值并返回为 unknown。"
    ),
    lifespan=lifespan,
)


def _to_dt(epoch_seconds: float) -> datetime:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)


@app.get("/health", summary="健康检查")
def health() -> dict:
    return {"status": "ok"}


@app.post("/tasks", response_model=TaskRecord, status_code=201, summary="创建潮位窗口计算任务")
def create_task(req: ComputeRequest) -> TaskRecord:
    times = [s.time.timestamp() for s in req.samples]
    heights = [s.height for s in req.samples]
    windows, unknown = compute_windows(
        times=times,
        heights=heights,
        qstart=req.start.timestamp(),
        qend=req.end.timestamp(),
        threshold=req.safe_depth,
        max_gap=req.max_gap_seconds,
    )
    result = ComputeResult(
        safe_depth=req.safe_depth,
        windows=[Interval(start=_to_dt(s), end=_to_dt(e)) for s, e in windows],
        unknown=[
            UnknownInterval(start=_to_dt(s), end=_to_dt(e), reason=reason)
            for s, e, reason in unknown
        ],
    )
    task_id = uuid4().hex
    created_at = datetime.now(timezone.utc)
    db.save_task(
        task_id=task_id,
        created_at=created_at.isoformat(),
        request_json=req.model_dump_json(),
        result_json=result.model_dump_json(),
    )
    return TaskRecord(
        task_id=task_id, status="done", created_at=created_at, request=req, result=result
    )


@app.get("/tasks/{task_id}", response_model=TaskRecord, summary="按 ID 查询计算任务")
def get_task(task_id: str) -> TaskRecord:
    record = db.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    return TaskRecord(**record)
