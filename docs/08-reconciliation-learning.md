# 08 库存与订单核对：如何发现数据不一致

## 这次解决什么问题

事务和接口验证能防止正常业务产生大量错误，但手工修改数据库、错误的数据迁移、旧程序或恢复备份仍可能破坏业务不变量。

新接口 GET /integrity 只读检查两件事：

1. 每个商品的 available 是否等于该商品全部库存流水 delta 的合计；商品缺少 inventory 行也算异常。
2. 每个订单是否至少有一条明细，且 total_cents 是否等于 quantity × unit_price_cents 的明细合计。

healthy=true 仅表示以上检查在此次读取快照中通过。它不证明全部业务规则、真实仓库数量、支付金额或安全性都正确。流水若与库存同时被错误改成一致，单靠这个检查也无法识别。

## 第一步：运行正常示例

服务器启动后：

~~~powershell
$headers = @{"X-API-Key"="local-learning-key-change-me"}
Invoke-RestMethod -Uri http://127.0.0.1:8018/integrity -Headers $headers
~~~

新建空数据库、种子库，以及正常下单/取消/履约后的数据，都应健康。模拟闭环脚本现在最后也会核对：

~~~powershell
.\.venv\Scripts\python.exe -m scripts.demo
~~~

响应字段：

| 字段 | 含义 |
|---|---|
| healthy | 本次两类核对是否全部通过 |
| products_checked / orders_checked | 此快照内检查的商品/订单总数 |
| stock_mismatch_count / order_mismatch_count | 两类异常的完整数量 |
| stock_mismatches / order_mismatches | 按稳定主键排序返回的异常样本 |
| truncated | 任一类别的异常数量超过本次 limit |

limit 默认 20，最大 100；只限制每类异常返回明细数，不限制检查范围。即使只展示一个异常，计数仍反映完整异常数量。

接口需要与其他管理接口相同的 API Key。它会扫描并聚合业务表，属于手动诊断接口，不适合当作高频健康探针。/health 仍是轻量连通性检查。

## 第二步：理解只读快照

读 app/db.py 的 read_snapshot：

- 打开专用连接。
- PRAGMA query_only=ON 在这个连接上禁止写入。
- BEGIN 建立读事务；第一条查询确定此次快照。
- 读取多个统计和异常列表，最后结束事务并关闭连接。

为何不直接分几次普通查询？假设先读库存为 5，另一个请求补货到 7 并写了 +2 流水，随后你读到新流水和 7。把旧库存与新流水比较就会误报。读事务确保本次所有查询看到同一个已提交版本。

当前 SQLite 使用 WAL，读者可以保持旧快照，写者在另一连接正常提交。它不代表读到了“此刻最新值”：返回的是一致的历史快照。

## 第三步：看 SQL 如何聚合

app/service.py 的 check_integrity 使用 LEFT JOIN 保留缺少库存账户或缺少明细的父记录。SUM 没有输入行时返回 NULL，用 COALESCE(...,0) 转成 0，再通过 HAVING 比较聚合结果。

注意这里有两类缺失：
- 没有库存流水且库存为 0：合法的新商品。
- 没有库存账户：商品创建流程不完整，必须报错。
- 没有订单明细：完整提交的业务订单不应出现，必须报错。

ORDER BY 保证异常样本稳定；LIMIT 参数通过绑定传入。SQL 的字符串组合只拼接代码中定义的固定查询片段，不拼用户字段。

## 第四步：只在测试临时库中制造异常

不要为了学习去改真实业务库。运行：

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q -k integrity
.\.venv\Scripts\python.exe -m pytest -q -k read_snapshot
~~~

阅读 tests/test_backend.py 中：
- test_integrity_is_healthy_after_business_flow：正常下单与取消后两边一致。
- test_integrity_detects_corruption_and_bounds_details：仅在临时库中改库存、删库存行、改订单总额，观察检测和返回上限。
- test_read_snapshot_does_not_mix_committed_versions：在快照读取中让独立写连接提交补货，当前快照仍看见旧版本，退出后新请求看见新版本。
- test_read_snapshot_connection_refuses_writes：验证诊断连接不能执行 UPDATE。

接口发现问题后不会把库存强行改成流水和，也不会重算并覆盖历史订单金额。究竟哪边正确，需要结合业务原因、操作记录与备份判断；自动覆盖可能掩盖第二个错误。

## 第五步：数据库迁移时保留什么

本阶段没有改表结构，user_version 仍为 1；API 版本升到 0.3.0。不要把 API 版本与数据库版本混为一谈。

迁到 PostgreSQL 的待实施方案：
1. 定义明确连接/事务适配边界，保留业务服务、幂等键与条件扣减规则。
2. 用目标引擎迁移工具记录初始 DDL 与之后的升级，禁止直接执行 SQLite PRAGMA 或误把本地 .db 迁过去。
3. 普通下单使用目标数据库写事务；唯一键冲突后明确回滚并重试整个业务事务。
4. 核对查询需使用目标引擎显式只读、REPEATABLE READ 事务。不能假定 PostgreSQL 默认 READ COMMITTED 的多条 SELECT 自动共享同一快照；其语义见 [PostgreSQL 官方事务隔离文档](https://www.postgresql.org/docs/current/transaction-iso.html)。
5. 在目标数据库里重新验证订单竞争、取消竞争、失败回滚和快照核对，再报告实际通过结果。

上述是迁移设计，不是已经实现的 PostgreSQL 适配。本次没有重试受拒绝的 Docker 引擎、修改权限或运行 PostgreSQL。准确阻塞见 07-stage-delivery.md。

## 面试追问

“已有事务为什么还要核对？”事务保证按已写逻辑执行的原子性，不能证明业务逻辑永远正确，也不能防止库外修改或错误迁移。

“核对为什么不用单独每次 SELECT 的最新结果？”因为多个时间点的最新值拼起来，可能不是任何一个真实存在过的业务状态。

“为什么不直接自动修复？”异常只是发现两边不同，尚不能判断哪边是正确账本。
