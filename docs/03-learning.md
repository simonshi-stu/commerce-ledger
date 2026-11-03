# 03 一步一步学习与操作

不要一口气背完。每一步能复述“输入是什么、写哪张表、失败怎么恢复”，再进入下一步。

## 第一步：运行现成的最小闭环

按 README 安装依赖后运行：

~~~powershell
.\.venv\Scripts\python.exe -m scripts.demo
~~~

预期看到：创建模拟订单→第二次购买缺货→取消返还库存。这个演示用临时库，可反复执行。阅读 scripts/demo.py，从请求字段追到 app/main.py 的对应路由。

自测问题：下单扣的是物理库存还是可售库存？为什么履约不再扣一次？正确回答：本原型只管理可售量，已下单部分已从可售量扣除。

## 第二步：创建自己的商品

启动服务器后，在另一个 PowerShell 窗口执行：

~~~powershell
$headers = @{"X-API-Key"="local-learning-key-change-me"}
$body = @{sku="LEARN-CUP-001";name="learning cup";price_cents=2990} | ConvertTo-Json
$p = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8018/products -Headers $headers -ContentType application/json -Body $body
$p
~~~

记住 $p.id。初始库存是 0。重复创建同一 SKU 返回 409，这是数据库唯一键在发挥作用。重复练习请换新 SKU，不要删库。

读 app/schemas.py 的 ProductCreate、app/service.py 的 create_product 和 app/schema.sql 的 products/inventory。观察“应用验证”和“数据库约束”是两道防线。

练习：传入 price_cents=0、小数、true、未知字段，解释为什么应收到 422。

## 第三步：入库并留下原因

~~~powershell
$body = @{delta=3;request_key="learn-stock-0001";reason="synthetic learning stock"} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8018/products/$($p.id)/stock-adjustments" -Headers $headers -ContentType application/json -Body $body
Invoke-RestMethod -Uri "http://127.0.0.1:8018/products/$($p.id)" -Headers $headers
~~~

重复同一请求只产生一次入库。如果用相同 request_key 改成 delta=4，应返回 409。读 adjust_stock：先检查幂等，再改库存，再写流水，三者都在一个事务中。

练习：为什么不能只用前端按钮禁用防重复？答：网络重试、多客户端和进程崩溃都不受前端约束。

## 第四步：下单与取消

~~~powershell
$body = @{request_key="learn-order-0001";items=@(@{product_id=$p.id;quantity=2})} | ConvertTo-Json -Depth 4
$o = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8018/orders -Headers $headers -ContentType application/json -Body $body
$o
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8018/orders/$($o.id)/cancel" -Headers $headers
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8018/orders/$($o.id)/cancel" -Headers $headers
~~~

取消两次后库存仍应是 3。读 place_order→db.transaction→transition_order，逐条指出 SQL 属于哪一步。

练习：把商品价格改为 3990，再看历史订单 unit_price_cents；它应该保留 2990。客户端提交 total_cents 会被拒绝。

## 第五步：亲眼验证回滚与并发

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q -k multi_item
.\.venv\Scripts\python.exe -m pytest -q -k twenty_connections
.\.venv\Scripts\python.exe -m pytest -q -k concurrent
~~~

这些测试用独立临时数据库，不影响演示数据。打开 tests/test_backend.py：

- multi_item：A 有货，B 没货，失败后 A 库存、订单表、明细表、流水必须恢复。
- twenty_connections：20 个独立连接同时抢 1 件，恰好 1 单成功、19 单 409。
- concurrent_retries：10 个同幂等键请求只得到一个订单号。
- concurrent_cancel：10 个取消请求只返还一次库存。

这不是吞吐量测试，不可据此声称支持某个 QPS。它验证的是竞争条件下业务不变量。

## 第六步：面试讲解提纲

先用 60 秒解释问题：模拟商家需要在多商品下单和重试时保持订单、库存、流水一致。

再画 products—inventory、orders—order_items 和流水关系，最后展示一次缺货回滚与并发测试。

| 面试问题 | 回答主线 |
|---|---|
| 为什么拆订单与明细？ | 一单多商品，主表存状态/总价，明细保存数量和价格快照 |
| 为什么金额不用 float？ | 整数分避免二进制小数误差；当前仅支持 CNY |
| 如何防超卖？ | 条件扣减+检查影响行数+整单事务；SQLite 还会串行化写事务 |
| 有事务为什么还要幂等？ | 事务保证一次执行完整，幂等解决同一次业务重复提交 |
| 取消为什么不会返两次？ | 写事务内状态判断与返还，同商品/类型/业务引用唯一 |
| 原酒店项目有哪些可改进？ | 用源码具体指出跨房逐次提交、未明确 rollback、查后写竞争 |
| SQLite 能否证明生产并发？ | 不能；需目标引擎集成测试、锁/死锁/重试和监控 |
| 最大限制是什么？ | 当前为单商家本地教学原型，没有真实支付和用户权限体系 |

简历可用的诚实表述（理解并能演示后再使用）：

“独立设计商品、库存与订单管理后端学习原型，建立关系约束、价格快照与库存流水；以条件扣减、整单事务和请求幂等处理下单/取消，并用并发与故障场景验证防超卖和原子回滚。”

不要写真实商户数量、线上收益、生产 QPS、真实支付或使用真实业务数据；这些当前都没有证据。

