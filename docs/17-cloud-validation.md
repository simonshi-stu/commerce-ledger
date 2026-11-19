# Commerce Ledger 云端修复与验证，2026-11-06

以下为 2026-11-06 云端候选包的历史验证记录，涉及的补丁与日志位于当日交付包，不是本仓库内的路径。当前阶段包含该 SQLite 修复；最新固定依赖复验与边界见 [第 19 章](19-stage-publication.md)。本次未合并或部署。

## 首选交付：已验证的 SQLite 修复

`sqlite_verified_fix/` 基于草稿 PR #1 的 `2a1bc4ccfd468a6b43ab0eed227c9f02137180b4`。本次重新读取远程树，基线全部 39 个文件的 Git blob 哈希一致。没有假定这是用户电脑未上传内容的最新版。

- `sqlite_verified_fix.patch` 只以这个 PR 提交为基线，不包含 PostgreSQL 适配。
- 最终 104 项通过、0 失败、0 跳过。原 67 项加 37 项新回归。
- 业务、旧库迁移、备份恢复、独立 uvicorn HTTP 烟雾检查通过。
- Python 编译检查通过。新并发与取消回滚测试额外重复 10 轮，每轮 2 项通过。
- 数据库版本仍为 v2，无需新数据库迁移。

源码来源：https://github.com/simonshi-stu/commerce-ledger/pull/1

## 实际修复

1. 超出 SQLite INTEGER / PostgreSQL BIGINT 范围的商品 ID 或分页 offset，原本进入驱动后返回 HTTP 500。现在在请求入口返回 422 / VALIDATION_ERROR，不打开业务事务。商品路径 ID 同时拒绝 0 和负数；最大正 64 位 ID 仍按正常 404 处理。
2. PostgreSQL 候选的 HTTP 演示原先继承 COMMERCE_DATABASE_URL，该配置优先于演示创建的临时 SQLite 路径。现在演示移除继承的 URL，避免误连其他数据库。独立进程测试给出不可用的本地伪 URL，确认仍完成临时 SQLite 演示且不写调用者配置的文件。该预防性隔离改动也保留在 SQLite 修复分支。
3. 增加 10 个并发请求以相反商品顺序争抢两个最后库存的回归，检查仅一单提交、两个明细及两个销售流水、无部分订单。
4. 增加取消第二项超出库存上限的回归，验证第一项返还及流水一并回滚；腾出容量后可重试成功，重复取消不重复返还。
5. 更新离线 OpenAPI 中 ID、offset 的上下界。

`pre-fix-reproduction.json` 和 `post-fix-reproduction.json` 对照记录四个实际请求从 500 改为 422。

## 另附：旧 PostgreSQL 候选的同步修复

`candidate/` 从之前保存在云端的 PostgreSQL 候选复制，并同步上述修复。`candidate.patch` 只适用于该旧候选，不能直接应用到 main 或 PR #1。

- 修复前候选复验：98 通过、45 跳过。
- 修复后：135 通过、81 跳过。新增 36 项共享契约也纳入真正 PostgreSQL 套件，但尚未运行。
- 当前没有 PostgreSQL 服务、postgres / psql / Docker 可执行文件或已配置的测试 URL。未安装引擎、未尝试外部数据库。
- 专项验收命令在无测试 URL 时实际返回 `missing_postgres_test_url`、退出码 2，仍然拒绝将跳过作为通过。
- 不声称 PostgreSQL 事务、触发器、死锁防护、迁移或恢复已经验收。

## 环境与可复现性限制

实际使用 Python 3.12.14、FastAPI 0.141.1、Pydantic 2.13.4、Starlette 1.6.0、HTTPX 0.28.1、pytest 9.2.0、uvicorn 0.52.1、psycopg 3.3.7。两个已知依赖弃用警告保留在日志中。

这不是 requirements.txt 中所有固定版本完全一致的环境复验，也没有更改锁定依赖来掩盖差异。测试只用合成数据与临时数据库，HTTP 服务仅绑定 127.0.0.1，完成后退出。

在相应代码目录中运行 `python -m pytest -q`。PostgreSQL 必须另行提供已获授权、可丢弃的测试数据库后运行 `python -I scripts/verify_postgres.py`。测试报告和当前环境清单位于本包根目录。
