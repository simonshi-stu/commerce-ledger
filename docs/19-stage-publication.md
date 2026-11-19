# 19：2026-11-08 阶段提交与发布前复验

本阶段继续原草稿 [PR #1](https://github.com/simonshi-stu/commerce-ledger/pull/1) 的 `feat/verified-recovery` 分支，不创建平行实现，不合并 main，也不部署。代码仅包含 SQLite 修复；PostgreSQL 候选未纳入本分支，真实 PostgreSQL 验收尚未完成。

## 提交范围与基线

- 本阶段起点为 PR #1 的 `2a1bc4ccfd468a6b43ab0eed227c9f02137180b4`；核对时 main 为 `659374c430a546b593413f93bcb2e9747317169e`。
- 完整远程树共 39 个文件，与补丁基线逐文件 Git blob 哈希一致。
- 使用 11/08 语义核对候选包，包内 129 个文件全部通过 SHA-256 清单核对。累计补丁从上述 PR head 干净应用，结果与 SQLite 候选源码逐字节一致。
- 同时纳入 11/06 的整数范围验证、HTTP 演示隔离、整单回滚及并发回归；新增订单与 SALE/CANCEL 流水核对及备份/恢复拒绝异常文件的门禁。
- 发布准备只调整候选中的说明文字，区分历史验证和当前状态，删除与本仓库无关的交付备注；业务源码与已核对候选一致。
- 数据库仍为 v2，没有新增迁移。`/integrity` 新增的响应字段见 [第 18 章](18-ledger-semantics.md)。严格校验额外字段的客户端应同步契约。

## 本阶段重新执行的检查

在云端 Linux、Python 3.12.14、SQLite 3.53.1 中执行：

- `requirements.txt` 中 25 个固定包版本逐项相符；`pip check` 通过。
- 全量 `python -m pytest -q`：153 passed，0 failed，0 skipped。
- 快照一致性、多商品相反顺序争抢及取消溢出回滚共 3 项测试，额外重复 10 轮，全部通过。
- `python -m compileall -q app scripts tests` 通过。
- 业务、v1 → v2 迁移、备份恢复及独立 uvicorn HTTP 烟雾检查通过。HTTP 只绑定 localhost，完成后进程退出。
- 扫描未发现真实凭据、私钥、运行数据库、备份或环境文件。测试中的 `unused:unused@127.0.0.1:1` 是明确标注的不可用合成 URL，用于证明 HTTP 演示不会继承外部数据库配置。

保留 1 项 Starlette/httpx 依赖弃用警告；不为消除警告改动固定依赖。

## 尚未覆盖

当前没有 PostgreSQL 测试 URL，且没有 postgres、psql 或 Docker 可执行文件。SQLite 测试成功不代表 PostgreSQL 的真实 SQL、行锁、并发、迁移或恢复通过。

本次未读取或覆盖其他机器上未上传的修改，未强制推送，未合并、未部署，未接触真实店铺、支付或客户数据。诊断不是防篡改控制，未完成生产负载或性能验收。推送是否成功及远程 CI 状态应以 GitHub 上该提交和检查记录为准，不能由本文件替代。
