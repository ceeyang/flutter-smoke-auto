#!/usr/bin/env bash
# 构建 → 安装/启动 → 执行冒烟 → 收集产物（Android / iOS / Web 三端）
#
# 用法:
#   bash run_smoke.sh --platform android --flows .smoke/flows --out .smoke/runs/$(date +%s)
#   bash run_smoke.sh --platform android --changed    # 定向：只跑 git 改动影响的用例+冷启动（日常默认）
#   bash run_smoke.sh --platform android --only login # 定向：只跑名字/内容匹配关键词的用例+冷启动
#   bash run_smoke.sh --platform ios --skip-build
#   bash run_smoke.sh --platform web                  # 需要 .smoke/flows/web/ 下有 playwright spec
#   bash run_smoke.sh --platform android --build-mode release
#   bash run_smoke.sh --platform android --env TEST_PHONE=13800000000 --env TEST_OTP=000000
#
# 改小功能后的日常验证用 --changed / --only，几分钟内完事；
# 不带范围参数 = 全量，留给提测/发版和 /smoke-* 命令。
#
# 构建模式默认值（可用 --build-mode 覆盖）：
#   android → profile   保住 VM Service，L1 层（marionette/widget tree）才可用
#   ios     → debug     模拟器不支持 profile/release 注入调试服务
#   web     → release   语义树由 SMOKE_TEST 开关在代码里 ensureSemantics() 打开
# 发版前建议再用 --build-mode release 跑一遍 android，测真实产物。

set -uo pipefail

PLATFORM="android"
FLOWS=".smoke/flows"
OUT=".smoke/runs/$(date +%s)"
SKIP_BUILD=0
BUILD_MODE=""
DART_DEFINES="--dart-define=SMOKE_TEST=true"
MAESTRO_ENV=()
WEB_PORT=8788

ATTACH=0
ONLY_KW=""
CHANGED=0
SHUTDOWN=""   # iOS：跑完是否 simctl shutdown。空=自动（全量关、定向不关），--shutdown/--no-shutdown 显式覆盖

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform)    PLATFORM="$2"; shift 2 ;;
    --flows)       FLOWS="$2"; shift 2 ;;
    --out)         OUT="$2"; shift 2 ;;
    --skip-build)  SKIP_BUILD=1; shift ;;
    --attach)      ATTACH=1; SKIP_BUILD=1; shift ;;   # 对已运行的 App 直接跑 flow（开发伴随快验）
    --only)        ONLY_KW="$2"; shift 2 ;;           # 定向：关键词圈用例
    --changed)     CHANGED=1; shift ;;                # 定向：git 改动推导用例
    --build-mode)  BUILD_MODE="$2"; shift 2 ;;
    --dart-define) DART_DEFINES="$DART_DEFINES --dart-define=$2"; shift 2 ;;
    --env)         MAESTRO_ENV+=("-e" "$2"); shift 2 ;;
    --web-port)    WEB_PORT="$2"; shift 2 ;;
    --shutdown)    SHUTDOWN=1; shift ;;
    --no-shutdown) SHUTDOWN=0; shift ;;
    *) echo "未知参数: $1"; exit 2 ;;
  esac
done

# ── 定向选用例：把范围收窄到受影响的用例 + 冷启动锚点 ──
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PW_FILTER=()
if [[ -n "$ONLY_KW" || "$CHANGED" == "1" ]]; then
  SEL_ARGS=(--flows "$FLOWS")
  [[ "$PLATFORM" == "web" ]] && SEL_ARGS+=(--web)
  if [[ -n "$ONLY_KW" ]]; then
    SEL_ARGS+=(--keyword "$ONLY_KW")
  else
    SEL_ARGS+=(--changed --registry "$(dirname "$FLOWS")/registry.json")
  fi
  SELECTED=$(python3 "$SCRIPT_DIR/select_flows.py" "${SEL_ARGS[@]}") || exit 2
  [[ -z "$SELECTED" ]] && { echo "定向选择结果为空，检查 --only 关键词或 flow 目录"; exit 2; }
  echo "定向执行，选中用例："; echo "$SELECTED" | sed 's/^/  /'
  if [[ "$PLATFORM" == "web" ]]; then
    while IFS= read -r f; do PW_FILTER+=("$(basename "$f")"); done <<< "$SELECTED"
  else
    # 拷到 flows 的兄弟目录，../subflows 相对引用保持可解析；maestro 跑这个目录。
    # 按平台分目录：smoke-all 三端并行时共用一个目录会互相 rm -rf
    SEL_DIR="$(dirname "$FLOWS")/flows-selected-$PLATFORM"
    rm -rf "$SEL_DIR" && mkdir -p "$SEL_DIR"
    while IFS= read -r f; do cp "$f" "$SEL_DIR/"; done <<< "$SELECTED"
    FLOWS="$SEL_DIR"
  fi
fi

if [[ "$ATTACH" == "1" ]]; then
  echo "attach 模式：跳过构建安装，直接测当前已运行的 App。"
  echo "注意：它可能是 debug/热重载态，结论只用于快验，不写进正式报告。"
fi

# 环境里已有的测试凭据自动透传（也可用 --env 显式给）
for var in TEST_PHONE TEST_OTP TEST_EMAIL TEST_PASSWORD; do
  if [[ -n "${!var:-}" ]]; then
    MAESTRO_ENV+=("-e" "$var=${!var}")
  fi
done

if [[ -z "$BUILD_MODE" ]]; then
  case "$PLATFORM" in
    android) BUILD_MODE="profile" ;;
    ios)     BUILD_MODE="debug" ;;
    web)     BUILD_MODE="release" ;;
  esac
fi

mkdir -p "$OUT"/{artifacts,logs}
OUT_ABS="$(cd "$OUT" && pwd)"
echo "平台=$PLATFORM  构建=$BUILD_MODE  flows=$FLOWS  产物=$OUT"

command -v flutter >/dev/null || { echo "缺少 flutter"; exit 1; }

write_state() {
  # 增量模式和完整性闸门的默认基准都靠它记住「上次跑到哪」，不要删。
  # 先写临时文件再 mv（原子）+ 平台专属副本：smoke-all 三端并行时不互相截断
  mkdir -p .smoke
  local tmp=".smoke/.state.$PLATFORM.$$.tmp"
  cat > "$tmp" <<EOF
{
  "commit": "$(git rev-parse HEAD 2>/dev/null || echo unknown)",
  "platform": "$PLATFORM",
  "build_mode": "$BUILD_MODE",
  "exit_code": $1,
  "out": "$OUT",
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
  cp "$tmp" ".smoke/state-$PLATFORM.json"
  mv "$tmp" .smoke/state.json
}

# ───────────────────────── Web ─────────────────────────
if [[ "$PLATFORM" == "web" ]]; then
  WEB_SPEC_DIR="$FLOWS/web"
  if ! ls "$WEB_SPEC_DIR"/*.spec.* >/dev/null 2>&1; then
    echo "没有找到 Web spec（$WEB_SPEC_DIR/*.spec.ts）。"
    echo "两条路："
    echo "  W1（推荐，确定性、可进 CI）: 按 assets/web-smoke/ 模板生成 playwright spec"
    echo "  W2（兜底，agent 驱动）:      用 chrome-devtools MCP 按 .smoke/plan.md 手动执行，"
    echo "                               结果写进报告，但注明不可进 CI、不可复跑"
    exit 1
  fi
  command -v npx >/dev/null || { echo "缺少 node/npx（Web 端需要 Playwright）"; exit 1; }

  if [[ "$SKIP_BUILD" == "0" ]]; then
    echo "构建 web ($BUILD_MODE)..."
    flutter build web --$BUILD_MODE $DART_DEFINES 2>&1 | tee "$OUT/logs/build.log"
    [[ -d build/web ]] || { echo "构建产物未找到，见 $OUT/logs/build.log"; exit 1; }
  fi

  echo "起本地服务 :$WEB_PORT 并执行 playwright..."
  python3 -m http.server "$WEB_PORT" --directory build/web >"$OUT/logs/server.log" 2>&1 &
  SERVER_PID=$!
  trap 'kill $SERVER_PID 2>/dev/null' EXIT
  sleep 1
  # 绑定失败时进程已死，playwright 会打到占着端口的别的服务（典型：dev-loop
  # 还开着 flutter run），测的是热重载态却当 release 结果——必须硬停
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "本地服务启动失败，端口 $WEB_PORT 可能被占用（dev-loop 的 flutter run 还开着？）。"
    echo "详见 $OUT/logs/server.log。先关掉占用进程，或用 --web-port 换端口。"
    exit 1
  fi

  ( cd "$WEB_SPEC_DIR" && \
    SMOKE_BASE_URL="http://localhost:$WEB_PORT" npx playwright test \
      ${PW_FILTER[@]+"${PW_FILTER[@]}"} \
      --reporter=list 2>&1 | tee "$OUT_ABS/logs/playwright.log"
    exit ${PIPESTATUS[0]} )
  STATUS=$?
  write_state $STATUS
  [[ $STATUS -eq 0 ]] && echo "Web 全部通过。" || echo "Web 有失败，日志: $OUT/logs/playwright.log"
  exit $STATUS
fi

# ───────────────────────── Android / iOS ─────────────────────────
command -v maestro >/dev/null || { echo "缺少 maestro: curl -fsSL https://get.maestro.mobile.dev | bash"; exit 1; }

# Maestro 底层要 Java。maestro --version 是 wrapper 能跑，真执行才炸，所以这里显式查。
if ! java -version >/dev/null 2>&1; then
  AS_JBR="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
  if [[ -d "$AS_JBR" ]]; then
    export JAVA_HOME="$AS_JBR"
    export PATH="$JAVA_HOME/bin:$PATH"
    echo "未找到系统 Java，借用 Android Studio 自带 JBR: $JAVA_HOME"
  else
    echo "缺少 Java 运行时（Maestro 需要）。装 JDK 或安装 Android Studio 后借用其 JBR。"
    exit 1
  fi
fi

# 显式选定设备。多平台共存（Android 模拟器 + iOS 模拟器同时在）时，
# Maestro 不指定设备可能选错端，错误信息却是误导性的 "Package X is not installed"。
DEVICE_ARGS=()
if [[ "$PLATFORM" == "android" ]]; then
  SERIAL=$(adb devices | awk 'NR>1 && $2=="device"{print $1; exit}')
  if [[ -z "$SERIAL" ]]; then
    echo "没有可用的 Android 设备/模拟器。启动一个再试：emulator -avd <name>"
    exit 1
  fi
  DEVICE_ARGS=(--device "$SERIAL")
  echo "Android 设备: $SERIAL"
else
  UDID=$(xcrun simctl list devices booted -j 2>/dev/null | python3 -c "
import json, sys
devs = [d for v in json.load(sys.stdin)['devices'].values() for d in v]
print(devs[0]['udid'] if devs else '')")
  if [[ -z "$UDID" ]]; then
    echo "没有已启动的 iOS 模拟器。启动一个再试：open -a Simulator"
    exit 1
  fi
  DEVICE_ARGS=(--udid "$UDID")
  echo "iOS 模拟器: $UDID"
fi

# ── iOS 单驱动闸门 ──
# SpringBoard 的 XCTAutomationSession init 有并发竞态（Apple bug，见 PITFALLS
# 2026-08-18）：残留的自动化会话叠上新会话，模拟器内 SpringBoard 直接段错误。
# 所以每次起 maestro 前、以及本脚本退出时，都把旧驱动清干净——重试永远从零开始。
kill_ios_drivers() {
  pkill -f "maestro-driver-ios" 2>/dev/null
  pkill -f "xcodebuild.*maestro" 2>/dev/null
  pkill -f "idb_companion" 2>/dev/null
  return 0
}
if [[ "$PLATFORM" == "ios" ]]; then
  if kill_ios_drivers; then sleep 1; fi
  # 全量验收跑完默认关掉模拟器（会话状态清零）；定向验证保留 boot 态省时间
  if [[ -z "$SHUTDOWN" ]]; then
    if [[ -z "$ONLY_KW" && "$CHANGED" == "0" && "$ATTACH" == "0" ]]; then SHUTDOWN=1; else SHUTDOWN=0; fi
  fi
fi

if [[ "$SKIP_BUILD" == "0" ]]; then
  echo "构建中 ($BUILD_MODE)..."
  if [[ "$PLATFORM" == "android" ]]; then
    flutter build apk --$BUILD_MODE $DART_DEFINES 2>&1 | tee "$OUT/logs/build.log"
    APK=$(find build/app/outputs -name "*.apk" -newer pubspec.yaml -exec ls -t {} + 2>/dev/null | head -1)
    [[ -z "$APK" ]] && { echo "构建产物未找到，见 $OUT/logs/build.log"; exit 1; }
    adb install -r "$APK" 2>&1 | tee -a "$OUT/logs/build.log"
  else
    flutter build ios --simulator --$BUILD_MODE $DART_DEFINES 2>&1 | tee "$OUT/logs/build.log"
    APP=$(find build/ios/iphonesimulator -maxdepth 1 -name "*.app" | head -1)
    [[ -z "$APP" ]] && { echo "构建产物未找到，见 $OUT/logs/build.log"; exit 1; }
    xcrun simctl install booted "$APP" 2>&1 | tee -a "$OUT/logs/build.log"
  fi
fi

# 采集设备日志（失败分诊要用）
if [[ "$PLATFORM" == "android" ]]; then
  adb logcat -c
  adb logcat > "$OUT/logs/device.log" 2>&1 &
  LOG_PID=$!
else
  xcrun simctl spawn booted log stream --level debug > "$OUT/logs/device.log" 2>&1 &
  LOG_PID=$!
fi
if [[ "$PLATFORM" == "ios" ]]; then
  trap '[[ -n "${LOG_PID:-}" ]] && kill $LOG_PID 2>/dev/null; kill_ios_drivers' EXIT
else
  trap '[[ -n "${LOG_PID:-}" ]] && kill $LOG_PID 2>/dev/null' EXIT
fi

echo "执行 flow..."
# 空数组展开写 ${arr[@]+...}：macOS 系统 bash 3.2 下 set -u + 空数组直接报
# "unbound variable"（bash 4.4 才修），不带 --env 或全量跑就会踩到
maestro ${DEVICE_ARGS[@]+"${DEVICE_ARGS[@]}"} test "$FLOWS" \
  --format junit \
  --output "$OUT/artifacts/results.xml" \
  --debug-output "$OUT/artifacts" \
  ${MAESTRO_ENV[@]+"${MAESTRO_ENV[@]}"} \
  2>&1 | tee "$OUT/logs/maestro.log"
STATUS=${PIPESTATUS[0]}

{
  echo "platform=$PLATFORM"
  echo "build_mode=$BUILD_MODE"
  echo "exit_code=$STATUS"
  echo "commit=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$OUT/run-meta.txt"
write_state $STATUS

echo
if [[ $STATUS -eq 0 ]]; then
  echo "全部通过。产物在 $OUT"
else
  echo "有失败。分诊材料："
  echo "  Maestro 日志: $OUT/logs/maestro.log"
  echo "  设备日志:     $OUT/logs/device.log"
  echo "  截图/层级:    $OUT/artifacts/"
  echo "按 references/triage.md 做三分类后再决定改不改测试。"
fi
if [[ "$PLATFORM" == "ios" && "$SHUTDOWN" == "1" ]]; then
  echo "关闭模拟器（清零自动化会话状态；定向验证或 --no-shutdown 时保留 boot 态）..."
  xcrun simctl shutdown "$UDID" 2>/dev/null
fi
exit $STATUS
