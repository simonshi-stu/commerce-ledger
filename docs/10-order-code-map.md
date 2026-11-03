# 10 一个下单例子穿透整套代码

目标：用模拟杯子单价 2990 分、数量 2 创建总额 5980 分的订单；重复同一个请求不重复扣库存。你要能沿调用链解释每一步，而不是背框架名。

## 代码地图

~~~mermaid
flowchart TD
    C[POST /orders：商品与数量] --> M[main.safe_request_boundary：生成请求编号]
    M --> A[security.KeyPolicy：识别 manager 并允许写入]
    A --> V[schemas.OrderCreate：类型/范围/重复项验证]
    V --> R[main.order_create：HTTP 入口]
    R --> S[service.place_order：业务规则]
    S --> T[db.transaction：BEGIN IMMEDIATE]
    T --> I[查幂等键与摘要]
    I --> Q[条件扣库存并检查 rowcount]
    Q --> W[写订单明细与库存流水，计算总价]
    W --> K[COMMIT 或异常 ROLLBACK]
    K --> O[OrderRead 输出验证]
    O --> C2[JSON 响应与 X-Request-ID]
~~~

图中表示必须经过的职责，不承诺框架内部解析 JSON 与依赖求值的精确调度顺序。授权和输入验证都必须通过，才能执行业务服务。

## 1. 构造真实请求

先按 README 启动服务并灌入模拟种子。在另一个使用相同演示管理密钥的 PowerShell：

~~~powershell
$headers = @{"X-API-Key"="local-learning-key-change-me"}
$products = Invoke-RestMethod -Uri http://127.0.0.1:8018/products -Headers $headers
$p = $products | Where-Object sku -eq "DEMO-MUG-001"
$requestKey = "learn-" + [guid]::NewGuid().ToString("N")
$payload = @{request_key=$requestKey;items=@(@{product_id=$p.id;quantity=2})} | ConvertTo-Json -Depth 4
$o = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8018/orders -Headers $headers -ContentType application/json -Body $payload
$o
$again = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8018/orders -Headers $headers -ContentType application/json -Body $payload
$again.id -eq $o.id
~~~

商品必须有至少 2 件可售库存，否则正确结果是 409。种子重复执行不会补库存。如果此前未改过模拟杯子的价格，总额应为 5980；如果你改过价格，则以服务器当前价格为准，不能硬凑示例值。

最后一行应为 True。保持同一个 payload 重试，不能为每次网络重试新造 request_key。新的独立购买才需要新键。

## 2. 路由层为什么很薄

打开 [app/main.py](../app/main.py)，找 order_create。它只把已验证的 OrderCreate 对象交给 service.place_order。路由不拼 SQL，不自己扣库存，也不信任客户端提供总价。

授权失败时不会执行 place_order。全局依赖使用 [app/security.py](../app/security.py) 的 KeyPolicy；只读凭据不能下单。

请求模型在 [app/schemas.py](../app/schemas.py)。request_key 有格式限制；items 非空且最多 50 行；商品 ID 为正整数；数量 1 到 10000；同商品不能重复出现；未知 total_cents 字段被拒绝。

## 3. 业务服务先处理“是不是重复业务”

打开 [app/service.py](../app/service.py) 的 place_order：

1. 先按 product_id 排序，得到稳定的明细排列。
2. 对商品与数量计算 SHA-256 摘要。这个摘要用于判定重试参数是否相同，不是密码哈希或身份验证。
3. 进入数据库事务后查 request_key。存在且摘要相同就返回原单；摘要不同返回 IDEMPOTENCY_CONFLICT。

为什么必须在事务里查？要让并发重试与后续写入处于明确的一致性边界。SQLite BEGIN IMMEDIATE 串行化写者；迁移 PostgreSQL 时必须重新实现并测试唯一键冲突后的事务恢复，不能原样套用假设。

## 4. 事务里发生的 SQL

[app/db.py](../app/db.py) 的 transaction 为整张订单建立一个边界。概念顺序：

~~~sql
BEGIN IMMEDIATE;
-- 查 request_key，未存在才继续。
-- 插入订单头（未提交的临时总额为 0）。
UPDATE inventory
SET available = available - 2
WHERE product_id = ? AND available >= 2;
-- 检查影响行数为 1。
-- 插入 quantity=2、unit_price_cents=2990 的明细。
-- 插入 delta=-2 的 SALE 流水，引用订单 ID。
-- 将订单总额更新为 5980。
COMMIT;
~~~

这是便于解释的伪 SQL，实际代码使用参数绑定，订单 ID 和价格来自服务端。多商品时循环写入，但 commit 在整个循环结束后才发生。

任何一行缺货、商品不存在或数据库异常都会向外抛出。事务上下文捕获后 rollback，再继续抛出，最后由 HTTP 层映射错误。不能在业务函数里吞掉异常后返回“成功”。

## 5. 提交以后也可能失败

place_order 返回字典后，OrderRead 输出模型检查返回结构。数据库可能已提交，但响应阶段出错或网络中断，客户端看不到成功。

因此“失败就再买一次”是错误理解。携带原键重试，服务会找到原单。请求编号用来定位一次 HTTP 尝试；幂等键标识同一次业务，两者用途不同。

## 6. 哪些测试证明哪些结论

| 测试名（tests/test_backend.py） | 要证明的行为 |
|---|---|
| test_complete_business_flow_and_snapshot | 数量 2 总额 5980，改价不改变历史明细 |
| test_order_idempotency_and_conflict | 相同请求复用原单，不同参数同键冲突 |
| test_multi_item_failure_rolls_back_everything | A 扣减后 B 缺货，整单恢复 |
| test_twenty_connections_compete_for_last_unit | 服务层 20 个独立连接只能卖出 1 件 |
| test_twenty_http_buyers_compete_for_last_unit | 经真实 ASGI 路由和鉴权，20 个请求只能 1 单成功 |
| test_unexpected_failure_rolls_back_before_commit_and_logs_no_secret | 提交前未知异常回滚，响应和应用日志不泄露异常原文 |
| test_response_failure_after_commit_retry_does_not_charge_twice | 提交后响应失败，原键重试不再次扣库存 |

ASGI 测试不是 20 条真实 TCP 连接。scripts/smoke_http.py 另外验证真实 uvicorn/本地 HTTP 启动；这些都不是生产压测或 PostgreSQL 证明。

## 7. 你应该能自己回答

- 为什么一个商品一张库存表，却允许很多库存流水？
- 为什么订单总额由服务器计算，历史明细保存价格？
- 为什么先查库存大于 0 还不够，要条件 UPDATE 和检查 rowcount？
- 为什么事务与幂等不能互相替代？
- 为什么请求编号每次不同，而重试幂等键必须不变？
- 为什么输出模型报错时，不能直接假定库存没扣？

完成这条链后，再读取消、履约和核对接口；它们使用相同的分层和事务原则。
