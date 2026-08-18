# Maestro × Flutter 参考

## 目录
- [定位机制](#定位机制)
- [常用命令](#常用命令)
- [断言写法](#断言写法)
- [Flutter 专属坑位](#flutter-专属坑位)
- [环境变量与测试数据](#环境变量与测试数据)

## 定位机制

Maestro 不读 Flutter 的 widget 树，读的是**系统语义树**（Semantics Tree）。这决定了：

- `Key('login_btn')` 对 Maestro **不可见**，别指望它
- `Semantics(identifier: 'login_btn')` → `id: login_btn`，最稳
- `semanticLabel: '登录'` 或 `Text('登录')` → `text: 登录`，会随文案变更失效
- 没有语义信息的自绘控件（`CustomPaint`、`Canvas`）Maestro 看不见，只能按坐标点，脆
- 好处是不依赖 widget 树内部结构，Flutter 升版本不会把测试碎掉

`identifier` 需要 Flutter 3.19+。更早的版本只能退回 `semanticLabel`，代价是无法和展示文案解耦。

## 常用命令

```yaml
appId: com.example.app        # 必需，文件头
---
- launchApp:
    clearState: true          # 清数据，保证起始态干净
    permissions: { all: allow }  # 跳过权限弹窗，冒烟阶段不测权限流
- tapOn: { id: "login_submit_btn" }
- inputText: "${TEST_PHONE}"
- inputRandomEmail                # 需要唯一值时用，避免重复注册失败
- scrollUntilVisible:
    element: { id: "home_feed_last_item" }
    direction: DOWN
    timeout: 10000
- waitForAnimationToEnd: { timeout: 5000 }
- pressKey: Back
- stopApp
- runFlow: { file: ../subflows/login.yaml }   # 抽公共前置，别复制粘贴
```

## 断言写法

```yaml
- assertVisible: { id: "home_feed_list" }
- assertNotVisible: { text: ".*(错误|失败|Error|异常).*" }   # 正则，兜住错误提示
- assertVisible: { id: "post_item", index: 0 }              # 列表至少渲染一项
- assertTrue: ${output.count > 0}
```

**冒烟阶段推荐的断言组合**（每条 flow 至少各一条）：

1. 到达：目标页锚点可见
2. 无错：错误文案不可见
3. 有内容：列表/详情非空，不是永久 loading 骨架

## 埋点位置规则（sliver 等特殊组件）

`Semantics` 是 box widget，**不能直接包 sliver**（`SliverList`、`SliverAppBar` 等）——
box/sliver 布局协议不同，运行时直接断言崩溃（"expected RenderSliver but received RenderBox"）。

| 想标识的东西 | 埋点打在哪 |
|---|---|
| 列表里的每一项 | `itemBuilder` 内部的 item（box），这正是测试需要的粒度 |
| 整个信息流/列表容器 | `CustomScrollView` 自身外面（它整体是 box） |
| `SliverAppBar` | 包它的 `title:` / `actions:` 子组件 |
| sliver 间的普通内容 | `SliverToBoxAdapter` 的 child |

一句话：**埋点永远打在 box widget 上，sliver 层不埋。**
`ListTile`/`ElevatedButton` 自带语义合并，外面再包 `Semantics(identifier:)` 不冲突；
但不要顺手加 `container:`/`excludeSemantics:` 等结构性属性，那会真的改变无障碍行为。

## 环境坑位（Phase 0 自检时处理）

**Java 缺失。** `maestro --version` 只是 wrapper，能通过不代表有 Java；真执行才报 `Unable to locate a Java Runtime`。没有 JDK 时优先借用 Android Studio 自带的（`run_smoke.sh` 已自动做这个兜底），别为此装几百兆 JDK：

```bash
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
```

**iOS 明文 HTTP（ATS）。** App 的 API base 是 `http://`（本地后端、测试环境）且要跑 iOS 时，查 `ios/Runner/Info.plist` 有没有 `NSAppTransportSecurity` 例外——没有的话 iOS 会**静默拒绝**所有明文请求：没有崩溃没有异常，登录点了没反应，极难排查。秒级静态检查：

```bash
grep -rl 'http://' lib/ && plutil -p ios/Runner/Info.plist | grep -q NSAppTransportSecurity || echo "缺 ATS 例外"
```

修法用 `NSAllowsLocalNetworking`（localhost）+ `NSExceptionDomains` 指定具体域名，**不要用 `NSAllowsArbitraryLoads`**（全局关掉 HTTPS 要求，提审会被问）。

**iOS 模拟器软键盘遮挡**：见下面「Flutter 专属坑位」对应条目。

## Flutter 专属坑位

**动画抢跑。** Flutter 页面切换有 300ms 左右的过渡，`tapOn` 后立刻断言经常扑空。在导航后加 `waitForAnimationToEnd`，别用 `sleep` 硬等（会让整套变慢且仍不稳）。

**列表懒加载。** `ListView.builder` 只渲染可视区，语义树里也只有可视项。断言列表内容用 `scrollUntilVisible`，不要直接 `assertVisible` 一个屏幕外的元素。

**输入框焦点。** `inputText` 前必须先 `tapOn` 目标输入框，Maestro 不会自动聚焦。连续填两个字段时每个都要先 tap。

**inputText 是追加不是覆盖。** 字段里已有内容（开发期预填的演示账号、记住的上次输入）时，新值拼在后面再被 maxLength 截断，产生"格式不正确"这类**看似业务 bug 的静默失败**——不报错、不崩溃，极易被误判成 APP_DEFECT。规矩：每个 `inputText` 前固定加 `eraseText`；表单类用例调试时输入前先截一张图确认字段初始状态。

**hideKeyboard 对 Flutter 无效。** Flutter 输入是自绘的，没有标准的 dismiss action，`hideKeyboard` 必报 `Couldn't hide the keyboard`。收键盘用 `tapOn` 空白区域锚点或 `pressKey: Back`（Android）。

**iOS 模拟器软键盘会挡住下方控件**，tap 落不到目标。测试前在模拟器菜单开启 I/O → Keyboard → Connect Hardware Keyboard（或 `defaults write com.apple.iphonesimulator ConnectHardwareKeyboard -bool true` 后重启模拟器），软键盘就不弹了。

**首帧空白。** `launchApp` 后 Flutter 引擎有初始化时间，第一个断言前加 `waitForAnimationToEnd` 或给足 timeout，否则会误报"启动失败"。

**Hero 动画与 WebView。** WebView 内部内容 Maestro 只能看到容器，内部元素定位不可靠。冒烟阶段涉及 WebView 的流程（第三方登录、支付页）只断言"WebView 容器出现"，不深入。

**iOS 权限弹窗。** 用 `permissions: { all: allow }` 一次性放行。要专门测权限拒绝路径就不该放在冒烟里。

## 环境变量与测试数据

flow 文件里只写 `${VAR}` 占位，值一律运行时注入——**不要写 `env:` 块把值落进文件**
（flow 要进 git，写进去就是泄漏，`check_registry.py` 也会拦硬编码凭据）：

```yaml
- tapOn: { id: "login_phone_input" }
- eraseText
- inputText: "${TEST_PHONE}"
```

```bash
bash run_smoke.sh --platform android --env TEST_PHONE=$SMOKE_PHONE --env TEST_OTP=$SMOKE_OTP
# 或直接: maestro test .smoke/flows -e TEST_PHONE=... -e TEST_OTP=...
```

固定验证码这类测试后门，用 `--dart-define=SMOKE_TEST=true` 在编译期开关控制，确保不进生产包。
