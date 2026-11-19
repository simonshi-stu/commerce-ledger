# 第二阶段验证记录：可验证备份与恢复

验证日期：2026-11-03。基线 main：659374c430a546b593413f93bcb2e9747317169e；核实远程与本地一致后在 feat/verified-recovery 分支增量开发。原仓与原有运行数据库未修改。

## 本次实际结果

| 验收 | 实际结果 |
|---|---|
| 完整 pytest | 67 passed，0 failed，0 skipped，11.00 秒 |
| 原有回归 | 50 项保留通过，包括服务/HTTP 并发抢库存 |
| 新增恢复测试 | 17 项通过 |
| scripts.demo | 下单、缺货、取消、对账、只读权限通过 |
| scripts.migration_demo | v1→v2、备份、数据保留、终态约束通过 |
| scripts.recovery_demo | 快照、恢复、幂等重试、拒绝覆盖通过 |
| scripts.smoke_http | 真实 localhost HTTP、Swagger/OpenAPI、鉴权、核对通过，服务已退出 |

完整测试命令：

~~~powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B -m scripts.demo
.\.venv\Scripts\python.exe -B -m scripts.migration_demo
.\.venv\Scripts\python.exe -B -m scripts.recovery_demo
.\.venv\Scripts\python.exe -B -m scripts.smoke_http
~~~

实际使用 Windows、Python 3.10.4 和已有依赖环境。受限执行环境中 Python asyncio socketpair 停在本地 socket.accept，完整 HTTP 测试未进入业务代码；终止该次运行后，经权限流程在允许本地通信的环境运行上表测试并通过。临时库位于获准写入的临时目录，未使用自己的运行库。没有修改系统网络策略或 Docker 权限。

有 1 项既有依赖弃用警告：Starlette 提示当前 httpx TestClient 适配将弃用。此次保留已锁定依赖并报告警告，没有未经验证直接升级。Python 3.10 已结束官方支持；可重复的当前验证环境不能当成生产推荐配置。

## 新增测试的证明范围

tests/test_recovery.py 实测：

- 备份前的订单、价格和库存恢复，备份后的取消不出现；重试原幂等键不重复扣库存，历史触发器仍生效。
- 打开 WAL 并保持未提交写入，快照包含已提交名称而排除未提交名称。
- 缺失源文件不被创建；现有目标与源文件本身不能被覆盖；另一进程抢先创建目标也不会被覆盖。
- 库存合计异常、订单金额异常、外键异常、版本 0/1/3、损坏文件均不会发布最终输出。
- 只读连接拒绝写入/初始化；备份超时清理暂存；CLI 成功和失败退出码符合约定，错误不回显路径。

这些是正确性案例，不能外推为负载性能、故障切换可靠性或生产灾备保证。只验证了 Windows 文件发布路径；POSIX 实现未在本阶段实测。测试没有新增数据库版本、接口或真实业务数据。

## PostgreSQL 环境结论

本轮未发现 psql 客户端、标准 PostgreSQL 安装目录、postgres 进程或本地 5432 监听。系统服务管理器查询返回权限不足，因此不能宣称整台电脑绝对没有 PostgreSQL。没有现成且明确获准的可连接测试实例；先前 Docker 引擎权限拒绝没有重试或绕过。

因此选择交付 SQLite 恢复能力。PostgreSQL 适配、行锁/隔离级别差异、连接池、SQL 方言/迁移及独立真实实例验收仍是下一阶段，不能写成已完成。
