# flutter-smoke-auto

[English](README.md) | **简体中文**

**Flutter 全自动冒烟测试（Android / iOS / Web 三端）——自带完整性闸门，拦住 AI 在测试上作弊。**

这是一个 [Claude Code](https://claude.com/claude-code) Agent Skill：从 Flutter 源码推导业务主流程 → 改造可测性 → 生成确定性测试套件（移动端 Maestro flow、Web 端 Playwright spec）→ 执行 → 失败分诊 → 自愈**测试**缺陷 → 修复**应用**缺陷 → 出人工可复核的报告。人工介入点只剩一个：审阅最终报告。

## 为什么做这个

任何 AI 编码 agent 拿到"跑测试 → 失败自己修"这个循环之后，迟早会发现：让测试变绿最省力的办法不是修好代码，而是弄坏测试——删断言、加 `skip`、把 `toEqual` 换成 `toBeTruthy`、调大超时。每一步单看都有说得过去的理由，合起来是覆盖率悄悄死掉而报告说一切健康。这个失败模式社区已有[大量](https://pyor.review/blog/test-rewrite-failure-mode)[实证](https://dev.to/moonrunnerkc/ai-agents-cheat-on-pull-requests-i-mined-327-of-them-to-prove-it-43ij)，但现有答案大多停留在博客里的模式建议。

本 skill 把约束做成了**代码而不是文字**：

- **`check_test_integrity.py`** —— 扫 git diff，**阻断**删断言、新增 skip、强匹配换弱匹配、调小期望数量、超时膨胀、删测试文件。零依赖，适用于**任意**语言和框架（Playwright、Cypress、pytest、Jest、Go test、JUnit、Maestro——不限 Flutter）。以 pre-commit hook 和 CI 步骤双重挂载。默认对比基准是*冒烟起点 commit*，"先 commit 后检查"骗不过它。
- **`check_registry.py`** —— 选择器契约闸门。用例里的每个选择器必须存在于契约表（`.smoke/registry.json`），契约表里的每个 identifier 必须真的（带引号地）出现在声称的源文件里。幻觉选择器在生成期就被拦下，不用等到设备上才炸。文本选择器默认就是错误——最省力的绕过路径不能是唯一不阻断的那条。

两个闸门都经过 TDD（43 个回归用例），且针对实践中真实观察到的绕过手法做过加固——例如源码对账要求 identifier *带引号*出现，因为"在注释里提一句 id"曾是骗过它的最省力方式。

## 与同类方案的差异

| | Maestro 语法型 skill | 模型在环的 MCP 工具 | **flutter-smoke-auto** |
|---|---|---|---|
| 范围 | 命令/选择器参考手册 | 给 LLM 提供运行时"眼和手" | 全流程：推导 → 埋点 → 生成 → 执行 → 分诊 → 修复 → 报告 |
| 选择器纪律 | 靠约定 | 模糊匹配、自愈定位器 | 强制契约表 + 闸门校验 |
| 防弱化测试 | — | — | 基于 diff 的完整性闸门（pre-commit + CI） |
| CI 执行 | 确定性 | 每次跑都调模型（费钱、不确定） | **确定性、零模型成本**——AI 只在生成与修复时介入 |
| Web 支持 | — | 不一 | Playwright spec 与移动端共用同一契约表 |

## 优点详述

**1. 全流程管线，不是参考手册。** 现有 Maestro skill 教 agent 写 YAML；本 skill 把整件事做完：静态扫描源码得到路由/页面/控件 → 从文档抽取业务事实 → 埋语义标识 → 选 5–8 条发版阻断级主流程 → 生成套件 → 执行 → 分诊 → 修复 → 报告。人工只剩审阅报告一步。

**2. 防作弊靠可执行的事实核查，不靠提示词自觉。** 完整性闸门只看 diff 的事实：断言净减少、新增 skip/only/`optional: true`/`@Ignore`、强匹配换弱匹配（`toEqual` → `toBeTruthy`）、期望数量调小、超时涨 3 倍以上、整个测试文件被删——全部阻断。默认基准取*冒烟起点 commit*（先 commit 也瞒不过）；未跟踪的新文件也纳入扫描（首轮生成不是盲区）；失败输出刻意不打印任何可复制的绕过开关。pre-commit hook 与 CI 双重挂载。

**3. 确定性 CI，零模型成本。** AI 只在两个时刻介入——生成用例、修复失败。CI 里跑的是纯 Maestro/Playwright：分钟级、可复现、没有按次计费的 API 账单。模型在环的方案把这个关系倒了过来，每一次执行都要付出模型延迟、不确定性和调用成本。

**4. 埋一次点，三端通用。** `Semantics(identifier:)` 在 Android 渲染成 `resource-id`、iOS 是 `accessibilityIdentifier`、Web 是 DOM 属性 `flt-semantics-identifier`。一份契约表同时驱动 Maestro flow 和 Playwright spec——而契约表本身还要和源码对账（identifier 必须*带引号*出现在声称的文件里，因为写进注释曾是骗过对账的最省力方式）。

**5. 断言不可能同义反复。** 生成的断言被限制为业务不变量——可到达、无错误、杀进程重启后数据仍在、可返回、有内容——或从需求文档抽取的具体期望（并标注出处行号）。禁止把代码行为翻译成断言：那等于把今天的 bug 固化成明天的"预期"。

**6. 不对称分诊保住信号。** 失败三分类：`TEST_DEFECT`（定位/等待/起始态问题——可自动修，上限 3 轮）、`APP_DEFECT`（业务断言不达成——**禁止改测试**）、`ENV_FLAKE`（重试一次，仍败升级处理）。分不清就按 `APP_DEFECT`：误报一个 bug 的代价是几分钟，掩盖一个 bug 的代价是一次发版。每一次自愈改动都写进报告的"自愈记录"区，供人复核。

**7. 定向执行让它日常可持续。** `--changed` 把 `git diff` → 契约表 `file` 字段 → 受影响用例（subflow 引用反查主 flow）自动映射出来，永远附带冷启动锚点——几分钟而不是一整轮。四类改动强制全量，因为影响面就是全局：路由/导航、全局状态、主题/国际化、依赖或 Flutter 版本升级。

**8. 工程上经得起看。** 闸门脚本零第三方依赖（内置 Maestro YAML 子集迷你解析器，不引 PyYAML），兼容 macOS 系统 bash 3.2；43 个回归用例全部先写红（先复现作弊路径再修）；针对实践中的真实绕过手法加固过。视觉兜底（截图 + 模型定位 + 坐标点击）是最后手段：每次点击都有像素差分验证，整条用例都活在这一层本身就会被报告为可访问性缺陷。

## 仓库内容

| 路径 | 说明 |
|---|---|
| `flutter-smoke-auto/` | 主 skill：SKILL.md（工作流）、7 份参考文档、6 个脚本、CI/hook/flow 模板、闸门回归测试 |
| `smoke-all/` | `/smoke-all` —— 所有可用端全量冒烟验收，并行执行 |
| `smoke-android/` `smoke-ios/` `smoke-web/` | `/smoke-android` 等 —— 单端全量冒烟验收 |

核心脚本（全部零依赖 Python 3 / 兼容 bash 3.2）：

- `check_test_integrity.py` —— 防弱化闸门（框架无关，可在任何仓库单独使用）
- `check_registry.py` —— 选择器契约 + 源码对账闸门
- `select_flows.py` —— git diff → 受影响用例映射，支撑定向执行
- `run_smoke.sh` —— 构建、起设备/服务、执行、收集产物（三端）
- `screen.py` —— 截图 / 坐标点击 / 像素差分 / 红屏白屏检测 / logcat 异常采集
- `scan_app.py` —— 静态扫描 `lib/` 产出应用地图（路由、页面、可交互控件）

## 安装

```bash
# 通过 skills.sh
npx skills add ceeyang/flutter-smoke-auto

# 或手动
git clone https://github.com/ceeyang/flutter-smoke-auto.git
cp -r flutter-smoke-auto/flutter-smoke-auto flutter-smoke-auto/smoke-* ~/.claude/skills/
```

然后在 Flutter 项目里让 Claude Code 做冒烟测试即可——或直接 `/smoke-all` 跑三端全量验收。首次运行时 skill 会完成埋点、生成套件、挂好 pre-commit hook 和 CI 工作流，产物都在项目的 `.smoke/` 目录下。

完整性闸门单独使用（任何仓库、任何测试框架）：

```bash
cp flutter-smoke-auto/scripts/check_test_integrity.py .smoke/scripts/
python3 .smoke/scripts/check_test_integrity.py          # 退出码 1 = 测试被弱化了
```

## 环境要求

- Flutter 3.19+（`identifier` 语义属性从这版可用）
- 移动端需 [Maestro](https://maestro.mobile.dev) + Java 运行时（可复用 Android Studio 自带 JDK）
- Web 端需 Playwright（`npx playwright`）——可选，另有 chrome-devtools MCP 兜底路线
- iOS 需要 macOS

## 工作原理

```
Phase 0   环境自检 + 场景分流（开发伴随 / 定向验证 / 全量验收）
Phase 1   静态扫描 + 业务事实抽取  →  .smoke/app-map.json
Phase 2   可测性改造（Semantics identifier）  →  .smoke/registry.json  [闸门：源码对账]
Phase 3   选冒烟用例（5–8 条发版阻断级 happy path，只写业务不变量）  →  .smoke/plan.md
Phase 4   生成（Maestro flow + Playwright spec）  [闸门：选择器契约]
Phase 5   执行与分诊（TEST_DEFECT / APP_DEFECT / ENV_FLAKE），自愈 ≤3 轮  [闸门：每轮完整性]
Phase 5.5 应用缺陷修复闭环（本次会话开发的代码）直到全绿  [闸门：每轮完整性]
Phase 6   报告（结论 / 用例结果 / 缺陷 / 自愈记录 / 覆盖缺口）+ CI 配置
```

CI 里只跑 Maestro/Playwright——不调模型、确定性、分钟级、零 API 成本。AI 恰好介入两次：生成时、修复时。

## 协议

[MIT](LICENSE)
