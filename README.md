# Commerce Ledger：商品、库存与订单管理后端（学习原型）

这是新建的独立项目，适合学习关系数据库和后端业务。按用户确认使用 Python/FastAPI，SQLite 为第一阶段本地数据库；业务规则、SQL 设计和测试场景可迁移至 PostgreSQL。没有连接真实店铺、支付、客户数据或原有项目。

参考酒店项目的“资源管理→占用资源→完成业务→报表”思路，独立编写代码，没有复制参考代码、图片、SQL 数据或报告。演示商品和库存全部为**模拟数据**。金额统一为人民币分。

**已验证：67 项测试通过；服务层与 HTTP 层各验证 20 人争抢最后一件，新增可验证备份与恢复演练。** [基础验证记录](docs/05-verification.md) · [基线发布复验](docs/12-publication-verification.md) · [恢复阶段验证](docs/14-recovery-verification.md)

项目仓库：[simonshi-stu/commerce-ledger](https://github.com/simonshi-stu/commerce-ledger)。

## 架构：一次请求经过什么

~~~mermaid
flowchart LR
    U[Swagger 或 HTTP 客户端] --> A[FastAPI 路由与 API Key]
    A --> V[Pydantic 输入验证]
    V --> S[业务服务：下单/取消/履约]
    S --> T[独立连接与整单事务]
    T --> D[(SQLite 关系数据库)]
    D --> P[商品与可售库存]
    D --> O[订单与价格快照]
    D --> M[不可经 API 修改的库存流水]
~~~

对应代码顺序：app/main.py → app/schemas.py → app/service.py → app/db.py → app/schema.sql。下单逻辑集中在 place_order，取消与履约集中在 transition_order；先沿这条路径读，不必从头背所有代码。

## 数据关系与状态

~~~mermaid
erDiagram
    PRODUCTS ||--|| INVENTORY : has
    PRODUCTS ||--o{ ORDER_ITEMS : included
    ORDERS ||--|{ ORDER_ITEMS : contains
    PRODUCTS ||--o{ STOCK_MOVEMENTS : audited
~~~

~~~mermaid
stateDiagram-v2
    [*] --> PLACED: 原子扣可售库存并创建订单
    PLACED --> CANCELLED: 返还库存一次
    PLACED --> FULFILLED: 模拟确认履约
    CANCELLED --> [*]
    FULFILLED --> [*]
~~~

多商品必须全部有货才提交；同一幂等键重试不重复扣库存；取消只从 PLACED 转出。[事务细节与接口表](docs/02-design.md) · [跟着写请求与面试问答](docs/03-learning.md)

## 从哪里开始

1. 阅读 [参考项目拆解](docs/01-reference-review.md)，先理解原项目的真实架构。
2. 阅读 [数据库与业务设计](docs/02-design.md)，把酒店业务映射到商品与订单。
3. 按 [分步骤学习](docs/03-learning.md) 跑一个闭环，再逐个理解函数。
4. 看 [数据来源与后续阶段](docs/04-data-and-roadmap.md)，区分已实现和计划。
5. 按 [从零编写顺序读代码](docs/06-code-walkthrough.md)，理解每层为什么这样写；[离线 OpenAPI](docs/openapi.json) 便于检查接口定义。
6. 阅读 [阶段交付与准确限制](docs/07-stage-delivery.md)，查看 Git 阶段边界、待上传状态与 PostgreSQL 阻塞。
7. 跟着 [只读核对与快照教程](docs/08-reconciliation-learning.md) 检查库存流水和订单金额，理解为什么多个查询需要一致快照。
8. 阅读 [凭据与权限边界](docs/09-security-boundaries.md)，再沿 [下单请求代码地图](docs/10-order-code-map.md) 追踪路由、验证、事务与测试。
9. 阅读 [版本迁移与最小本地运行](docs/11-migrations-and-local-running.md)，真实演示旧库升级、备份、失败回滚与数据库历史约束。
10. 跟着 [备份与恢复教程](docs/13-backup-and-recovery.md)，验证 WAL 快照、恢复后的订单/库存/幂等和拒绝覆盖。
11. 使用 [完整面试讲解与自测](docs/15-interview-guide.md)，按业务、架构、数据库、并发、取舍、证据和局限逐步准备，并如实说明 AI 辅助开发。

## 本地运行（Windows PowerShell，Python 3.10+）

在本项目目录打开终端。首次克隆或换机器时执行：

~~~powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
~~~

如果 py 不可用，把它换成实际 python.exe 路径。本次验证使用 Python 3.10.4；运行时请使用本机解释器或项目 .venv 中的 python.exe。

无需激活环境，不受 PowerShell 脚本执行策略影响：

~~~powershell
.\.venv\Scripts\python.exe -m app.migrate --apply
.\.venv\Scripts\python.exe -m app.seed
$env:COMMERCE_API_KEY = "local-learning-key-change-me"
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8018 --no-access-log
~~~

默认数据库：data/commerce.db。可通过 COMMERCE_DB 设置独立文件路径。种子脚本可重复执行，不补回已卖出的库存，也不修改已有 SKU。服务不自动灌入种子。

浏览器访问 http://127.0.0.1:8018/docs，点击 Authorize 填写 API Key，后续请求自动携带 X-API-Key。当前为本地单商家教学密钥，没有多用户权限隔离或真实支付。

## 一条命令看完整演示

~~~powershell
.\.venv\Scripts\python.exe -m scripts.demo
~~~

演示使用独立临时数据库，模拟购买最后一件礼盒、第二次购买被拒绝、取消后库存返还，不影响 data/commerce.db。

## 自动测试

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q
~~~

测试使用 pytest 临时文件数据库，覆盖 API、数据库约束、事务回滚、20 个并发连接争抢最后一件、重复下单和并发取消。测试结果和范围见 [验证记录](docs/05-verification.md)。

## 目录与职责

| 位置 | 职责 |
|---|---|
| app/main.py | HTTP 路由、鉴权、错误映射、生命周期 |
| app/schemas.py | 输入类型、范围、重复商品等验证 |
| app/service.py | 下单、取消、库存调整与事务边界 |
| app/db.py | 独立连接、外键开启、BEGIN IMMEDIATE、提交/回滚 |
| app/schema.sql | 5 张表、外键、唯一键、CHECK、索引 |
| app/seed.py | 5 个合成商品与可重复种子 |
| app/recovery.py | 只读验证、备份和恢复到新文件的 CLI |
| tests/test_backend.py | 业务/HTTP/并发测试 |
| tests/test_recovery.py | WAL、恢复语义、异常和拒绝覆盖测试 |
| scripts/demo.py | 临时数据库端到端演示 |
| docs/ | 中文拆解、教学与证据 |

本仓库提供教学源码与合成测试；没有部署、付费服务或修改原四项目。SQLite 的正确性演示不能表述为 MySQL/PostgreSQL 性能或生产并发能力。

数据库版本 1 由 app/schema.sql 创建，版本 2 在 app/migrations/002_history_guards.sql 增加历史保护触发器。已有旧库必须先停止服务并执行 python -m app.migrate --apply，命令会备份后升级；未知更高版本拒绝启动。详见第 11 章。

## 独立验证真实 HTTP

~~~powershell
.\.venv\Scripts\python.exe -m scripts.smoke_http
~~~

该脚本使用随机本地端口和临时数据库，启动 uvicorn，验证健康检查、Swagger、OpenAPI、商品写入/读取和鉴权，结束后关闭进程。


## 数据一致性核对（自 API 0.3.0）

GET /integrity 在只读事务里核对库存与流水、订单总额与明细合计。异常计数完整，明细限量返回；不自动覆盖账目，不代表全部业务正确。详见第 08 章教程。当前仍是 SQLite 本地实现，PostgreSQL 未实现或实测。

## 权限与故障边界（API 0.4.0）

COMMERCE_API_KEY 为必填管理凭据；可选 COMMERCE_READ_API_KEY 只能读取，写操作返回 403。两个值不能相同，没有默认有效凭据；这不是账号密码、多租户或完整 RBAC。每个响应带服务器生成的 X-Request-ID；应用故障日志不记录请求体、凭据、查询串或异常原文。

新增故障测试覆盖提交前整单回滚，以及提交后响应失败时原幂等键重试不重复扣库存。源码发布目标为用户自己的 commerce-ledger 仓库，PostgreSQL 未实施或实测。教学与安全边界见第 09、10 章。

## 真实旧库升级演示（API 0.5.0 / DB v2）

运行 python -m scripts.migration_demo，在临时旧库验证 v1 到 v2 升级、数据保留、备份可读、终态保护和重复执行。数据库现在拒绝改写历史明细与库存流水；正常取消追加返还流水。当前没有 PostgreSQL 实现或实测；本次源码发布不包含运行数据库。

## 可验证备份与恢复演练（第二阶段 / DB v2）

运行 `python -m scripts.recovery_demo`，在临时合成库完成备份、原库继续变化、恢复到新文件、幂等重试和拒绝覆盖演练。手动操作使用 `python -m app.recovery backup|verify|restore`；参数、失败排查与面试讲解见 [第 13 章](docs/13-backup-and-recovery.md)。备份通过结构、外键和业务核对后才发布；没有生产灾备、PostgreSQL 或 Docker 实测。
