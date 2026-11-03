# Commerce Ledger：商品、库存与订单管理后端（学习原型）

这是新建的独立项目，适合学习关系数据库和后端业务。按用户确认使用 Python/FastAPI，SQLite 为第一阶段本地数据库；业务规则、SQL 设计和测试场景可迁移至 PostgreSQL。没有连接真实店铺、支付、客户数据或原有项目。

参考酒店项目的“资源管理→占用资源→完成业务→报表”思路，独立编写代码，没有复制参考代码、图片、SQL 数据或报告。演示商品和库存全部为**模拟数据**。金额统一为人民币分。

**已验证：26 项测试通过；服务层与 HTTP 层各验证 20 人争抢最后一件。** [验证记录](docs/05-verification.md)

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

## 本地运行（Windows PowerShell，Python 3.10+）

在本项目目录打开终端。这里已创建 .venv；换机器时执行：

~~~powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
~~~

如果 py 不可用，把它换成实际 python.exe 路径。本次验证使用 Python 3.10.4；运行时请使用本机解释器或项目 .venv 中的 python.exe。

无需激活环境，不受 PowerShell 脚本执行策略影响：

~~~powershell
.\.venv\Scripts\python.exe -m app.seed
$env:COMMERCE_API_KEY = "local-learning-key-change-me"
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8018
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
| tests/test_backend.py | 业务/HTTP/并发测试 |
| scripts/demo.py | 临时数据库端到端演示 |
| docs/ | 中文拆解、教学与证据 |

当前没有部署、创建远端仓库、付费服务或修改原四项目。SQLite 的正确性演示不能表述为 MySQL/PostgreSQL 性能或生产并发能力。

数据库初始迁移使用 app/schema.sql，并由 PRAGMA user_version=1 标记版本；重复启动不重置数据，遇到更高版本拒绝启动。当前只有初始版本，没有跨版本升级脚本。移到 PostgreSQL 时需要另加目标数据库迁移与真实引擎测试。

## 独立验证真实 HTTP

~~~powershell
.\.venv\Scripts\python.exe -m scripts.smoke_http
~~~

该脚本使用随机本地端口和临时数据库，启动 uvicorn，验证健康检查、Swagger、OpenAPI、商品写入/读取和鉴权，结束后关闭进程。

