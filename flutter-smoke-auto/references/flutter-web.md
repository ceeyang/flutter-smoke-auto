# Flutter Web 冒烟测试

Web 端与移动端共用同一份 `plan.md` 和 `registry.json`。能共用的原因只有一个：
Flutter 3.19+ 的 `Semantics(identifier:)` 是跨端的——
Android 上渲染成 `resource-id`，iOS 上是 `accessibilityIdentifier`，
**Web 上是 DOM 属性 `flt-semantics-identifier`**。Phase 2 埋一次点，三端都认。

## 前提：语义树必须显式打开

Flutter Web 默认**不生成**语义 DOM（出于性能），页面上只有一个隐藏的
"Enable accessibility" 占位按钮。不处理这一步，所有 `byId` 都找不到元素。

正确做法是在代码里用编译开关打开（Phase 2 一并做，三端都受益）：

```dart
// main.dart，runApp 之前
if (const bool.fromEnvironment('SMOKE_TEST')) {
  SemanticsBinding.instance.ensureSemantics();
}
```

构建时 `--dart-define=SMOKE_TEST=true`（`run_smoke.sh` 默认带）。
不要在测试里去点那个占位按钮——它的位置和实现是引擎内部细节，会变。

## 两条执行路线

**W1 — Playwright（默认，确定性，进 CI）**

模板在 `assets/web-smoke/`（config + helpers + spec 骨架），复制到
`.smoke/flows/web/` 后按 plan.md 填充。规则与移动端一致：

- 业务元素只走 `byId()`（内部是 `[flt-semantics-identifier="..."]`），
  `check_registry.py` 会扫 spec 里的 `byId('...')` 并对契约表校验
- `getByText` 定位业务元素 = 移动端的 `text:` 选择器，同样被拦；
  唯一豁免是 `expectNoErrorText`（错误提示没有 id，只能按文案兜）
- 单条 90 秒、串行执行、不静默 retry，与冒烟纪律一致

首次准备（在 `.smoke/flows/web/` 下）：

```bash
npm init -y && npm i -D @playwright/test && npx playwright install chromium
```

**W2 — chrome-devtools MCP（兜底，agent 驱动）**

项目没有 node 环境、或 Playwright 装不上时，agent 直接用 chrome-devtools MCP
（`navigate_page` / `take_snapshot` / `click` / `evaluate_script`）按
`.smoke/plan.md` 逐条执行，等价于移动端的 L2/L3 层：

1. `navigate_page` 打开本地服务（`python3 -m http.server 8788 --directory build/web`）
2. `evaluate_script` 确认 `document.querySelector('[flt-semantics-identifier]')` 非空
   （语义树没开就先回去修 SMOKE_TEST 开关，不要靠坐标点击硬跑）
3. 定位优先 `document.querySelector('[flt-semantics-identifier="<id>"]')`，
   拿到坐标再 `click`；`take_snapshot` 的无障碍树等价于移动端 L2
4. 每步之后照抄 L3 纪律：验证页面确实变化，连续两次无变化判该步失败
5. 结果写进报告，但**必须注明是 agent 手工执行**：不可复跑、不进 CI，
   只作为"今天这个版本能不能用"的一次性结论

W2 跑通之后，正确的下一步是补出 W1 的 spec，而不是把 W2 当常态——
和移动端"L3 反复出现就该回去补埋点"是同一条纪律。

## Web 专属坑位

**渲染器与语义 DOM 无关。** CanvasKit/skwasm 把像素画在 canvas 上，但语义树
是独立的 DOM 层（`flt-semantics-host`），`byId` 定位不受渲染器影响。
受影响的是视觉截图对比——canvas 内容对 DOM diff 不可见。

**语义节点是透明覆盖层。** 语义元素本身宽高可能为 0 或透明，
`toBeVisible()` 可能误判，定位后断言用 `toBeAttached()`；
需要"真的可见"语义时，断言目标页锚点 + 无错文案组合即可。

**懒加载列表同移动端。** 语义树里只有可视区的项，屏幕外元素要先滚动。
Playwright 里对语义节点用 `scrollIntoViewIfNeeded()`。

**路由。** `launchApp(page, '/login')` 需要 App 配了 web 路由策略
（path 或 hash）。没配 deep link 的项目从 `/` 进入后按导航走。

**文本输入。** 语义 input 节点接键盘事件：先 `byId(...).click()` 聚焦
再 `page.keyboard.type(...)`。对语义节点用 `fill()` 不一定生效。

**iframe/WebView 同移动端纪律。** 第三方登录、支付页只断言容器出现，不深入。
