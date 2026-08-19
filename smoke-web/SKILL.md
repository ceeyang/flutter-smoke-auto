---
name: smoke-web
description: /smoke-web — 对当前 Flutter 项目的 Web 端跑全量冒烟验收：flutter build web、本地起服务、Playwright（或 chrome-devtools MCP 兜底）执行、自愈、缺陷修复闭环、出报告。用户输入该命令即已明确选择冒烟验收模式，无需再确认场景。
---

# /smoke-web — Web 端全量冒烟

用户已明确下单：**冒烟验收模式，Web 端，全自动**。
不要再问"是想预览还是验收"（flutter-smoke-auto 第 0 步的询问跳过），直接执行。

## 执行

1. 读主 skill `flutter-smoke-auto` 的 SKILL.md 并完整遵循，Web 细节读
   `references/flutter-web.md`。
2. 前置确认：项目有 `web/` 目录（没有就报出来停止，不要擅自 `flutter create` 加端）；
   `main.dart` 里有 `SMOKE_TEST` 守护的 `ensureSemantics()` 开关（没有按主 skill
   Phase 2 补上——不开语义树 Web 端全部定位失败）。
3. 首次运行（无 `.smoke/registry.json`）完整走 Phase 1–4；已有则按增量模式只处理 diff。
   Web spec 按 `assets/web-smoke/` 模板生成到 `.smoke/flows/web/`。
4. 执行：`bash <skill目录>/scripts/run_smoke.sh --platform web --all`（W1，Playwright；命令即全量授权，`--all` 必带）。
   没有 node/Playwright 时走 W2：chrome-devtools MCP 按 `.smoke/plan.md` 逐条执行，
   结果记入报告并**注明是 agent 手工执行、不可进 CI**（规程见 flutter-web.md）。
5. 红灯走 Phase 5.5 修复闭环：本次会话开发的功能自动修代码重跑直到全绿；
   存量功能的缺陷只记报告不擅自修。每轮改动过完整性闸门。
6. 产出 `.smoke/report.md`，回复里给结论摘要：通过/阻塞 + 缺陷清单 + 能否发版一句话。

## 参数

`/smoke-web <关键词>` → 定向执行：只跑文件名匹配关键词的 spec + 冷启动
（实现：`run_smoke.sh --platform web --only <关键词>`）。其余照常（闸门、分诊、修复闭环）。
