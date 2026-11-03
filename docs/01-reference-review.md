# 01 参考项目源码拆解

## 核查范围与证据

检查日期：2026-11-03。参考仓库：[ranxi2001/Hotel-information-management-system](https://github.com/ranxi2001/Hotel-information-management-system)。

固定版本：8b8712aa12c12a1f8084610619210887875caa25。以下判断来自该版本 Main.py、hotelManagement.sql、requirements.txt、LICENSE、README 和递归文件树的静态审查。没有运行参考程序，也没有导入其中的人名、电话或身份证样式数据。不能把静态发现当作运行验证。

- [Main.py 固定源码](https://github.com/ranxi2001/Hotel-information-management-system/blob/8b8712aa12c12a1f8084610619210887875caa25/Main.py)
- [数据库 DDL](https://github.com/ranxi2001/Hotel-information-management-system/blob/8b8712aa12c12a1f8084610619210887875caa25/hotelManagement.sql)
- [依赖](https://github.com/ranxi2001/Hotel-information-management-system/blob/8b8712aa12c12a1f8084610619210887875caa25/requirements.txt)
- [MIT 许可](https://github.com/ranxi2001/Hotel-information-management-system/blob/8b8712aa12c12a1f8084610619210887875caa25/LICENSE)

仓库的 102 个树条目中未见 AGENTS.md、.agents/技能说明或 pytest 等业务自动测试套件。references 下有 QtDialogtest.py、QtWidgetest.py 等界面实验，不能由文件名推断为事务/并发测试。未套用其他项目的说明。此次仅读取已有电商项目 README 来理解场景，没有修改其源码。

## 它实际是什么架构

PyQt5 桌面界面 → Python 对象（Staff、Room、Chart）→ PyMySQL → MySQL/InnoDB。

不是浏览器前后端分离，也没有发现 HTTP API 或 REST 路由。GUI 事件处理方法充当入口；不要在面试里把它说成 Spring、Vue 或微服务。

Main.py 同时包含数据库访问、业务判断、窗口代码。ui/ 提供部分生成界面，pictures/ 放界面素材，references/ 放实验资料。matplotlib 做图表，xlwt 导出 Excel，报表窗口调用 mysqldump 导出 SQL。README 提到 Python 3.9/MySQL 8.0.29；当前 requirements 的新依赖不应直接假定与 3.9 全部兼容，实际运行需另验证。

## 9 张表怎么关联

| 表 | 含义与关键约束 |
|---|---|
| staff | 员工/账号；sid 主键，susername 唯一，保存角色 |
| client | 个人客人；cid 主键，register_sid 外键到员工 |
| team | 团队；tid 主键，check_in_sid 外键到员工 |
| room | 房源；rid 主键，类型、楼层、价格、描述、图片 |
| booking_client | 个人预订；(cid,rid) 联合主键，关联客人和房间 |
| booking_team | 团队预订；(tid,rid) 联合主键 |
| checkin_client | 个人入住；(rid,cid) 联合主键，关联员工 |
| checkin_team | 团队入住；(rid,tid) 联合主键，关联员工 |
| hotelorder | 已结账记录；(id,start_time,end_time,rid,ordertype) 联合主键；房间和登记员工外键 |

hotelorder.id 同时表示个人或团队身份，不是一个独立订单编号；没有同时连向 client/team 的外键。预订联合主键没有日期，同一客人和同一房间不能同时保存多个日期段的预订。

SQL 还定义 Customers、Rooms、Living、Administrators 四个视图。两个 AFTER INSERT 触发器在个人/团队入住后增加住宿次数。团队每插入一个房间会加一次，因此多房团队的“次数”可能表达房间记录次数，而非一次旅行。

价格、年龄和金额大量使用 varchar。优点是课程项目直观，缺点是数据库不能直接保证数值语义。时间先后、正价格和同房跨表时间段互斥没有完整 CHECK/唯一约束。

## 从点击到数据库发生什么

1. 登录：LoginPage.display → Staff.userLogin（98 行），读取 staff 并在 Python 比较账号密码，保存全局 staff。
2. 查房：RoomOp.findRoom → Room.showRoom（225 行）。先筛选类型、楼层、价格，再逐房查询四张预订/入住表判断时间交叉。可理解为“过滤→排除占用”，但会产生随房间数增长的多次查询。
3. 直接入住：singleCheckinDB（307 行）检查占用，必要时登记客人，根据房价×天数计费，写 checkin_client，commit；触发器累计次数。
4. 预约：reserveCDB/reserveTDB（467/505 行）检查冲突，插入预订表。团队通过多个房间号循环操作。
5. 预约转入住：reserveToCheckinC/T（402/433 行），查询当天预订，写入住记录、删预订。
6. 退房：checkoutDB（575 行），将入住信息写入 hotelorder 后删入住表；这让房间重新可用。
7. 报表：Chart（621 行起）读取营业额、入住率、客户和员工统计；展示和导出。

## 事务与错误处理：值得学什么、要补什么

源码确实使用了 commit，不能说“完全没有事务”。但下列边界需要改善：

- 团队预约在循环内 commit（538 行）；团队转入住（461 行）和团队退房（614 行）也逐房提交。第一个房间成功、后一个失败时，前面不能整批撤销。
- 多处 catch Exception 后只打印/返回 False，没有明确 rollback。操作失败后的连接和未提交写入需要管理。
- 查占用和插入分开执行，没有看到房源锁/排他约束保证跨四表时间段互斥。两个客户端可能都查到空闲再插入；单个主键无法阻止不同客人占同房。
- reserveCDB 在 471 行的 SQL 字符串两段条件之间缺少布尔连接词，且使用 today 而非传入预约起日进行部分冲突判断；属于静态可见风险，未运行复现。
- modifyRoom 在 299 行把输入拼接成 SQL；其他多处已用参数绑定，不能笼统说所有 SQL 都有注入。
- 登录直接比较 staff.spassword，源码与演示 SQL 中密码为明文；角色判断多在界面层，不适合直接作为开放后端的权限设计。
- GUI 与业务异常混合，部分方法读取结果 [0] 时没有空值保护，用户看到的失败原因未必准确。

## 如何独立借鉴

借鉴的是关系建模、资源占用、完整业务流、报表与约束意识。新项目把房间映射为 SKU、占用映射为扣可售库存、退房/取消映射为履约/返还；使用独立业务语义和原创实现。

参考仓库 MIT 允许在保留许可条件下使用代码，但本项目没有复用其代码、资产或数据。仓库里看似个人信息的种子数据不导入新项目。

