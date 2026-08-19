---
name: smoke-all
description: /smoke-all — 对当前 Flutter 项目的全部可用端（Web + Android + iOS）跑全量冒烟验收，执行段三端并行（子代理），修复集中去重：构建安装包、并行执行、自愈、缺陷修复闭环、出合并报告。用户输入该命令即已明确选择冒烟验收模式，无需再确认场景。
---

# /smoke-all — 三端并行冒烟

用户已明确下单：**冒烟验收模式，所有可用端，全自动**。
不要再问"是想预览还是验收"（flutter-smoke-auto 第 0 步的询问跳过），直接执行。

主流程与所有纪律遵循主 skill `flutter-smoke-auto`（references 按需读）。
本命令只定义**并行编排**：准备和修复必须串行/集中，执行段并行——
三端共享同一个工作区和同一份 flow，乱并行会互相踩改动、同一个 bug 修三遍。

## 编排

**① 共享准备（主 agent，串行，只做一次）**

- 探测可用端（`web/`、`android/`、`ios/` 目录 + 设备）；缺设备先自动拉起
  （`emulator -avd` / `simctl boot`），拉不起的端记"未执行"，不阻塞其他端
- Phase 0–4：环境自检 → 扫描 → 埋点 → 契约表 → 生成用例（增量模式照主 skill）
- **三端构建串行**：依次 `flutter build web / apk / ios --simulator`
  （共享 `.dart_tool/`，并发构建会踩锁且互相拖慢 CPU）

**② 并行执行（每端一个子代理，同一条消息里一起派出）**

每个子代理只做三件事，**禁止改任何文件**（lib/、flow、registry 都不许碰）：

1. `bash <skill目录>/scripts/run_smoke.sh --platform <端> --all --skip-build --out .smoke/runs/<端>-<轮次>`（命令即全量授权，`--all` 必带）
   （`<skill目录>` = flutter-smoke-auto 的安装绝对路径，由主 agent 解析后写死进子代理提示词，
   子代理不要自己猜路径）
2. 按 `references/triage.md` 对每个失败做三分类，收集证据（截图路径、日志片段、源码位置）
3. 返回结构化结果：`{端, 每条用例: 通过/失败, 失败分类, 证据, 建议修复}`

子代理提示词里明确写上这个约束和返回格式。三端产物目录互不重叠，无写冲突。

**用例多（≥10 条）时叠加端内车道**（主 skill「端内并行」一节）：
`shard_flows.py` 分车道 → `device_pool.py` 每车道认领独立设备（移动端每端 ≤2 台，
内存预算核算）→ 每车道一个子代理 `--from-list lane-N.txt --device <认领的> --env
TEST_PHONE=$TEST_PHONE_LANE<N>`；Web 端直接 `--workers 4` 不开子代理。
执行顺序 Web 优先（最便宜的探测器，红了先修再耗模拟器），但发版结论以各端自己跑过为准。
所有子代理退出前 release 自己认领的设备（用户 pin 的不动）。

**③ 集中修复（主 agent，串行）**

- 合并三端分诊，**按根因去重**：同一缺陷三端都红只算一个，修一次
- TEST_DEFECT → 修 flow/spec；APP_DEFECT（本次会话开发的功能）→ 修 lib/ 代码；
  存量功能缺陷只记报告。每轮修改过完整性闸门（`check_test_integrity.py`）
- 可先用开发伴随模式热重载快验（`references/dev-loop.md`），确认后重建**仅受影响的端**

**④ 并行重跑 → 循环**

只重跑有失败的端、失败的 flow（子代理并行，同 ②）。全绿或触发主 skill
Phase 5.5 的终止条件（缺凭据/同一缺陷 3 轮/动存量模块/全局 5 轮）即收敛。

**⑤ 合并报告**

`.smoke/report.md` 三端合并：每端结果表 + 去重后的缺陷区（标注影响哪些端）+
自愈与修复记录 + 结论（每端 通过/阻塞 + 能否发版一句话）。回复里给摘要。

## 资源提醒

Android 模拟器 + iOS 模拟器 + Chromium 同机并跑吃内存（≥16G 建议值）。
机器扛不住（模拟器启动超时、执行明显卡顿）就退化为 Web → Android → iOS 串行，
在报告里注明原因。

## 参数

`/smoke-all <关键词>` → 三端都只跑匹配关键词的用例 + 冷启动（定向验证，编排不变；实现：各端 `run_smoke.sh --only <关键词>`）。

`/smoke-all release` → 各端 `--build-mode release` 测发版产物
（默认 android=profile / ios=debug / web=release）。
