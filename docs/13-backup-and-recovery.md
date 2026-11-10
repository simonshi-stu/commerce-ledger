# 13：可验证备份与恢复到新文件

本阶段补上 SQLite 恢复演练。HTTP API 仍为 0.5.0，数据库仍为 v2，没有修改订单规则或已有运行库。PostgreSQL 尚未实现或实测。

## 1. 先运行不影响自己的数据库的演示

在仓库根目录，使用已安装 requirements.txt 的 Python 环境：

~~~powershell
.\.venv\Scripts\python.exe -m scripts.recovery_demo
~~~

过程：模拟商品 5 件、单价 2990 分 → 下单 2 件、剩 3 件 → 备份 → 原库取消、库存变回 5 → 将备份恢复到新文件 → 恢复文件仍有原订单和 3 件库存 → 同一幂等键重试不再次扣货 → 再次写入同一恢复路径被拒绝。

输出以 `Recovery demo passed:` 开头。脚本使用临时合成库并清理文件，不读取 data/commerce.db，不启动后台服务。备份之后的取消不在恢复文件中，恢复前必须明确要回到哪个时间点。

## 2. 备份自己的本地教学库

先按 README 初始化、灌入合成数据。以下路径相对于仓库根目录：

~~~powershell
New-Item -ItemType Directory -Force backups | Out-Null
.\.venv\Scripts\python.exe -m app.recovery backup --source data/commerce.db --output backups/commerce-before-practice.db
$LASTEXITCODE
~~~

成功退出码为 0，JSON 包含 `ok: true`、`operation: backup`、`verified: true`、版本和已核对的商品/订单数量。输出必须是新路径；重复执行同一路径会失败，不会覆盖。需要另一份备份时显式换名。

源库必须存在且正好为当前 DB v2。命令不创建缺失源库、不自动初始化或迁移、不修复异常账目。旧库按照第 11 章先停服升级；迁移产生的旧版本备份仍保留其原版本，不能直接作为本章 v2 的恢复输入。

实现使用 SQLite backup API 复制已提交状态，不能等同于仅复制 `.db` 主文件。测试保持 WAL 连接及未提交写事务，确认包含已提交变更、排除未提交变更。[SQLite Backup API](https://www.sqlite.org/backup.html) · [Python Connection.backup](https://docs.python.org/3.10/library/sqlite3.html#sqlite3.Connection.backup)

## 3. 验证并恢复到新文件

~~~powershell
.\.venv\Scripts\python.exe -m app.recovery verify --database backups/commerce-before-practice.db
.\.venv\Scripts\python.exe -m app.recovery restore --source backups/commerce-before-practice.db --output backups/commerce-rehearsal.db
.\.venv\Scripts\python.exe -m app.recovery verify --database backups/commerce-rehearsal.db
~~~

backup 和 restore 共用安全复制逻辑，区别在操作意图：恢复时输入备份、输出独立演练库。没有原地覆盖、自动切换服务或合并新旧数据。

验证分四层：版本号匹配；`PRAGMA integrity_check` 检查结构/索引/部分约束；单独执行 `PRAGMA foreign_key_check`；复用 `check_integrity` 核对库存与流水、订单总额与明细合计。结构检查不能代替外键或业务核对。[SQLite PRAGMA 文档](https://www.sqlite.org/pragma.html#pragma_integrity_check)

verify 以只读模式连接，不创建缺失文件、不写业务数据。对活动源库直接 verify 时，结构检查和业务检查使用两个快照，所以它只是一项诊断；备份/恢复在私有暂存库上验证，没有其他写入者，通过后才发布。

## 4. 用恢复文件启动独立服务

先停止原服务并保留原数据库，再在当前终端启动演练服务：

~~~powershell
$env:COMMERCE_DB = "backups/commerce-rehearsal.db"
$env:COMMERCE_API_KEY = "local-learning-key-change-me"
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8018 --workers 1 --no-access-log
~~~

在 `/docs` 查看商品、订单和 `/integrity`。停止服务后执行 `Remove-Item Env:COMMERCE_DB`，下次启动恢复默认路径。演示密钥仅供本地学习，自己的凭据不写入代码或提交。

## 5. 读代码并解释失败行为

沿 app/recovery.py 阅读：snapshot_database → 同目录私有暂存子目录 → SQLite backup → 关闭数据库句柄 → verify_database → publish_new_file → 清理暂存目录。

暂存与目标位于同一文件系统。Windows 使用拒绝覆盖的 `os.rename`；POSIX 使用 `os.link`，因为 POSIX rename 会覆盖已有文件。即使其他进程抢先创建目标，最终发布也失败、已有文件保持不变。POSIX 文件系统需支持硬链接，否则安全失败，无覆盖回退；本阶段只在 Windows 实测。[Python 文件操作语义](https://docs.python.org/3.10/library/os.html#os.rename)

复制默认有 10 秒期限，繁忙时可失败后重试；验证时间不包含在复制期限内。常规异常清理暂存目录，进程被强制终止可能留下 `.commerce-snapshot-*`，确认没有任务使用后再手动处理。

CLI 失败退出码为 1，只输出错误类型，避免回显数据库原文和本机路径：

| 错误类型 | 检查方向 |
|---|---|
| FileExistsError | 输出必须是新路径，不能覆盖现有文件/目录 |
| FileNotFoundError | 输出父目录是否存在 |
| OperationalError / DatabaseError | 源文件是否存在、有效且可读，数据库是否被锁定 |
| VerificationError | 版本、外键、业务账目；检查原因，不自动改账 |
| TimeoutError | 减少持续写入后，用新路径重试 |
| PermissionError | 本机目录/文件系统权限，不绕过权限限制 |

## 面试讲解与边界

“事务保证一次下单全部提交或全部回滚；备份处理文件丢失或回到过去状态。我用 SQLite backup API 处理 WAL，先验证暂存快照，再发布到新路径。恢复后检查价格快照、库存和幂等键；备份后的业务不会自动回来，因此不能覆盖正在使用的库。”

对应 tests/test_recovery.py：WAL 提交边界、恢复后幂等与历史保护、目标竞争时拒绝覆盖、损坏/错版本/外键/业务异常拒绝发布、超时清理、缺失源不创建。

这是本地恢复演练，没有调度、保留策略、加密、异地副本、签名、时间点恢复、自动故障切换或 RPO/RTO 实测。检查不覆盖全部业务语义，不证明触发器或自定义架构没有被管理员篡改。只处理自己可信的教学数据库。backups/ 默认被 Git 忽略，运行库、备份和密钥不属于源码交付；没有生产灾备、PostgreSQL 或 Docker 实测。
