# 07 阶段交付与第二阶段质量加固

## 阶段 1：最小闭环已完成

本地独立 Git 仓库：commerce-ledger/.git。初始提交：a905d7c，包含原创业务代码、五表 DDL、种子、22 项测试、真实 HTTP 演示和中文教学资料。

未包含 .venv、运行数据库、.env 或旧项目文件。提交作者为工具身份 Codex，不冒充用户。首次源码发布目标为用户自己的 [commerce-ledger](https://github.com/simonshi-stu/commerce-ledger)；不向参考项目仓库推送。

## 阶段 2：质量加固子阶段已完成

- 输入模型之外增加 ProductRead、MovementRead、OrderSummary 和 OrderRead 输出模型，OpenAPI 能展示真实响应字段，框架会验证服务输出。
- APIKeyHeader 声明鉴权方案，Swagger 可通过 Authorize 统一设置请求头；缺少密钥仍返回结构化 401。
- 新增 HTTP 层 20 人抢最后一件的验证，补充原有服务层独立连接验证。
- 验证取消与履约同时竞争时只产生一个终态；库存与胜出的状态一致。
- 验证缺货事务不消耗幂等键，补货后可用原键重试成功。
- 核查 OpenAPI 的鉴权声明、201 订单响应以及价格快照字段。

最终测试 26 passed，保留 1 条依赖弃用警告，详见 05-verification.md 和 test-results.xml。

这不代表整体项目或 PostgreSQL 阶段完成。当前仍使用直接 SQL 和 Python sqlite3，没有 SQLAlchemy/PostgreSQL 适配、生产权限系统或支付。

## 首次源码发布边界

开发阶段原始历史单独保留。发布副本保留五个阶段的顺序、功能及测试证据，仅清除环境路径和主机名；下方阶段提交号指向发布副本中的对应提交。

目标仓库由用户明确指定为 simonshi-stu/commerce-ledger。发布前核实该仓库为空且当前账户有写权限；只向明确的 main 分支执行常规推送，不覆盖其他远端工作，不强推。源码与合成测试可发布，运行数据库、虚拟环境、原始日志、私人备份和凭据不进入仓库。

最新独立验证及准确限制见 [发布前验证](12-publication-verification.md)。

本地检查命令：

~~~powershell
git status --short
git log --oneline -5
git remote -v
~~~

## PostgreSQL 的准确阻塞与恢复条件

已执行的只读动作：docker version --format '{{.Server.Version}}'。

目标：Windows Docker Engine 命名管道 //./pipe/docker_engine。

结果：系统返回 Access is denied，未读到服务版本。该输出带有“daemon may not be running”的泛化提示，不能据此断言 Docker Desktop 没启动。准确结论是此次访问被拒绝；没有启动容器，没有安装 PostgreSQL，未产生数据库测试结果。

本阶段没有再次连接该引擎，也没有切换账户、提高权限、改管道/用户组或换工具访问。

[Docker 官方 Windows 权限说明](https://docs.docker.com/desktop/setup/install/windows-permission-requirements/) 区分安装模式、启动 Docker Desktop 的用户和特权操作。下一步应由用户或管理员按该文档确认当前登录账户有正常使用 Docker Desktop/引擎的授权，或提供已经批准的本地 PostgreSQL 实例；确认访问条件改变后再做新的只读检查。无需把“管理员启动整个开发环境”当作默认修复。

之后才能完成 PostgreSQL 适配、版本迁移与真实引擎集成测试，并分别报告：连接成功、DDL 成功、普通业务用例、并发防超卖、幂等唯一键冲突、死锁/重试场景。SQLite 的 26 项结果不能替代这些证据。

## 给学习者的本阶段练习

打开 app/schemas.py 中 OrderCreate 与 OrderRead，解释为什么请求不接受 total_cents，而响应必须包含它；再在 Swagger 中观察两种 schema。

运行：

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q -k twenty_http
.\.venv\Scripts\python.exe -m pytest -q -k cancel_and_fulfill
.\.venv\Scripts\python.exe -m pytest -q -k restock
~~~

HTTP 并发测试使用进程内 ASGI TestClient，真实路由与鉴权会执行，但没有 20 条真实网络连接。另一个 smoke_http 脚本负责验证真实本地 TCP/uvicorn 启动。两种证据各有边界，不能混称为生产压测。

## 后续本地阶段

阶段 3 已以 caeccc3 保存只读核对与快照验证。阶段 4 增加两级共享凭据权限及故障边界，详见 09-security-boundaries.md 与 10-order-code-map.md。这些阶段不改变此前 PostgreSQL 未验证的结论；源码已整理为独立脱敏发布历史。
