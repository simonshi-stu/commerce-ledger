# 11 已有数据库怎么升级：版本、备份与历史约束

## 为什么这一阶段属于核心功能

项目不能靠删库重建来升级。当前 API 0.5.0 支持数据库版本 2：
- 版本 1：app/schema.sql，五表、外键、唯一键、CHECK 与索引。
- 版本 2：app/migrations/002_history_guards.sql，新增六个历史/状态保护触发器，没有改写现有业务数据。

版本顺序在 app/db.py 的 MIGRATIONS 中声明，数据库用 PRAGMA user_version 记录已应用版本。API 版本与数据库版本分别演进，不能混为一谈。

## 第一步：只检查，不改变数据库

~~~powershell
.\.venv\Scripts\python.exe -m app.migrate --database data/commerce.db
~~~

返回 exists、version、supported。路径不存在时只报告不存在，不创建数据库。检查通过只读连接完成，不启动 HTTP 服务，也不需要 API Key。

不要把“能检查版本”当作“业务数据一定完整”。数据核对另用 GET /integrity。

## 第二步：停止写入，再备份和升级

先在服务终端 Ctrl+C，确保没有其他进程继续写这个文件，然后执行：

~~~powershell
.\.venv\Scripts\python.exe -m app.migrate --database data/commerce.db --apply
.\.venv\Scripts\python.exe -m app.migrate --database data/commerce.db
~~~

行为：
- 全新文件：在一个事务内应用版本 1 和 2。
- 版本 1 文件：先通过 SQLite backup API 生成同目录唯一备份文件，再在事务内应用版本 2。
- 已是版本 2：不重复应用、不再生成备份。
- 版本高于 2：拒绝覆盖或降级。

备份名类似 commerce.db.v1-UTC时间-随机串.bak。项目 data 下这些运行数据和备份均被 Git 忽略，不进入源码包。

备份是一份一致性快照；若迁移期间仍有其他写者，备份不能包含快照之后的新写入，所以本原型明确要求停服务再升级。没有实现维护模式或自动阻止外部进程。

不要在服务运行时仅复制 .db 主文件来替代一致性备份，尤其不要忽略 WAL。本章阶段没有实现自动降级或自动恢复；后续已增加[第 13 章的手动可验证备份与恢复演练](13-backup-and-recovery.md)，仍没有自动调度或故障切换。恢复旧备份前需要保留当前状态并评估备份之后的业务，不应直接覆盖正在运行的库。[SQLite 官方备份说明](https://www.sqlite.org/backup.html)

## 第三步：理解失败为什么能恢复

db.initialize 把待应用的 SQL、BEGIN IMMEDIATE、版本号更新与 COMMIT 放在同一个脚本中执行；错误时显式 rollback。版本号只有全部成功后才更新。

这里有一个 Python 细节：executescript 会先提交已有挂起事务，所以不能只在外面 BEGIN 后以为整个脚本自然被包住。代码将事务控制写进脚本，测试通过“先建一张表、再执行错误 SQL”验证前面的 DDL 也被撤销。[Python sqlite3 事务说明](https://docs.python.org/3.10/library/sqlite3.html)

服务和种子命令遇到已有旧版文件时会拒绝静默升级，提示先执行 app.migrate --apply。新文件可以自动初始化；显式迁移命令负责已有数据升级前的备份。

## 第四步：数据库现在保护什么

| 触发器 | 禁止行为 |
|---|---|
| order_status_transition_guard | 终态改回 PLACED，或 CANCELLED/FULFILLED 互相转换 |
| order_items_update_guard | 修改已有订单明细，包括数量和价格快照 |
| order_items_delete_guard | 删除已有订单明细 |
| terminal_order_items_insert_guard | 向已取消/已履约订单追加新明细 |
| stock_movements_update_guard | 改写既有库存流水 |
| stock_movements_delete_guard | 删除既有库存流水 |

正常商品改价修改 products，不会改历史 order_items。取消通过新增 CANCEL 流水返还库存，不会删除 SALE 流水，所以正常业务继续可用。

这些约束不是数据库管理员也无法篡改的证据系统。拥有文件和架构修改权限的人仍能移除触发器；库存余额或订单总额等其他异常由核对接口辅助发现。当前没有用触发器完整实现跨表金额守恒，服务事务与核对测试仍然重要。

## 第五步：亲眼验证，不动你的业务库

~~~powershell
.\.venv\Scripts\python.exe -m scripts.migration_demo
.\.venv\Scripts\python.exe -m pytest -q -k migration
.\.venv\Scripts\python.exe -m pytest -q -k history
~~~

演示在临时目录真实创建旧版文件：一个模拟杯子入库 5 件、下单 2 件，再升级。预期结果：

~~~text
Migration demo passed: v1 -> v2; backup readable; order/stock preserved; terminal guard enforced; repeat safe
~~~

重点测试位置在 tests/test_backend.py：
- make_legacy_v1_database：直接用版本 1 SQL 创建旧库，不能调用新版初始化伪装旧库。
- test_migration_v1_to_v2_preserves_existing_business_data：升级前后比较完整订单、库存和流水。
- test_failed_migration_rolls_back_ddl_and_keeps_old_version：错误 SQL 后 DDL 和版本号回滚。
- test_migration_command_backs_up_old_data_and_is_repeatable：旧备份可读，重复升级不再备份。
- test_startup_requires_explicit_migration_for_existing_old_database：已有旧库启动被拒，数据保持。
- test_database_guards_terminal_state_and_order_history：直接 SQL 操作也不能绕过终态与历史约束。

累计实际通过 50 项测试；不是新增了 50 个模块。

## 最小本地运行方式

完成升级后，再按 README 配置管理密钥，可选只读密钥，启动：

~~~powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8018 --workers 1 --no-access-log
~~~

当前交付只验证本机 JSON API、单 SQLite 文件和合成数据。没有执行互联网部署。真实部署还需要受支持的 Python 版本、TLS、反向代理、密钥管理、权限隔离、监控与目标引擎测试。本次实际可用并测试的是 Python 3.10.4；Python 官方已标记 3.10 文档版本不再受支持，不能据此推荐旧解释器用于新生产部署。

## 当前阶段边界与下一步

本地 MVP 的业务闭环、权限边界、一致性核对、升级路径和教程均已具备可重复证据。继续推进的最小外部条件是：
1. 明确用户自己的 GitHub 仓库后上传阶段提交，不向参考仓库推送。
2. 用户提供正常授权的 PostgreSQL/Docker 环境后再做目标引擎适配和真实并发验证。此前 Docker 引擎访问拒绝未重试或绕过。
3. 与用户按第 10 章逐步演练下单代码，确认能亲自讲解，再决定下一项业务扩展。

没有这些新条件时，不继续为增加功能数量堆新模块。仍不能将本地 MVP 称为生产级系统或 PostgreSQL 已验证项目。
