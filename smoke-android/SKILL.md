---
name: smoke-android
description: /smoke-android — 对当前 Flutter 项目的 Android 端跑全量冒烟验收：构建 APK、安装到设备/模拟器、Maestro 执行、自愈、缺陷修复闭环、出报告。用户输入该命令即已明确选择冒烟验收模式，无需再确认场景。
---

# /smoke-android — Android 端全量冒烟

用户已明确下单：**冒烟验收模式，Android 端，全自动**。
不要再问"是想预览还是验收"（flutter-smoke-auto 第 0 步的询问跳过），直接执行。

## 执行

1. 读主 skill `flutter-smoke-auto` 的 SKILL.md 并完整遵循（references 按需读）。
2. Phase 0 环境自检：`adb devices` 没有可用设备就自己拉模拟器
   （`emulator -list-avds` 选一个 `emulator -avd <name>` 后台启动，等 boot 完成），
   拉不起来才停下来向用户说明缺什么。
3. 首次运行（无 `.smoke/registry.json`）完整走 Phase 1–4；已有则按增量模式只处理 diff。
4. 执行范围按用户输入定，**不是无脑全量**：
   - `/smoke-android`（无参数）→ `--all`（命令即全量授权）
   - `/smoke-android <关键词>` → `--only <关键词>`（只跑该功能的用例 + 冷启动，**不许改成 --all**）
   - 用户点名了设备/型号 → 加 `--device <serial/UDID>`（先 `device_pool.py claim --model` 认领；被别的会话占用就换一台或停下来说明），只在那台设备上跑
   执行：`bash <skill目录>/scripts/run_smoke.sh --platform android <范围参数> [--device ...]`
   （默认 profile 包保住 L1 层；测试凭据按 SKILL.md 用 `--env` 注入）。
5. 红灯走 Phase 5.5 修复闭环：本次会话开发的功能自动修代码重跑直到全绿；
   存量功能的缺陷只记报告不擅自修。每轮改动过完整性闸门。
6. 产出 `.smoke/report.md`，回复里给结论摘要：通过/阻塞 + 缺陷清单 + 能否发版一句话。

## 参数

`/smoke-android <关键词>` → 定向执行：只跑文件名/name 匹配关键词的 flow + smoke-01 冷启动
（实现：`run_smoke.sh --platform android --only <关键词>`）。其余照常（闸门、分诊、修复闭环）。

`/smoke-android <关键词> <设备名或UDID>` → 在指定设备上只跑该功能（`device_pool.py claim` 认领后 `--device` 传入；型号如 "iPhone 15"、"Pixel_6" 均可）。

`/smoke-android release` → `--build-mode release` 测发版产物。
