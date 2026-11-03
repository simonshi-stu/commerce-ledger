# 06 代码为什么这样写

本章按你自己从零实现的顺序讲解。建议打开对应文件，一次只读一个函数。

## 1. 先写数据库，不先写按钮

app/schema.sql 中 products 用独立 id 作主键，sku 唯一；inventory.product_id 同时作主键和外键，表示每个商品只有一个库存账户。

order_items 用 (order_id,product_id) 联合主键，这是“同一单里同一种商品只有一行”的规则。orders 保存业务状态，明细保存下单价格快照。stock_movements 记录为什么库存发生变化；流水合计应与可售库存一致。

金额使用整数分，数量要求整数。SQLite 类型比较宽松，所以关键 CHECK 同时检查 typeof(...)=integer；HTTP 验证不能替代数据库约束。

## 2. 再写连接与事务

app/db.py 的 connection 是上下文管理器：进入时建立连接，退出时一定 close。每个操作独立连接，避免多线程共享同一个 sqlite3 connection。

transaction 在连接内执行 BEGIN IMMEDIATE，yield 把连接交给业务函数；业务正常结束就 commit，出现异常就 rollback 再向上抛。业务函数只要在 with db.transaction() as c 中操作，就有统一失败恢复。

initialize 按 MIGRATIONS 顺序执行未应用版本，当前数据库版本为 2。新文件可初始化，已有旧库在服务启动时拒绝静默升级；先用 app.migrate --apply 生成一致性备份并升级。全部待应用 DDL 与版本更新在同一脚本事务中，失败 rollback；更高未知版本拒绝覆盖。详见第 11 章。

## 3. 把外部输入变成可靠的内部对象

app/schemas.py 用 Pydantic 定义 ProductCreate、Adjustment 和 OrderCreate。Field(strict=True) 防止把 true、字符串或小数自动当成整数数量。范围限制防止空订单、超长字符串和不合理数量。

extra=forbid 拒绝未声明字段，例如调用者发送 total_cents。金额由服务器计算，客户端只能提交商品和数量。

OrderCreate.unique_products 在字段校验后检查重复商品。先合并再下单是调用者的责任，后端不悄悄改业务含义。

## 4. 实现最普通的商品操作

app/service.py 的 create_product 在一个事务里插 products 和 inventory。不能先提交商品再建库存，否则中途失败可能产生“没有库存行的商品”。

update_product 的 data=None 表示下架；SQL 不删除商品，历史 order_items 的外键仍可找到它。价格修改只改变当前目录，不改订单快照。

所有来自用户的 SQL 值通过 ? 和参数元组绑定；不要用字符串拼接把名称塞入 SQL。

## 5. 下单：读 place_order

按代码顺序复述：

1. 对明细按 product_id 排序，生成摘要。同一商品列表顺序变了仍视为相同请求。
2. 进入写事务，查询 request_key。已有且摘要相同则返回原单；摘要不同则 409。
3. 创建订单占位，初始总额 0，所有后续操作仍在同一未提交事务内。
4. 遍历每个商品，确认存在且没有下架。
5. 条件扣减 available，并检查 rowcount。失败就抛 INSUFFICIENT_STOCK。
6. 保存 quantity 和 unit_price_cents 快照，写负库存流水。
7. 累计总价并更新 orders，最后退出上下文提交。

为何先写订单占位？order_items 需要真实订单外键。为何不会留下 0 金额空单？后面任何步骤异常都会回滚占位；成功时会写实际总价。

代码的 for 循环不代表每个商品分别提交。提交发生在循环外的事务管理器。

## 6. 取消：读 transition_order

先校验当前状态。若目标终态已经达到，直接返回；若已经进入另一个终态，返回冲突；只有 PLACED 可以转出。

取消时逐条明细返还库存、写 CANCEL 流水、修改订单状态，全部共用同一个事务。并发取消靠数据库写事务串行判断，不能靠 Python 全局变量或浏览器按钮禁用。

FULFILLED 是模拟手工确认履约，不调用支付。它不再扣库存，因为 PLACED 已经减少可售量。

## 7. 最后才连接 HTTP

app/main.py 的 create_app 接收数据库路径和 API Key，所以测试能注入独立临时库。lifespan 在启动时初始化数据库。

路由负责接收参数和调用业务；异常处理器把 BusinessError 转成 HTTP 状态码与结构化 JSON。输入错误不返回整份原请求；数据库约束失败不回传原始 SQL。数据库忙时返回 Retry-After，提示调用方使用原幂等键重试。

同步数据库调用使用普通 def。先理解同步事务的正确性，再考虑异步数据库，不要把 async 当作防超卖机制。

## 8. 用失败场景证明，而不是靠注释宣称

tests/test_backend.py 为每次测试创建独立临时文件数据库。测试不仅检查 HTTP 200，还检查状态、价格快照、最终库存、订单行数与流水。

重点看 test_multi_item_failure_rolls_back_everything 和 test_twenty_connections_compete_for_last_unit。前者制造中途失败，后者让多个独立连接在屏障后一起竞争。两者分别验证原子性和竞争时的库存不变量。

完成本章后，试着不看代码写下下单伪代码，再用测试验证自己的改动。所有实验都放新临时库，不接真实商家数据。

