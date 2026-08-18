# flutter-smoke-auto

Flutter 三端 App（Android / iOS / Web）的全自动冒烟测试 skill：从源码推导业务主流程 →
生成用例（移动端 Maestro flow、Web 端 Playwright spec，共用一份选择器契约表）→
执行 → 分诊自愈 → 出报告。不需要人工录制脚本。

内含**测试完整性闸门**，防止 AI 在自愈过程中通过删断言、加 skip、调大超时来
伪造绿灯。这一部分与框架无关，Playwright / Cypress / pytest / Jest / Go test /
JUnit / Maestro 都适用。

## 安装

```bash
mkdir -p ~/.claude/skills
cp -r flutter-smoke-auto ~/.claude/skills/          # 全局可用
# 或放进项目，随仓库共享给团队：
cp -r flutter-smoke-auto <项目>/.claude/skills/
```

重启 Claude Code 会话后自动发现，不需要额外配置。

首次在项目里落地时，按 SKILL.md「首次落地」一节做三件事：
闸门脚本拷进 `.smoke/scripts/`、pre-commit hook 挂上、CI 配置落地。

## 用法

直接说人话即可，skill 会自动激活：

- 「给这个 App 做一遍冒烟测试」
- 「刚加完发布评价的功能，验证下主流程没坏」
- 「这几个测试挂了，帮我修一下」← 会先过完整性闸门

## 依赖

| 必需 | 说明 |
|---|---|
| Flutter 3.19+ | `Semantics(identifier:)` 从这版可用，三端通用定位的基础 |
| Python 3 | 闸门脚本零第三方依赖；视觉兜底层另需 Pillow |
| Android SDK / Xcode / Chrome | 至少一端 |

| 按端补充 | 作用 |
|---|---|
| Maestro CLI | 移动端执行。`curl -fsSL https://get.maestro.mobile.dev \| bash` |
| Node + Playwright | Web 端执行（W1）。装不上走 chrome-devtools MCP 兜底（W2） |
| marionette_mcp | L1 层：widget tree 精确定位 |
| mobile-mcp | L2 层：系统无障碍树 |
| idb | iOS 模拟器坐标点击（`xcrun simctl` 不支持注入触摸） |
| Pillow | L3 视觉兜底层（screen.py） |

## 结构

```
SKILL.md                        六阶段主流程 + 三级降级策略
references/
  journey-selection.md          怎么选冒烟用例（框架无关）
  triage.md                     失败三分类与禁止的「修复」（框架无关）
  test-integrity.md             完整性闸门说明（框架无关）
  vision-fallback.md            L3 视觉驱动规程
  maestro-flutter.md            Maestro 语法与 Flutter 语义树坑位（含 sliver 埋点规则）
  flutter-web.md                Web 端：语义树开关、Playwright、MCP 兜底
  dev-loop.md                   开发伴随模式：热重载 + 实时验证（不出报告）
scripts/
  scan_app.py                   静态扫描产出业务图谱
  check_registry.py             拦截幻觉选择器（flow + web spec），可与源码对账
  select_flows.py               定向执行选用例：git 改动/关键词 → 受影响用例+冷启动
  check_test_integrity.py       拦截弱化测试的改动（跨语言）
  screen.py                     截图/点击/差分/异常检测/日志采集
  run_smoke.sh                  构建、起设备/服务、执行、收产物（三端）
assets/
  flow-template.yaml            移动端用例骨架
  web-smoke/                    Web 端 Playwright 模板
  smoke-ci.yml                  GitHub Actions（gates + android + ios + web）
  pre-commit-hook.sh            闸门挂 git hook
tests/
  test_gates.py                 两个闸门的回归测试（python3 tests/test_gates.py）
```

## 已知状态

两个闸门 + 定向选择器有回归测试覆盖（`tests/test_gates.py`，含曾经实测放行的作弊路径），
screen.py 的纯函数部分见 `tests/test_screen.py`。
完整流程已在真实项目上端到端验证过一轮（2026-08-17，Android 端 5 用例 / 3 轮自愈），
分诊表和定向执行就来自那次实测反馈。
仍开放的校准项：screen.py 阈值（截图 900px、差分 0.005、空白比 0.88、超时 3 倍线）
需要更多项目的数据；`errors` 子命令的 logcat 时间窗改动待真机复验；
Web 端依赖 App 正确接入 `ensureSemantics()` 开关。首次使用建议人工核对前三轮结果。

## 设计取舍

- **AI 只在生成和修复时介入，CI 执行是确定性的**。否则冒烟测试会变得慢、贵、
  且每次结果不同，最终没人愿意跑
- **断言只写业务不变量**（到达、无错、有内容、可持久），不从实现反推预期，
  否则测试会把 bug 固化成「预期行为」
- **选择器必须先写进 App 再写进测试**，由 `check_registry.py` 强制（文本选择器
  默认就是错误），并可与源码对账，杜绝凭空生成的定位符
- **一份契约表管三端**：`Semantics(identifier:)` 在 Android/iOS/Web 分别渲染为
  resource-id / accessibilityIdentifier / flt-semantics-identifier
- **视觉兜底是下限不是常态**。同一元素反复要靠 L3 定位，说明该回去补埋点
