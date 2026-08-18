# L3 视觉兜底层

当 widget tree（marionette）和系统无障碍树（mobile-mcp）都拿不到目标元素时，
退到这一层：截图 → 模型识别 → 坐标点击 → 验证。它总是可用，包括 release 包，
代价是慢和贵。

## 三级降级，按步而非按流程

关键规则：**降级是每一步单独决策的，不是整条用例锁死在某一层。**

```
这一步要点的元素：
  在 get_interactive_elements 里？  → L1，按 key/text 点，零截图
  在无障碍树里？                    → L2，按 accessibility id 点，零截图
  都没有（自绘控件、Canvas、图表）→ L3，截图 + 模型定位
```

一条 20 步的用例通常只有 1–3 步需要 L3。全程 L3 意味着成本和不确定性涨十倍，
而且往往说明这个 App 的可访问性有问题——那本身就是个该报的缺陷。

## 坐标换算：最容易错的一环

模型看到的是缩放后的图，设备接受的是物理像素。直接把模型给的坐标喂给
`adb shell input tap` 必然偏，而且偏得很稳定、很隐蔽——点到的往往是另一个
控件，流程看起来"走通了"，其实走的是另一条路。

规程：

1. `screen.py shot` 同时输出图像尺寸、设备尺寸和缩放比，写进 `NNNN.json`
2. 模型**只在图像坐标系里说话**，永远不直接给设备坐标
3. `screen.py tap --space image --ref NNNN.json` 负责换算，越界直接报错

不要自己在 prompt 里做乘除法。模型算错了不会告诉你。

## 点击后必须验证

纯视觉模式最危险的失败不是点不中，是**点不中但继续往下走**。模型看到下一张
截图还是原来那页，会倾向于解释成"页面还没加载完"，然后重试、再解释、
最终产出一份声称通过的报告。

所以每次点击后固定做一次差分，这一步零 token：

```bash
python screen.py shot --out .smoke/frames          # → 0007.json
python screen.py tap --x 412 --y 780 --ref .smoke/frames/0006.json
sleep 1
python screen.py shot --out .smoke/frames          # → 0008.json
python screen.py diff .smoke/frames/0006.png .smoke/frames/0008.png
```

- `no_change` → 点空了。**不要重试同一个坐标**，重新截图重新定位；连续两次
  no_change 就判定这一步失败，记录并停止这条用例
- `minor` → 只有局部变化，可能是按钮按下态或 toast，需要模型确认
- `changed` → 页面切换了，继续

## 没有 VM Service 时的 oracle

这是 L3 最大的短板：拿不到异常流和 HTTP 状态。剩下三个信号，按可靠性排：

**1. logcat / os_log**（最可靠，零 token）

```bash
python screen.py errors --since 30
```

Flutter 的框架异常、`RenderFlex overflowed`、未捕获异常都会打到系统日志。
抓不到的是：被 `catch` 吞掉的异常、HTTP 状态码、以及被业务代码降级成
"暂无数据"的接口错误。

**2. 视觉异常预筛**（零 token）

```bash
python screen.py inspect .smoke/frames/0008.png
```

识别红屏（debug 下的 ErrorWidget）、白屏、近乎空白页。
`ok: true` 只代表"不是明显异常"，不代表页面正确——后者仍要模型判断。

**3. 模型判断**（贵，最后用）

只在前两层都没结论时问模型，且问法要具体：
不要问"这个页面对吗"，要问"这个页面上有没有出现『我的评价』这四个字"。
开放式提问会得到过于宽容的回答。

## 成本控制

一次 900px 长边的截图大约 1–1.5k token。20 步用例加上验证帧和重试，
轻松到 50 帧、几万 token。四条能显著降本的做法：

| 做法 | 省多少 | 代价 |
|---|---|---|
| 长边压到 900px（默认） | 相比原图省 60%+ | 小于 12px 的字识别率下降 |
| 只在预期会变化的动作后截图 | 省 30–40% | 漏掉中间态 |
| 先 `inspect` 预筛，正常才给模型看 | 省掉大量正常帧 | 无 |
| 用 deep link 直达而不是逐页点 | 省掉整段前置 | 需要 App 配了 scheme |

deep link 是这里性价比最高的一条。20 步的用例配了 deep link 之后可能只剩 4 步。

```bash
python screen.py deeplink --url "myapp://review/create"
```

## 中文输入

`adb shell input text` 不支持非 ASCII，中文会变成空。三个选择：

1. **测试数据用 ASCII**（推荐）——冒烟用例的输入值是你自己定的，用
   `smoke test 20260814` 完全够用，没必要非填中文
2. 装 ADBKeyBoard 输入法，用 broadcast 发送中文
3. 走 L1/L2 的 `enter_text`，它们经由 Flutter 内部设值，没有这个限制

iOS 模拟器用 `idb ui text`，支持中文。

## iOS 的额外前提

`xcrun simctl` **不能注入触摸事件**，这是个常被忽略的事实。iOS 模拟器上做
坐标点击需要 `idb`：

```bash
brew tap facebook/fb && brew install idb-companion && pipx install fb-idb
```

不想装 idb 的话，用 `mobile-mcp` 或 Maestro 当输入后端——它们内部已经解决了
这个问题。真机则需要 WebDriverAgent，配置成本更高，冒烟阶段建议只用模拟器。

**iOS 后端互斥（硬规则）：同一台模拟器同一时刻只允许一个自动化客户端。**
Maestro、idb、mobile-mcp/WebDriverAgent 都会向模拟器建立 XCTest 自动化会话，
而 `XCTAutomationSession` 的初始化存在并发竞态（Apple bug）——两个会话同时建立
会把模拟器内的 SpringBoard 直接打崩（EXC_BAD_ACCESS，模拟器自动重启、用例莫名中断）。
所以：

- 一轮之内选定一个输入后端就用到底，不要 idb 和 Maestro 混着点；
- 换后端（或修复轮里用 idb 探查页面）之前，先确认上一个驱动已退出：
  `pgrep -f "maestro-driver-ios|idb_companion" || echo clean`，有残留先 kill；
- `run_smoke.sh` 起 maestro 前会自动清残留驱动、全量跑完自动 shutdown 模拟器——
  绕过它手动跑 maestro 时，这两步要自己做。

## 什么时候该停下来改 App 而不是继续烧 token

如果某个元素反复要靠 L3 才点得到，正确的反应不是加大 token 预算，而是
去给它补一个 `Semantics(identifier:)`。一次五分钟的代码改动，换来这个元素
在之后所有用例里都走 L1。

判断线：同一个元素在三条以上用例里都需要 L3 → 记进报告的「可测性缺口」，
建议补埋点。视觉兜底是保证跑得起来的下限，不是长期方案。
