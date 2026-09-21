# 潮位通航窗口 API

基于 **FastAPI + SciPy（PCHIP 单调插值）+ SQLite** 的潮位通航窗口计算服务。

输入按 UTC 递增的潮位样本、查询起止时间、船舶吃水和最小富余水深，服务求出
**潮高不低于安全水深（= 吃水 + 最小富余）的全部连续时间窗口**，阈值交点通过
PCHIP 逐段解析求根并精确到秒。相邻样本间隔超过 `max_gap_seconds` 的区段禁止
插值，以 `unknown` 返回，窗口不会跨越这些区段。

## 快速启动（单条命令）

```bash
docker compose up --build
```

启动后访问：

- API 地址： http://localhost:8000
- 交互式文档（Swagger UI）： http://localhost:8000/docs
- 健康检查： http://localhost:8000/health

依赖安装与 SQLite 建表均在容器启动时自动完成，无需额外步骤。

## 配置

| 环境变量         | 默认值                        | 说明                     |
| ---------------- | ----------------------------- | ------------------------ |
| `DATABASE_PATH`  | `/app/data/tide_windows.db`   | SQLite 数据库文件路径    |

Compose 默认将宿主机 `8000` 端口映射到容器 `8000`，并通过命名卷 `tide-data`
持久化数据库。如需修改端口，编辑 `docker-compose.yml` 中的 `ports`。

## API 说明

### `POST /tasks` — 创建计算任务

请求体：

```json
{
  "samples": [
    {"time": "2026-09-21T00:00:00Z", "height": 1.2},
    {"time": "2026-09-21T01:00:00Z", "height": 2.6},
    {"time": "2026-09-21T02:00:00Z", "height": 4.1},
    {"time": "2026-09-21T03:00:00Z", "height": 5.3},
    {"time": "2026-09-21T04:00:00Z", "height": 5.8},
    {"time": "2026-09-21T05:00:00Z", "height": 5.1},
    {"time": "2026-09-21T06:00:00Z", "height": 3.9}
  ],
  "start": "2026-09-21T00:00:00Z",
  "end": "2026-09-21T06:00:00Z",
  "draft": 3.5,
  "min_clearance": 0.5,
  "max_gap_seconds": 7200
}
```

字段约束：

- `samples`：至少 2 个，时间必须带时区且**严格递增**（不允许重复时刻）；`height` 单位米
- `start` / `end`：ISO 8601 带时区时间，且 `end > start`
- `draft` ≥ 0：船舶吃水（米）
- `min_clearance` ≥ 0：最小富余水深（米）
- `max_gap_seconds` > 0：允许插值的最大相邻样本间隔（秒）

响应 `201 Created`（节选）：

```json
{
  "task_id": "a07302d93fe24a9b87e1b68e927a1bd2",
  "status": "done",
  "created_at": "2026-09-21T06:19:45Z",
  "request": { "...": "原始请求" },
  "result": {
    "safe_depth": 4.0,
    "windows": [
      {"start": "2026-09-21T01:55:35Z", "end": "2026-09-21T05:55:31Z"}
    ],
    "unknown": [
      {"start": "2026-09-21T08:00:00Z", "end": "2026-09-21T18:00:00Z", "reason": "gap_exceeded"}
    ]
  }
}
```

- `windows`：潮高 ≥ 安全水深的连续窗口，边界为阈值交点（精确到秒）或查询/样本边界
- `unknown`：禁止或无法插值的区段
  - `gap_exceeded`：相邻样本间隔超过 `max_gap_seconds`
  - `no_data`：查询范围超出样本覆盖时段
- 窗口与 `unknown` 区段互不重叠，窗口不会跨越 `unknown` 区段

### `GET /tasks/{task_id}` — 按 ID 查询任务

返回与创建时相同的任务记录（含请求与结果）；任务不存在时返回 `404`。

```bash
curl http://localhost:8000/tasks/a07302d93fe24a9b87e1b68e927a1bd2
```

### 错误响应

- `422`：请求校验失败（样本未递增、时间缺时区、`end <= start`、参数越界等）
- `404`：任务 ID 不存在

## 本地开发（不用 Docker）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

运行测试：

```bash
pytest
```

## 实现要点

- **插值**：`scipy.interpolate.PchipInterpolator` 直接作用于 `潮高 − 安全水深`，
  保证单调性、不产生过冲
- **交点求根**：逐段取 PCHIP 三次多项式系数解析求根（`numpy.roots`），
  根四舍五入到整秒作为窗口边界
- **区间判定**：以交点和查询边界把定义域切分为子区间，取中点符号判定是否
  处于窗口内，再合并相邻子区间
- **空窗区段**：按 `max_gap_seconds` 将样本切分为连续块，块内插值、块间
  记为 `unknown`，从机制上保证窗口不跨越空窗区段
- **持久化**：SQLite，启动时 `CREATE TABLE IF NOT EXISTS` 自动初始化，
  任务请求与结果以 JSON 存储，可按 ID 检索

## 项目结构

```
app/
  main.py    FastAPI 应用与路由（POST /tasks、GET /tasks/{id}、GET /health）
  core.py    PCHIP 插值、逐段求根、窗口与 unknown 区段计算
  models.py  Pydantic 请求/响应模型与校验
  db.py      SQLite 初始化与任务存取
tests/
  test_tide.py  核心算法与 API 测试
Dockerfile
docker-compose.yml
requirements.txt      运行时依赖
requirements-dev.txt  含 pytest/httpx 的开发依赖
```
