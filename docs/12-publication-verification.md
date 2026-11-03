# 12 首次源码发布前验证

日期：2026-11-03。范围：API 0.5.0、SQLite DB v2 的独立源码发布副本。

## 实际执行

- pytest：50 passed、0 failed、0 skipped，1 条未隐藏的 Starlette/httpx 弃用警告，9.44 秒。
- scripts.demo：下单、缺货拒绝、取消返库、只读一致性核对及只读凭据拒绝写入通过。
- scripts.migration_demo：临时 v1 文件升级 v2，备份可读、订单与库存保留、历史终态约束及重复执行通过。
- scripts.smoke_http：真实启动并关闭本地随机端口 uvicorn，健康检查、Swagger/OpenAPI、商品写读、鉴权、核对接口、只读权限及请求编号通过。

各命令退出码均为 0。测试和演示使用隔离临时数据库，没有使用原项目运行数据库。复用已有隔离 Python 3.10.4 环境及 requirements.txt 所列依赖；不代表全新机器重新安装验收。

复现入口（在按 README 准备环境后执行）：

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m scripts.demo
.\.venv\Scripts\python.exe -m scripts.migration_demo
.\.venv\Scripts\python.exe -m scripts.smoke_http
~~~

## 发布内容与证据

五个阶段的应用、SQL、脚本与测试源码字节保持不变。发布历史中的文档去除本机解释器绝对路径和环境旁白，JUnit XML 去除 hostname 属性；原测试明细、数量、状态及计时时间保留。本次只增加源码发布说明及修正过期的远端状态文案。

原始历史和原始运行日志在本地私有位置保留，不属于公开仓库或源码 ZIP。任何后续 JUnit/日志更新都应先清除环境路径、主机名和凭据。

## 局限

这是 SQLite 本地单商家教学原型，不是 PostgreSQL 集成验收、生产性能压测或部署。没有多租户账号系统、真实支付、真实物流或客户数据；全部演示数据为合成数据。公开代码不等于已上线服务。依赖警告仍需在未来升级时验证兼容性，本次未以隐藏警告代替修复。
