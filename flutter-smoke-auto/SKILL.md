---
name: flutter-smoke-auto
description: 当用户要给 Flutter App（Android / iOS / Web 三端）做冒烟测试、自动化测试、E2E 测试、回归验证、"测一下能不能跑通"、提测/发版前验证、CI 里加自动化测试，或刚完成一批功能想确认主流程没坏时使用——即使没明确说"冒烟"两个字；新增页面、改动路由时同样适用。开发过程中想"边改边看效果 / 实时预览 / 刷新看看"时也用本 skill。另外，任何需要修改已有测试让它通过、处理失败测试、测试自愈或 flaky 治理的场景也适用，无论项目是不是 Flutter——对 Playwright、Cypress、pytest、Jest、Go test、JUnit、Maestro 同样有效。
---

# Flutter 全自动冒烟测试（Android / iOS / Web）

从 Flutter 源码推导业务主流程 → 改造可测性 → 生成用例（移动端 Maestro flow、
Web 端 Playwright spec，共用一份契约表）→ 执行 → 分诊自愈 → **修复功能缺陷并重跑
（本次会话开发的功能）** → 出报告。人工介入点只有一个：审阅最终报告。不需要录制脚本。

配合功能开发使用时（"开发 X 并测好"），这套流程是开发完成定义的一部分：
冒烟不全绿不算开发完，红灯自动进入 Phase 5.5 的修复闭环，而不是交给用户。

## 第 0 步：先判断场景，走错通道比慢更糟

本 skill 有三种工作场景，通道和范围都不同，动手前先判断：

| 场景 | 信号 | 通道与范围 |
|---|---|---|
| **开发伴随**（边改边看） | 功能正在写到一半；"看下效果 / 边改边看 / 刷新看看"；UI 微调 | `flutter run` 热重载 + 实时查看（Web 用 chrome-devtools MCP），见 `references/dev-loop.md`。不建 `.smoke`、不出报告 |
| **定向验证**（改完某个功能要确认它没坏）——**日常默认** | "改了 XX 功能测一下"、"开发 XX 并测好"、修完 bug 要验证 | 只跑**受影响的用例 + 冷启动那条**（选法见下面「定向执行」），build+install 照常、闸门照过、结论可信，但几分钟内完事 |
| **全量冒烟验收** | 用户明确说"全量 / 提测 / 发版 / 跑冒烟"、使用 `/smoke-*` 命令、CI | Phase 0–6 完整流程，所有用例所有端 |

**改一个小功能 ≠ 全量冒烟。** 全量一轮十几分钟，把它当日常验证会让人不愿意再跑测试。
日常改动走定向验证；全量留给发版/提测节点和 `/smoke-*` 命令。
但有四类改动**必须升级为全量**——它们的影响面就是全局：
路由/导航结构、全局状态管理、主题/国际化、依赖升级或 Flutter 版本变更。

**「全量」两个字必须先问全量的对象是谁**——实测踩过的误判：

| 用户说 | 正确解析 | 错误解析 |
|---|---|---|
| "跑 XX 功能的全量测试 / 完整测试" | `--only XX`（该**功能**的所有用例） | ~~`--all`~~ |
| "全量跑一遍 / 提测 / 发版" | `--all`（全套） | — |

带功能限定词的"全量"永远是定向。`--only` 关键词没命中时 select_flows 会硬拒绝
并列出可选用例名——**改关键词重试，不许因为没命中就升级成 `--all`**。

**多端验收默认并行执行**（不只属于 /smoke-all）：同一个请求要跑 ≥2 个端时，
构建串行（共享 `.dart_tool/`）、执行段每端一个子代理**同一条消息并行派出**
（子代理只执行+分诊不改文件，规则同 smoke-all ②），不要一端跑完再跑下一端干等。
单端内用例 ≥10 条再叠加车道（见下节）。

**判断不了就问，不要猜**，一句话二选一：
「你是想边改边看效果（秒级热重载预览），还是功能已完成、要做一轮正式冒烟验收（构建安装包全流程）？」

例外：用户通过 `/smoke-all`、`/smoke-android`、`/smoke-ios`、`/smoke-web`
命令进入时，命令本身就是答案——全量冒烟验收 + 指定端，跳过询问直接执行。

### 定向执行的入口（改完小功能验证就用这个，不要跑全量）

`run_smoke.sh` 自带两个定向参数，选用例的推导由 `scripts/select_flows.py` 自动做
（git diff → registry 的 file 字段 → 用到受影响 id 的用例，含 subflow 反查主 flow），
结果永远附带冷启动锚点（最便宜的全局回归保险）：

```bash
bash scripts/run_smoke.sh --platform android --changed     # 日常默认：按 git 改动自动圈范围
bash scripts/run_smoke.sh --platform android --only login  # 手动圈范围（web 同样支持）
bash scripts/run_smoke.sh --platform ios --failed          # 修复轮：只重跑上一轮失败的用例
# 只看会选中哪些用例（不执行）：python3 scripts/select_flows.py --flows .smoke/flows --registry .smoke/registry.json --changed
```

**范围参数必带**（`--changed`/`--only`/`--failed`/`--all`），裸跑会被脚本拒绝——
全量是明确的选择（`--all`），只该出现在提测/发版和 `/smoke-*` 命令里，
不是忘了带参数时的静默默认。
改动映射不到任何用例时（典型：新功能还没有用例），select_flows 会明确提醒：
只为新功能增量补一条 flow/spec，跑新用例 + 冷启动，不重建其他任何东西。
定向红灯的分诊、闸门、Phase 5.5 修复闭环与全量完全一致——省的是范围，不是纪律。

**定向用例必须自包含**——干净冷启动起步、公共前置（登录等）用 `runFlow` 引 subflow、
自己走完到达路径。生成时自查一句：**"这条用例在刚装好的干净设备上能独立跑通吗？"**
写法模板与缩短前置的手段见 `references/journey-selection.md`「定向用例的自包含要求」。

猜错的代价不对称：把开发伴随误判成冒烟验收 = 每次改动白等几分钟构建；
把验收误判成开发伴随 = 交出去的结论没测过真实产物。

两个衔接点：开发伴随模式收尾（用户说"行了 / 就这样 / 完成了"）时，**主动提议跑一轮正式冒烟收口**；反过来，冒烟的 Phase 5.5 修复轮次里，可以先用开发伴随模式秒级快验修没修对，确认后再 build + install 出定论——报告只认安装包跑出来的结果。

### 端内并行：多设备车道（用例多、等不起串行时）

用例规模大（≥10 条）时，把互不冲突的用例拆成车道并行跑。三条铁律先立住：

1. **并行是声明出来的，不是默认的。** 用例在 `tags:` 里声明资源占用
   （`mutates-posts` / `readonly`），Phase 3 的 plan.md 里同步写一行
   `资源: writes=[posts] reads=[feed]`。没声明的视为互相冲突，保守串行。
2. **写同一资源（含传递闭包）→ 同车道串行；每条车道独立测试账号**
   （`--env TEST_PHONE=$TEST_PHONE_LANE1`）——大部分"互踩"其实是踩同一账号的数据。
3. **iOS 一台模拟器同时只许一个 maestro 驱动**（两个自动化会话并发会打崩
   SpringBoard，见 PITFALLS）；多台模拟器各配各的驱动是安全的。

工具链与流程（构建一次，车道只执行）：

```bash
python3 scripts/shard_flows.py --flows .smoke/flows --lanes 2 --out .smoke/lanes
python3 scripts/device_pool.py claim --platform android --owner $CLAUDE_SESSION_ID
                                    # 认领设备（已启动的优先）；被别的会话占用会拒绝
bash scripts/run_smoke.sh --platform android --from-list .smoke/lanes/lane-1.txt \
     --device <认领到的serial> --skip-build --env TEST_PHONE=$TEST_PHONE_LANE1 &
# lane-2 同理换设备换账号；子代理并行时每车道一个代理，只执行+分诊不改文件
python3 scripts/device_pool.py release --mine --owner $CLAUDE_SESSION_ID   # 跑完释放
```

**设备池纪律**：注册表在 `~/.flutter-smoke/device-pool.json`，跨会话共享。
用户 pin 给某会话的设备（`assign --pin`）别的会话碰不了，解除只能用户明示
（`release --unpin`）。占用超 2 小时 `list` 里标 STALE，只提示不自动抢。
上限：移动端每端 ≤2 台（`FSA_MAX_PER_PLATFORM`），并按实时内存预算核算
（android 3G / ios 2.5G，预留 8G），超了拒绝 claim。

**Web 端不用车道工具**：`--workers 4`（或 `SMOKE_WORKERS=4`）走 playwright
原生并行，每个 worker 独立 browser context，缓存/cookie 天然隔离；
账号用 `helpers.ts` 的 `laneEnv()` 按 worker 分发。

**Web 优先原则**：能在 Web 端验证的用例（纯 Dart 共享逻辑、无原生插件依赖）
先跑 Web——秒级构建、零模拟器内存、4 车道并行，是最便宜的探测器。Web 红了
先修再碰模拟器。但 **Web 绿 ≠ 移动端绿**（ATS、权限、键盘、平台通道只在移动端炸），
发版结论仍以各端自己跑过为准；仅移动端能测的用例在 plan.md 标 `platforms: [android, ios]`。
省内存的设备偏好序：已启动的设备 > iOS 模拟器(~2.5G) > Android 模拟器(~3G)。

**账号来源**：优先用用户提供的测试账号（车道数 > 账号数时多余车道只跑只读用例）。
没提供账号且 App 支持自助注册时，满足三个条件才自建：有 `SMOKE_TEST` 固定验证码
后门、后端是测试环境（base URL 含 localhost/test/staging，判断不了就停下来问）、
建完缓存进 `.smoke/accounts.json` 复用不重复注册。缺任一条件 → 写类用例锁死单车道。

## 为什么按这个顺序做

三件事决定这套流程是有用还是有害，先理解再执行：

1. **断言必须来自业务期望，不能来自实现。** 如果直接把代码行为翻译成断言，实现里的 bug 会被固化成"预期"，测试永远绿灯，等于没测。所以断言优先从需求文档/PRD/issue 描述抽取；没有文档时，只写**业务不变量**（见 Phase 3），不写"点了 A 会变成 B"这种从代码抄下来的行为。
2. **选择器必须先写进 App 再写进测试。** 凭记忆或猜测生成 `tapOn: "登录"` 是这类任务最主要的失败源。Phase 2 先把 `Semantics(identifier:)` 埋进源码并落成契约表，Phase 4 只允许从契约表取值，`check_registry.py` 会拦截任何表外选择器。
3. **自愈只修测试的技术缺陷，不修业务断言。** 定位失败、等待不足、启动态不干净 → 可以改 flow。业务断言失败（页面没到、数据没存、报错弹窗）→ 一律记为 App 缺陷，禁止放宽或删除断言来换绿灯。放宽断言比测试挂掉危险得多，因为它会伪造安全感。

## Phase 0 — 环境自检

依次确认，缺什么就先装什么或告诉用户怎么装，不要跳过后继续跑：

```bash
flutter --version                 # 需要 3.19+，identifier 语义属性从这版开始可用
maestro --version                 # 移动端。缺失：curl -fsSL https://get.maestro.mobile.dev | bash
java -version                     # Maestro 真执行时才需要，wrapper 能跑不代表有 Java（见下）
adb devices                       # Android 设备/模拟器
xcrun simctl list devices booted  # iOS 模拟器（仅 macOS）
npx playwright --version          # Web 端（W1 路线）。装不上就走 W2，见 references/flutter-web.md
```

三个实测踩过的环境坑，Phase 0 就处理掉（排查命令与修法见 `references/maestro-flutter.md`「环境坑位」）：

- **Java 缺失**：`maestro --version` 是 wrapper 能通过，真执行才炸；`run_smoke.sh` 已自动借用 Android Studio 自带 JDK 兜底
- **iOS 明文 HTTP（ATS）**：API base 是 `http://` 且 Info.plist 没有 ATS 例外时，iOS **静默拒绝**所有明文请求——登录点了没反应，极难排查
- **iOS 模拟器软键盘遮挡**：开 Connect Hardware Keyboard，否则软键盘挡住输入框让 tap 落空

项目是否有 Web 端，看 `web/` 目录是否存在或 `flutter devices` 里有没有 Chrome。

执行顺序按成本从低到高：**Web（秒级、免模拟器）→ Android（快、免签名）→ iOS**。
没有 Web 端就从 Android 开始。上一端全绿再跑下一端。

## Phase 1 — 抽取业务图谱

```bash
python scripts/scan_app.py --project <flutter项目根目录> --out .smoke/app-map.json
```

脚本静态扫描 `lib/`，产出路由表、页面清单、可交互控件、表单字段、网络调用点。它是启发式的，会漏也会多报。**跑完必须自己读一遍 `lib/` 的路由定义文件和主要页面**校正结果——脚本负责把范围缩小到几十个文件，判断由你做。

同时找业务事实来源，按优先级：`docs/`、`README`、`*.md` 需求文档 > issue/PR 标题 > 路由与页面命名 > API 接口定义。找到什么记进 `.smoke/business-context.md`，Phase 3 写断言时只能引用这里的内容。

## Phase 2 — 可测性改造

给主流程涉及的控件补语义标识。定位一律走 Flutter 的 Semantics Tree，不读 widget 树，所以 `Key` 是无效的，必须用 `Semantics(identifier:)` 或 `semanticLabel`。`identifier` 是三端通用的：Android 渲染成 `resource-id`，iOS 是 `accessibilityIdentifier`，Web 是 DOM 属性 `flt-semantics-identifier`——埋一次点，一份契约表三端共用。

有 Web 端的项目，同时在 `main.dart` 里加 `SMOKE_TEST` 语义树开关——Flutter Web 默认不生成语义 DOM，不开的话 Web 端所有定位都会失败。代码与构建参数见 `references/flutter-web.md`。

命名规约：`<页面>_<元素>_<类型>`，全小写下划线，例如 `login_phone_input`、`login_submit_btn`、`home_feed_list`、`post_detail_title`。

```dart
Semantics(
  identifier: 'login_submit_btn',
  child: ElevatedButton(onPressed: _submit, child: const Text('登录')),
)
```

改完生成契约表 `.smoke/registry.json`，每个 identifier 一条，五个字段都要：

```json
{"login_submit_btn": {"file": "lib/features/auth/login_page.dart", "line": 84, "type": "button", "screen": "LoginPage", "label": "登录"}}
```

只改主流程用得上的控件，一次别超过 30 个——改动越大越难 review，也越容易和用户手上的分支冲突。改完做两个验证，都过了才算 Phase 2 完成：

```bash
flutter analyze                                                            # 没编译错误
python scripts/check_registry.py --registry .smoke/registry.json --source .   # 契约表↔源码对账
```

对账失败说明 registry 是空头支票（identifier 写进表里但没真落到源码，或行号文件对不上），当场修，别把问题带进 Phase 4。

**同时清点"开发期便利"**——预填的演示账号、mock 数据、跳过登录的后门。这类东西是自动化的隐形杀手：预填账号 + `inputText` 追加语义 = 拼接出非法值，产生"格式不正确"这种看似业务 bug 的静默失败。逐项决定测试怎么处理（用例里先 `eraseText` 清场 / 用 `SMOKE_TEST` 编译开关关掉预填 / 干脆利用它），把结论记进 `.smoke/business-context.md`。

## Phase 3 — 选冒烟用例

冒烟测的是"这个版本值不值得继续测"，不是覆盖率。选择标准，全部满足才入选：

- 挂掉就必须停止发版
- 走的是 happy path，不含边界值和异常分支
- 单条 90 秒内能跑完
- 不依赖上一条用例的残留状态

总条数控制在 5–8 条，整套 5 分钟内跑完。超时说明选宽了，砍掉。

选法参考 `references/journey-selection.md`（按 App 类型给了取舍模板）。

断言只写这几类**业务不变量**，它们不依赖实现细节，因此不会同义反复：

| 类型 | 写法 |
|---|---|
| 到达 | `assertVisible: id: <目标页锚点>` |
| 无错 | `assertNotVisible` 报错弹窗/错误文案/空白占位 |
| 持久 | 杀进程重启后关键数据仍在 |
| 可逆 | 返回上一页不卡死、不白屏 |
| 有内容 | 列表页至少渲染出一项，而不是永久 loading |

有需求文档时，额外加从文档抽的具体期望值，并在 flow 注释里标注出处行号，方便日后追溯。

产出 `.smoke/plan.md`：每条用例写清楚 **目的 / 前置数据 / 步骤 / 断言 / 失败意味着什么 / 资源与平台**（`资源: writes=[...] reads=[...]`、`platforms: [web, android, ios]`——车道并行与 Web 优先靠这两行做决策，生成 flow 时同步落成 `tags:` 里的 `mutates-*`/`readonly`）。这份文件是后面所有生成动作的唯一依据。

## Phase 4 — 生成用例

按 `.smoke/plan.md` 逐条生成。同一份 plan、同一份 registry，两种产物：

- **移动端**：Maestro flow → `.smoke/flows/*.yaml`，模板 `assets/flow-template.yaml`，
  语法和 Flutter 坑位见 `references/maestro-flutter.md`
- **Web 端（项目有 web/ 才生成）**：Playwright spec → `.smoke/flows/web/*.spec.ts`，
  模板 `assets/web-smoke/`（config + helpers + spec 骨架），规程见 `references/flutter-web.md`。
  没有 node 环境时走 W2（chrome-devtools MCP，agent 驱动兜底），同见该文档

三条硬规则（两种产物都适用）：

- 选择器一律走契约表：移动端 `id: <registry键>`，Web 端 `byId(page, '<registry键>')`。
  不要用 `text:` / `getByText` 匹配文案（文案会改、会做国际化，id 不会）。
  唯一豁免：`assertNotVisible` / `expectNoErrorText` 的无错断言——错误提示没有 id
- 每条用例起始态干净：flow 开头 `launchApp: clearState: true`，spec 用 `launchApp(page)`
- 测试账号、手机号、验证码从环境变量注入，不硬编码

生成完立刻校验（yaml 和 spec 一起扫）：

```bash
python scripts/check_registry.py --flows .smoke/flows --registry .smoke/registry.json
```

有表外选择器就停下来回 Phase 2 补埋点，**不要**改成 `text:` 绕过去——那是把问题推迟到运行时。文本选择器默认就是错误，不是警告。

## 元素定位的三级降级（移动端）

Web 端的对应关系（byId → 无障碍树 → 截图坐标）见 `references/flutter-web.md`。
每一步单独决策用哪一层，不要整条用例锁死在一层：

| 层 | 手段 | 成本 | 前提 |
|---|---|---|---|
| L1 | marionette 的 `get_interactive_elements` + `tap` | 极低 | 埋点 + debug/profile 包 |
| L2 | mobile-mcp / Maestro 走系统无障碍树 | 低 | 无，release 包也行 |
| L3 | `scripts/screen.py` 截图 + 模型定位 + 坐标点击 | 高 | 无，永远可用 |

先试 L1，拿不到目标就 L2，再拿不到才 L3。一条 20 步的用例通常只有 1–3 步会掉到 L3。
全程都在 L3 说明这个 App 的可访问性有问题，那本身就是该报的缺陷。
**iOS 上换层前必须确认旧驱动已退出**——Maestro/idb/mobile-mcp 并发连同一台模拟器
会触发 XCTAutomationSession 竞态、打崩 SpringBoard（硬规则见 vision-fallback「iOS 后端互斥」）。

L3 的完整规程见 `references/vision-fallback.md`，三条不能省的纪律：
坐标换算只走 `screen.py tap --space image --ref`（不在 prompt 里自己做乘除法）；
每次点击后 `screen.py diff` 验证，`no_change` 就重新截图定位而**不是重试同一坐标**；
先 `inspect`/`errors` 零 token 预筛，过滤不掉的才交给模型看。

## Phase 5 — 执行与自愈

```bash
# 范围参数必带：全量验收 --all，日常定向 --changed，修复轮 --failed
bash scripts/run_smoke.sh --platform web --all       # 有 web 端先跑它，秒级
bash scripts/run_smoke.sh --platform android --all   # 默认 profile 包，保住 L1 层
bash scripts/run_smoke.sh --platform ios --all
```

测试凭据用 `--env TEST_PHONE=...` 传，或直接放环境变量（`TEST_PHONE`/`TEST_OTP` 等会自动透传）。发版前建议对 Android 再补一轮 `--build-mode release`，测真实产物。

上面按顺序列的是**依赖关系**（构建串行、Web 最便宜先探路），不是执行方式：
要跑多个端时，构建完成后各端执行用子代理并行（第 0 步「多端验收默认并行执行」），
不要串行干等。

失败时，按 `references/triage.md` 的规则做三分类，这是整套流程最关键的一步：

- **TEST_DEFECT**（定位失效、等待不足、起始态不干净）→ 允许自动修 flow，重跑
- **APP_DEFECT**（业务断言不达成、崩溃、接口报错）→ 写进 `.smoke/report.md` 的缺陷区，**不改测试**
- **ENV_FLAKE**（设备掉线、网络超时、模拟器卡死）→ 原样重试一次，仍失败按 APP_DEFECT 处理

自愈上限 3 轮。3 轮后仍红的照实报告。分不清是哪一类时，一律按 APP_DEFECT 处理——误报一个 bug 的代价，远小于放过一个。

### 每轮改完必须过闸门

```bash
python scripts/check_test_integrity.py   # 默认基准：上次冒烟起点（.smoke/state.json），无则 HEAD；含工作区与未跟踪新文件
```

**顺序不可颠倒：闸门通过之后才 commit 这一轮改动。** 先 commit 再检查，
对比 HEAD 的 diff 就是空的；默认基准取冒烟起点能兜住这种情况，但那是保险不是流程。

退出码非 0 就是你刚才弱化了测试，**回滚这次修改**，把该项重新归为 APP_DEFECT。
不要用 `--warn-only` 绕过——那个开关是给人用的，agent 用它等于把闸门拆了。
`assertion_target_changed` 提示也不要无视：换定位目标只有"id 改名且 diff 里有证据"一种合法理由。

这一步不是可选的。上面 `references/triage.md` 里的禁令是文字约束，
你在跑到第三轮开始烦躁的时候可能会给自己找到例外；这个脚本只看 diff 的事实，
不看理由。详见 `references/test-integrity.md`。

它拦这些：删断言、加 skip/optional/Ignore、强匹配换弱匹配、
调小期望数量、超时涨 3 倍以上、删整个测试文件。适用于任何语言和框架，
不限 Flutter。

## Phase 5.5 — 缺陷修复闭环（默认开启）

APP_DEFECT 不是终点。冒烟属于"开发完成"的定义：功能是刚开发的（本次会话写的代码），
红灯就是你的返工单，**自动修复功能代码并重跑，直到全绿或触发终止条件**，
不要停下来把红灯报告交给用户——用户要的是完成的功能，不是缺陷清单。

循环规则：

1. 按报告里的源码位置修 `lib/` 下的**功能代码**（闸门只拦改测试，改业务代码不受限）；
2. 修完可先用开发伴随模式秒级快验（`references/dev-loop.md`，热重载确认修没修对，
   省掉无效的整轮构建），确认后 build+install 用 `--failed` 只重跑上轮失败的用例，
   过了再 `--all` 全量跑一遍（防止修 A 坏 B）——写进报告的结论只认安装包的结果。
   大面积失败（脚本会提示 ⚠）是环境/前置级故障，先修根因，不要整轮重试；
3. 每轮修复照常过完整性闸门——修功能的过程中顺手"调整"了测试，同样会被拦；
4. 全绿 → 进 Phase 6，报告即完成凭证。

**必须停下来问用户的情况**（只有这四种，其余一律自己继续）：

- 缺测试账号/凭据，或后端接口不可用——不是代码能修的；
- 同一条缺陷修了 3 轮仍红——说明对需求的理解可能有偏差，继续硬修是在猜；
- 修复需要改动本次会话没碰过的既有模块的行为——超出本次开发授权；
- 全局循环达到 5 轮上限——带着剩余红灯出报告，说明卡在哪。

功能不是本次会话开发的（对既有 App 首次做冒烟）时，本阶段不适用：
红灯是既有缺陷，写报告交给用户决定修不修，不要擅自动别人的存量代码。

**例外：阻塞性的环境/配置缺陷不受"存量不修"限制。** App 根本起不来、连不上后端、
ATS 拒绝明文 HTTP 这类问题会挡住全部用例——不修就什么都测不了，等用户批复毫无意义。
直接修，但在报告里单独标出「非本次开发范围的前置修复」，写清改了什么、为什么必须改。
判断标准：它挡住的是**测试的进行**而不是某条用例的通过，且修法是配置级的（plist、
gradle、环境变量），不改业务逻辑。

## Phase 6 — 报告与固化

`.smoke/report.md` 用这个结构：

```markdown
# 冒烟测试报告 <日期> <commit>
## 结论
通过 / 阻塞（一句话说清能不能发版）
## 用例结果
| 用例 | 平台 | 结果 | 耗时 | 备注 |
## 缺陷
按严重度排，每条附截图路径、复现步骤、相关源码位置
## 自愈记录
本轮改了哪些 flow、为什么改（供人复核，防止 AI 悄悄放宽了断言）
## 覆盖缺口
哪些主流程还没纳入，为什么
```

首次运行额外产出 CI 配置（`assets/smoke-ci.yml` 改一改即可，含 Android/iOS/Web 三个 job）。**CI 里只跑 Maestro/Playwright，不调大模型**——执行必须是确定性的、分钟级的、零 API 成本的；AI 只在生成和修复这两个时刻介入。这条守住了，这套东西才可持续。

## 首次在项目里落地时一并做的事

**① 把两个闸门脚本拷进仓库**，CI 和 git hook 需要一个不依赖 skill 安装位置的稳定路径：

```bash
mkdir -p .smoke/scripts
cp scripts/check_registry.py scripts/check_test_integrity.py .smoke/scripts/
```

（`.smoke/registry.json`、`.smoke/flows/`、`.smoke/scripts/` 进 git；`.smoke/runs/`、`.smoke/frames/` 进 `.gitignore`。）

**② 把完整性闸门挂进 pre-commit hook**，让它不依赖任何人记得去跑：

```bash
cp assets/pre-commit-hook.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

**③ 落 CI 配置**：`assets/smoke-ci.yml` 已内置两个闸门步骤，按项目改 Flutter 版本和 secrets 即可。

## 增量模式

不是首次运行时（`.smoke/registry.json` 已存在），只处理 diff：对 `.smoke/state.json` 里记录的上次冒烟 commit（`run_smoke.sh` 自动写入）跑 `git diff --name-only -- lib/`，改动涉及的页面才重新走 Phase 2–4，其余用例直接复用。全量重建每次都会重排 identifier，制造无意义的 diff，也会让用例的历史变得不可追溯。

## 参考文件

- **references/**：test-integrity（闸门原理与放行流程）· vision-fallback（L3 规程）·
  maestro-flutter（Maestro 语法、Flutter/环境坑位）· flutter-web（Web 两路线）·
  dev-loop（开发伴随模式）· journey-selection（选用例模板、定向用例写法）· triage（失败三分类）
- **scripts/**：scan_app（静态扫描）· check_registry（选择器契约闸门）·
  check_test_integrity（防弱化闸门，跨语言）· select_flows（定向选用例）·
  shard_flows（按资源写冲突分车道）· device_pool（跨会话模拟器所有权）·
  screen（截图/点击/差分/异常采集）· run_smoke（三端执行入口）
- **assets/**：flow-template.yaml · web-smoke/（Playwright 模板）· smoke-ci.yml · pre-commit-hook.sh
- **tests/**：test_gates.py + test_screen.py + test_device_pool.py + test_shard_flows.py，
  改脚本前先跑；纪律见 tests/README.md
