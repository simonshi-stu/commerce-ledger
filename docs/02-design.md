# 02 数据库与业务设计

## 一个能讲清楚的业务问题

小商家维护商品，录入到货库存；接到订单时扣可售数量；未履约订单可取消并返还库存；履约后进入终态。商家需要追踪每次库存变化，避免两个人都买到最后一件。

与已有电商内容运营项目的联系是同一个商家场景，但这是独立交易后端，没有集成现有 Agent，也没有使用旧项目内部数据。当前单商家、单币种 CNY、单库存池，不收集姓名、电话、地址或客户 ID。

## ER 关系

~~~mermaid
erDiagram
    PRODUCTS ||--|| INVENTORY : has
    PRODUCTS ||--o{ ORDER_ITEMS : appears_in
    ORDERS ||--|{ ORDER_ITEMS : contains
    PRODUCTS ||--o{ STOCK_MOVEMENTS : records
~~~

| 表 | 主键/关键字段 | 数据库保证 |
|---|---|---|
| products | id，sku，name，price_cents，active | SKU 唯一，价格为正整数分，名称非空，active 为 0/1 |
| inventory | product_id，available | 外键和唯一商品库存行，可售量为非负整数且有上限 |
| orders | UUID id，request_key，request_hash，status，total_cents | 幂等键唯一，状态限定，金额非负 |
| order_items | (order_id,product_id)，quantity，unit_price_cents | 外键、同单商品唯一、正数量、正快照价 |
| stock_movements | id，product_id，delta，kind，reference，reason | 外键、变化非零、同商品/类型/业务引用不重复 |

DDL 在 app/schema.sql。inventory 与 products 的一对一存在性由商品创建事务同时插入两行保证；外键本身不能强制每个商品都有库存行。

订单明细保存下单时价格。以后商品改价不影响历史订单。total_cents 由服务器按明细计算，不信任客户端金额。跨行“订单金额等于明细之和”以及“库存等于流水和”由业务事务和测试保证，并非单表 CHECK 自动实现。

库存账只描述 available（可售量），不是仓库物理在库量：下单即减少可售量，履约不再扣一次；取消未履约单才返还。未来若加入仓储，需要单独建 on_hand、reserved 等字段，不可混用口径。

## 接口与语义

所有业务请求都需要 X-API-Key。完整参数和响应可在 /docs 查看。

| 方法/路径 | 行为 |
|---|---|
| POST /products | 新增商品，初始库存 0，201 |
| GET /products、/products/{id} | 分页列表/详情 |
| PUT /products/{id} | 修改名称和当前价格，SKU 不变 |
| DELETE /products/{id} | 软删除即下架，保留历史记录 |
| POST /products/{id}/stock-adjustments | 带 request_key 的正/负库存调整，必须写原因 |
| GET /products/{id}/movements | 分页查看库存流水 |
| POST /orders | 原子创建多商品订单，201 |
| GET /orders、/orders/{id} | 分页列表/明细 |
| POST /orders/{id}/cancel | PLACED→CANCELLED；重复取消直接返回当前单 |
| POST /orders/{id}/fulfill | PLACED→FULFILLED；模拟手工确认履约 |

POST 创建接口幂等重放仍返回 201 和原结果（库存调整返回原流水；下单返回订单当前状态）。这是本原型约定，不表示重放新建了资源。库存调整键作用域为单商品，订单键作用域为整个单商家系统。

取消和履约是状态动作，没有任意更新订单金额/删除订单接口，否则会破坏审计。商品下架不能再下新单，但已有单可取消或履约。

## 下单事务

~~~mermaid
sequenceDiagram
    participant C as 调用者
    participant A as API与验证
    participant S as 业务层
    participant D as SQLite
    C->>A: request_key + 商品数量
    A->>S: 合法输入
    S->>D: BEGIN IMMEDIATE
    S->>D: 查幂等键，比较请求摘要
    S->>D: 写订单并逐商品条件扣库存
    S->>D: 写价格快照与库存流水
    S->>D: 更新总价，COMMIT
    S-->>C: 订单详情
~~~

任一商品缺货或不存在，抛业务异常并 ROLLBACK；前面扣的库存、写的明细、订单和流水全部撤销。不允许“先扣 A 成功，B 失败却保留 A”。

防超卖核心语句：

~~~sql
UPDATE inventory
SET available = available - ?
WHERE product_id = ? AND available >= ?;
~~~

检查 rowcount=1 才能继续。SQLite BEGIN IMMEDIATE 在业务读之前取得写事务，数据库同一时刻只有一个写事务；多线程分别使用独立连接。WAL 允许读者与写者更好地共存，不意味着多个写者并行。等待上限 5 秒，忙时 API 返回 503/Retry-After，调用者带原幂等键重试。

幂等摘要按 product_id 排序后计算，只包含商品与数量，忽略客户端排列顺序。同键同参数直接返回原订单；同键不同参数返回 409；失败事务不消耗幂等键。下单重试遇到商品已改价/下架仍先返回已创建订单，避免重新扣库存。

取消在同一写事务里读状态、返库存、写 CANCEL 流水、改状态；重复或并发取消只能返还一次。取消与履约相互竞争时，先完成的终态生效，另一操作 409。

## 错误处理与边界

输入范围、严格整数、重复商品、未知字段：422；API Key 错误：401；资源不存在：404；库存不足/状态冲突/唯一约束：409；锁等待超时：503。返回结构为 error.code 与 message/details，不回传原始 SQL 或输入中的敏感字段。

所有 SQL 值均参数绑定。同步 sqlite3 路由采用普通 def，让框架在线程池执行。本原型没有连接池、异步 DB、JWT/RBAC、多租户、支付、退款、物流、定时过期或生产监控。不要把“模拟履约”当作真实支付成功。

## 面向关系数据库迁移

迁到 PostgreSQL/MySQL 时保留表语义、条件扣减和整单事务；替换连接/参数占位符、DDL 和迁移系统。加入行级锁或明确的更新顺序、唯一键冲突后的整事务重试、死锁处理，并在真实引擎跑并发测试。SQLite 测试不能证明这些引擎上的隔离级别行为。

