# 09 本地凭据权限与安全边界

## 当前鉴权的真实性质

这不是用户账号系统。数据库没有用户名、密码或密码哈希表；没有 JWT、登录会话、多租户或按客户隔离订单。现在是两个可选用途不同的共享 API Key：

| 凭据 | 来源 | 权限 |
|---|---|---|
| manager | 必填 COMMERCE_API_KEY | 读取，以及新增/修改/下架商品、调整库存、下单、取消、履约 |
| reader | 可选 COMMERCE_READ_API_KEY | 读取本系统全部商品、订单、流水、核对结果；不能执行写操作 |
| 未提供/不匹配 | 不存在默认有效值 | 业务接口返回 401 |

reader 可以看全部管理数据，不代表某个客户的身份。如果未来引入客户、商家或不同店铺，必须另建身份和对象归属授权；不能只靠订单 UUID 难猜。

没有账号密码，因此“密码已哈希存储”的说法不成立。API Key 从进程环境或测试显式注入，保存在进程内存，不写业务库。不加载 .env 文件，没有硬编码的运行默认密钥。README 和脚本的固定字符串只用于明确的本地模拟；未配置管理密钥，应用拒绝启动。

## 第一步：启用只读凭据

启动服务前，在服务所在的 PowerShell 中：

~~~powershell
$env:COMMERCE_API_KEY = "local-learning-key-change-me"
$env:COMMERCE_READ_API_KEY = "local-learning-read-key-change-me"
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8018 --no-access-log
~~~

两个值必须不同；只读值可不设置。全部空白的配置被拒绝。示例值不是推荐的共享秘密；需要在自己的会话使用随机值时，可以赋值生成：

~~~powershell
$env:COMMERCE_API_KEY = (& .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))")
$env:COMMERCE_READ_API_KEY = (& .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))")
~~~

不要打印、提交或发给第三方。修改环境后需要重启服务，已启动进程不会自动读取新值。当前没有动态密钥撤销或在线轮换系统。

## 第二步：观察身份验证与权限判断的区别

在请求终端使用与服务一致的演示只读值：

~~~powershell
$reader = @{"X-API-Key"="local-learning-read-key-change-me"}
Invoke-RestMethod -Uri http://127.0.0.1:8018/products -Headers $reader
$body = @{sku="BLOCKED";name="blocked";price_cents=100} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8018/products -Headers $reader -ContentType application/json -Body $body
~~~

读取应成功；创建应返回 403。401 表示凭据无法识别，403 表示已识别但不允许这个操作。

代码路径：[KeyPolicy](../app/security.py) 用 compare_digest 比较凭据，authenticate 返回 manager/reader；authorize 根据请求方法拦截 reader 的写操作。这个依赖在整个 FastAPI 应用的业务路由上启用，因此新加写路由也默认受限。所有 GET 路由必须继续保持只读。

Swagger/OpenAPI 是公开的接口说明页面，不含实际业务查询结果或有效密钥。点击 Authorize 填入只读密钥不会获得写权限，提交仍被后端拦截。

## 第三步：理解请求编号与脱敏日志

每个请求产生新的服务器请求编号，响应头为 X-Request-ID。不会信任并回显调用者提交的同名值，避免用它向日志注入任意内容。

未知异常返回 INTERNAL_ERROR；非繁忙数据库运行错误返回 DATABASE_ERROR。响应只含稳定错误码、通用提示和请求编号。应用的故障日志仅记录：

~~~text
request_failure request_id=... method=POST route=/orders error_type=RuntimeError
~~~

路由为模板，例如 /orders/{order_id}，不是用户的原始路径；不记录请求体、查询串、API Key、异常原文或 traceback。这是应用自己的日志策略，不能自动约束代理、外部平台、浏览器或另外配置的日志采集器。

启动命令加 --no-access-log，避免 uvicorn 默认访问日志收集完整 URL。不要把密钥放在查询参数里。本地诊断若需要更详细日志，应在可信临时数据环境按需增加，不能直接把秘密或完整业务数据写日志。

当前异常边界针对现有 JSON 请求/响应路径；没有验证流式响应、WebSocket、后台任务异常或进程崩溃。

参考：[OWASP 日志中应排除的数据](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)、[OWASP REST 安全](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html)。

## 第四步：验证两种故障边界

“收到 500”不一定表示数据库没有提交，所以重试必须带原幂等键。

| 故障位置 | 数据库结果 | 原键重试 |
|---|---|---|
| 事务内部、提交前 | 回滚订单、明细、扣库存和流水 | 重新执行一次业务 |
| 提交成功、响应模型生成失败 | 订单已存在，库存已扣一次 | 找到原订单，不再扣库存 |

测试通过 monkeypatch 注入故障，没有新增可从 HTTP 触发的故障接口。

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q -k reader
.\.venv\Scripts\python.exe -m pytest -q -k failure
.\.venv\Scripts\python.exe -m pytest -q -k redacted
~~~

完整测试为 42 项，含七类写接口的只读权限矩阵、配置拒绝、异常日志不含故障原文、事务提交前失败、提交后响应失败重试等。实际结果见 05-verification.md。

## 尚未解决的安全工程问题

没有账号密码、租户级隔离、限流、请求体总字节上限、TLS 终止、完整安全事件审计、秘密管理服务或生产安全评估。当前仅绑定 127.0.0.1，用合成数据本地教学；这些结果不能表述为生产安全认证。Docker 被拒问题未重试，PostgreSQL 未实现或验证。
