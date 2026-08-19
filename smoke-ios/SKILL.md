---
name: smoke-ios
description: /smoke-ios — 对当前 Flutter 项目的 iOS 端跑全量冒烟验收：构建模拟器 .app、simctl 安装、Maestro 执行、自愈、缺陷修复闭环、出报告。用户输入该命令即已明确选择冒烟验收模式，无需再确认场景。
---

# /smoke-ios — iOS 端全量冒烟

用户已明确下单：**冒烟验收模式，iOS 端，全自动**。
不要再问"是想预览还是验收"（flutter-smoke-auto 第 0 步的询问跳过），直接执行。

## 执行

1. 读主 skill `flutter-smoke-auto` 的 SKILL.md 并完整遵循（references 按需读）。
2. Phase 0 环境自检：需要 macOS + Xcode。没有已启动的模拟器就自己拉
   （`xcrun simctl list devices available` 选一台 `xcrun simctl boot <udid>`，
   `open -a Simulator`），拉不起来才停下来向用户说明缺什么。
   模拟器构建不需要签名证书；真机不在冒烟范围。
3. 首次运行（无 `.smoke/registry.json`）完整走 Phase 1–4；已有则按增量模式只处理 diff。
4. 执行：`bash <skill目录>/scripts/run_smoke.sh --platform ios --all`（命令即全量授权，`--all` 必带）
   （默认 debug 包——iOS 模拟器不支持 profile 注入；坐标兜底需 idb，见主 skill）。
5. 红灯走 Phase 5.5 修复闭环：本次会话开发的功能自动修代码重跑直到全绿；
   存量功能的缺陷只记报告不擅自修。每轮改动过完整性闸门。
6. 产出 `.smoke/report.md`，回复里给结论摘要：通过/阻塞 + 缺陷清单 + 能否发版一句话。

## 参数

`/smoke-ios <关键词>` → 定向执行：只跑文件名/name 匹配关键词的 flow + smoke-01 冷启动
（实现：`run_smoke.sh --platform ios --only <关键词>`）。其余照常（闸门、分诊、修复闭环）。

`/smoke-ios release` → `--build-mode release` 测发版产物。
