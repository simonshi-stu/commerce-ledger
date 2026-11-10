# 18：订单流水语义核对与恢复门禁

2026-11-08 云端阶段提交，未合并或部署。发布前复验见 [第 19 章](19-stage-publication.md)。沿用 11 月 6 日已验证 SQLite 修复，新增 49 项回归；当前 SQLite 完整套件 153 项通过。全部数据为合成数据。

## 为什么总数相等还不够

旧 `/integrity` 核对两个等式：当前库存等于库存流水总和，订单总额等于明细金额总和。这两个等式成立时，SALE 流水仍可能关联到不存在的订单，或取消返还记录缺失。数据库原有唯一键、外键和不可改写历史的触发器不覆盖这些跨表语义。

可复现场景：正常下单后，测试模拟错误导入，追加一条关联不存在订单的 SALE -1，再追加 ADJUST +1。当前库存和流水净额不变，订单金额也正确。没有关闭触发器、删除约束或修改历史。旧核对返回 `healthy: true`，旧备份发布成功；修复后核对返回 `healthy: false`，备份验证抛出 `VerificationError`，没有发布目标文件。

这证明诊断及恢复门禁存在盲区，不证明正常 HTTP 下单已经产生坏账，也不新增任何导入或修复 API。

## 新规则

- 每个订单明细，无论 PLACED、FULFILLED 或 CANCELLED，都应有且仅有一条相同订单、相同商品的 SALE，delta 等于负的 quantity。唯一性由现有复合唯一键保证。
- CANCELLED 明细应有匹配的 CANCEL，delta 等于正的 quantity。
- PLACED 和 FULFILLED 不应出现 CANCEL。
- SALE/CANCEL 必须匹配一个应有的明细及状态，不能是孤立引用或引用订单内不存在的商品。
- ADJUST 是独立库存操作，没有订单关联要求，仍纳入库存总和核对。

代码先用 `WITH expected` 计算应有流水，再比较缺失/错误数量和多余流水。查询只读，与原库存及金额核对使用同一个快照，不自动改账。

## HTTP 结果

`GET /integrity` 在原字段上增加：

- `ledger_mismatch_count`：不匹配的订单-商品-流水类型记录数，不是异常订单数。
- `ledger_mismatches`：明细，每项包含 order_id、product_id、kind、expected_delta、actual_delta。
- actual_delta 为 null 表示缺少应有流水；expected_delta 为 null 表示多余或孤立流水。二者都有值表示数量/符号错误。
- 每类异常明细独立受 limit 限制，计数保留全量。`truncated` 覆盖三类异常；按订单、商品、类型稳定排序。
- `healthy` 只有库存、金额、流水三类异常均为零时才为 true。只读凭据可查询。

这是返回结构的增量扩展。严格校验响应额外字段的客户端需要同步新契约。离线 docs/openapi.json 已更新。数据库仍为 v2，没有新增表、索引或迁移。

## 恢复行为

`verify_database` 及 backup/restore 原本就复用 `check_integrity`，因此会拒绝这些新识别的异常；无需重复另一套规则。先验证私有暂存文件，通过后才发布，失败清理暂存目录，不修改源库、不覆盖任何现有目标。

新规则也意味着某些过去能通过的旧文件现在会被拒绝。应先调查异常来源，不要为了让验证通过而自动改写不可变历史。

## 验证

- 新增 11 类等式仍成立的损坏样例：缺失 SALE，SALE 符号/数量错误，缺失 CANCEL，CANCEL 符号/数量错误，PLACED/FULFILLED 却出现 CANCEL，孤立 SALE/CANCEL，错误商品引用。
- 每类覆盖诊断及 SQLite verify/backup/restore。另覆盖 CLI 脱敏失败、上限/排序/只读权限、正常多商品三种状态及幂等重试、OpenAPI。
- 并发快照测试在异常计数后让另一个事务提交新的异常。当前报告的计数与明细仍一致，下一次报告才看到变化。
- 49 项新增测试在原代码上全部失败，其中 44 项直接验证旧诊断和恢复的错误接受；其余覆盖新增契约/边界。修复后全通过。保留修复前 XML 和日志，不把“新增字段不存在”误称成 49 个独立漏洞。
- 使用 requirements.txt 全部固定版本，Python 3.12.14、SQLite 3.53.1。逐包版本一致，pip check 通过。保留 1 项 Starlette/httpx 依赖弃用警告。

## 边界

本修复增强只读诊断，不阻止持有直接数据库写权限者插入不一致历史，不证明数据库未被管理员篡改。没有生产负载基准，limit 只限制返回详情，计数仍扫描全量历史。迁移、备份和恢复仍保持原来的人工流程和已说明限制。

单独交付包中的 PostgreSQL 候选已同步同一 SQL 和 15 项共享真实引擎回归，但本次没有可用的 postgres/psql/Docker 可执行文件或测试 URL。候选 184 项非引擎测试通过、96 项真实 PostgreSQL 测试跳过。验收门禁实际返回 missing_postgres_test_url、退出码 2；不能把 SQLite 或跳过当作 PostgreSQL 事务/并发/SQL 验收。
