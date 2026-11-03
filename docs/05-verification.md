# 05 实际验证记录

日期：2026-11-03。执行环境：Windows、Python 3.10.4、SQLite 3.37.2。依赖精确版本记录在 requirements.txt（从此次隔离虚拟环境冻结）。

## 最终测试

~~~text
python -m pytest -q --basetemp=.test-tmp/stage4-01 --tb=short --junitxml=docs/test-results.xml
42 passed, 1 warning in 8.49s
~~~

机器可读报告：[test-results.xml](test-results.xml)。42 项包含参数化输入场景；不表示 42 个独立业务模块。

覆盖：商品 CRUD 与下架、API Key、缺失资源、SKU 唯一、参数绑定保留 SQL 样式普通文字、严格数值验证、数据库外键与 CHECK、初始迁移重复执行及未来版本拒绝、价格快照、库存入库幂等、同单重复商品拒绝、订单幂等与不同参数冲突、明细排序不改变幂等、缺货整单回滚、重复取消、履约终态限制、锁等待的 503 响应、种子重复运行。

并发测试使用 ThreadPoolExecutor、Barrier 和文件 SQLite，每次服务操作独立数据库连接：
- 20 个不同下单请求争抢 1 件库存：1 成功、19 个库存冲突；最终 1 张订单、库存 0、流水和 0。
- 10 个同键重试：相同订单号，库存只扣 1。
- 10 个并发取消：库存只返还 1 次。

这是单机事务正确性测试，不是压测，不支持生产吞吐量结论。尚未测试跨进程、进程崩溃、磁盘故障、长期高负载或目标 PostgreSQL 引擎。

## 闭环演示与真实服务

python -m scripts.demo 实际输出：

~~~text
1. Simulated order placed: [临时订单 UUID] CNY cents: 15900
2. Second purchase rejected: insufficient stock
3. Cancellation restored exactly one unit; demo complete
4. Read-only reconciliation passed: stock ledger and order totals match
5. Reader credential can inspect products but cannot place an order
~~~

python -m scripts.smoke_http 实际输出：

~~~text
HTTP smoke passed: health, docs, OpenAPI, CRUD, authentication, reconciliation, reader restrictions, request IDs
~~~

HTTP 验证确实启动本地 uvicorn 子进程，使用临时数据库并自动停止；不是仅根据源码推测。demo 与测试均未操作真实店铺。

## 环境问题与修复记录

初次依赖下载受到沙箱网络隔离，获准后安装到本项目 .venv，未修改全局 Python。普通沙箱下 Windows asyncio 在 socketpair 初始化处卡住；经 traceback 定位后获准在隔离外执行本地测试。随后默认 pytest 临时根目录不可读，改用本项目新建的 .test-tmp/final-03 后全部通过。没有删除或改写原有 pytest 临时目录。

运行中修正了 Python 3.10 不提供 sqlite3.SQLITE_BUSY 常量的问题，使用 SQLite 错误码并增加数据库繁忙响应测试。当前依赖产生 1 条 Starlette 关于 httpx 测试客户端的弃用警告，不影响当前测试结果；没有隐藏该警告。

只读检查找到 docker.exe，但读取 Docker 引擎时 Access is denied；未更改系统服务或安装 PostgreSQL。因此 PostgreSQL 的迁移、连接与并发测试均未执行，也没有宣称通过。

## 剩余限制

本地单商家管理原型，只有共享 API Key；无多用户/RBAC、支付退款、真实物流、多仓、自动超时取消、生产监控与目标引擎迁移。没有部署或远端发布。参考酒店项目仅作静态审查，没有运行复现其缺陷。公开 UCI 数据仅核查许可与字段，没有下载导入。当前数据全部合成。

技术资料：[SQLite 事务](https://www.sqlite.org/lang_transaction.html)、[FastAPI 测试](https://fastapi.tiangolo.com/tutorial/testing/)。后续应在选定数据库中重跑并发场景，再据实更新该记录。


## 第二阶段新增证据

HTTP 层 20 个 ASGI 请求争抢最后一件：1 个 201、19 个 INSUFFICIENT_STOCK/409；同时新增取消/履约竞争、缺货后原键重试、OpenAPI 鉴权与响应契约测试。这不是 20 条真实 TCP 连接或生产压测。阶段细节见 07-stage-delivery.md。

## 第三阶段新增证据

新增 4 项只读核对测试：正常业务一致、临时库异常检测与返回上限、独立写连接提交后保持读快照一致、诊断连接拒绝写入。全部 30 项通过。API 版本 0.3.0；数据库无结构变化。SQLite 实测不代表 PostgreSQL 实测。

## 第四阶段新增证据

新增 12 项测试（含 7 类写操作的权限矩阵），累计 42 passed、1 条未隐藏的依赖弃用警告。验证只读凭据权限、无默认管理密钥、管理/只读密钥不能相同、提交前异常回滚、提交后响应失败原键重试、应用日志和错误响应不泄露异常原文。当前 API 0.4.0，数据库结构版本仍为 1。

安全结果限于本地共享凭据、当前 JSON API 与应用自身错误日志；不是用户账号体系、生产安全认证或 PostgreSQL 测试。实际运行的 demo 和 HTTP smoke 也已包含 reader 拒绝写入与请求编号检查。
