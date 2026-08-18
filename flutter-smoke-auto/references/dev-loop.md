# 开发伴随模式（边改边看）

这不是测试，是开发反馈回路：改动 → 秒级看到效果 → 验证改动点 → 继续改。
不建 `.smoke/`、不生成 flow、不出报告。结束时提议切换到正式冒烟收口。

## 启动：后台 flutter run + PID 文件

PID 文件是可脚本化热重载的关键——agent 没法往交互终端里敲 `r`，
但可以给进程发信号：

```bash
# Web：用 web-server 设备，浏览器交给 chrome-devtools MCP 控制
flutter run -d web-server --web-port 8788 \
  --pid-file /tmp/flutter-dev.pid --dart-define=SMOKE_TEST=true

# Android / iOS 模拟器（flutter devices 拿 deviceId）
flutter run -d <deviceId> \
  --pid-file /tmp/flutter-dev.pid --dart-define=SMOKE_TEST=true
```

（用后台方式运行并保留输出日志，编译错误会打在这里。）

## 每次改完代码的刷新

```bash
kill -USR1 $(cat /tmp/flutter-dev.pid)   # 热重载：保留页面状态，亚秒级
kill -USR2 $(cat /tmp/flutter-dev.pid)   # 热重启：改了 main/路由/全局状态/常量时用
```

改动没生效的判断顺序：先看 flutter run 日志有没有编译错误 →
USR1 对这类改动是否本来就无效（新增字段初始化、const 变更）→ 换 USR2 →
Web 端老版本 Flutter 不支持热重载，USR2 后再刷新浏览器。

## 查看与验证

**Web —— chrome-devtools MCP：**

1. `new_page` / `navigate_page` 打开 `http://localhost:8788`
2. 改完代码 → 发信号 → `navigate_page` 刷新（热重载生效时不用刷新，直接看）
3. 验证改动点，按成本从低到高：
   - `list_console_messages` —— 有没有新报错（免费，但 console 干净≠UI 对）
   - `take_snapshot` —— 无障碍树里查结构和 `flt-semantics-identifier`
   - `take_screenshot` —— 视觉确认布局/样式改动
4. 需要交互才能到达的状态：`click` / `fill` 直接驱动到位再看

**移动端：**

- 装了 marionette → L1 `get_interactive_elements` 直接查元素状态，零截图
- 没装 → `scripts/screen.py` 三件套：`shot`（截图）、`inspect`（红屏/白屏预筛）、
  `errors`（logcat 异常），都是零 token 预筛，过滤不掉的再看图
- 想对正在 `flutter run` 的 App 直接跑几条 flow 验证：
  `bash scripts/run_smoke.sh --platform android --attach`——跳过构建安装，
  直接测运行中的 App。秒级，但它测的是 debug/热重载态，结论只用于快验不进报告
- `flutter run` 的日志窗口本身是重要的诊断源：请求被拒（如 iOS ATS 拦明文 HTTP）
  这类"没有崩溃没有异常"的问题，往往只有这里能看出"不是崩了，是被拒了"

## 纪律

- **每次改动验证的是改动点本身**，不是"页面还在"。改的是按钮间距就截图看间距，
  改的是提交逻辑就驱动到提交后的状态——"没报错"不构成验证。
- 本模式的一切结论**不写进报告**。用户说"完成/可以了/就这样"时，
  主动提议：「切到正式冒烟跑一轮？构建安装包验证真实产物，几分钟。」
- 会话结束或切换模式前，清理：kill flutter run 进程、删 PID 文件、关掉开的浏览器页。
- debug 模式性能（JIT、首帧慢）不代表产物，别在本模式下下"性能没问题"的结论。
