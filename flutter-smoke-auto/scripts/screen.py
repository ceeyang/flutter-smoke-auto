#!/usr/bin/env python3
"""L3 视觉兜底层：截图 → 坐标换算 → 点击 → 变化验证 → 错误采集。

不依赖 VM Service、不依赖埋点、release 包也能用。
唯一依赖：adb（Android）或 idb（iOS 模拟器）+ Pillow。

设计要点见 references/vision-fallback.md。核心是三件事：
1. 模型永远只在「图像坐标系」里说话，设备坐标换算由本脚本做
2. 每次点击后做一次零成本的像素差分，确认屏幕真的变了
3. 没有 VM Service 时，用 logcat/oslog + 视觉异常检测当 oracle

用法:
    python screen.py shot --out .smoke/frames
    python screen.py tap --x 412 --y 780 --ref .smoke/frames/0003.json
    python screen.py text "13800000000"
    python screen.py swipe --from 500,1500 --to 500,600 --ref .smoke/frames/0003.json
    python screen.py key back
    python screen.py diff .smoke/frames/0003.png .smoke/frames/0004.png
    python screen.py inspect .smoke/frames/0004.png
    python screen.py errors --since 60
    python screen.py deeplink --url "myapp://checkout"
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

try:
    from PIL import Image, ImageChops, ImageStat
except ImportError:
    sys.exit("需要 Pillow: pip install Pillow")


# ─────────────────────────── 平台探测 ───────────────────────────

def have(cmd):
    return subprocess.run(["which", cmd], capture_output=True).returncode == 0


def detect_platform():
    if have("adb"):
        r = subprocess.run(["adb", "devices"], capture_output=True, text=True)
        if any(l.split("\t")[-1].strip() == "device" for l in r.stdout.splitlines()[1:] if "\t" in l):
            return "android"
    if have("xcrun"):
        r = subprocess.run(["xcrun", "simctl", "list", "devices", "booted"],
                           capture_output=True, text=True)
        if "Booted" in r.stdout:
            return "ios"
    sys.exit("没有检测到已连接的 Android 设备或已启动的 iOS 模拟器")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def must(p, what):
    """子进程失败必须显式报错。静默把空输出当结果，会产出 ok:true 的假阴性——
    L3 的 oracle 说"没问题"必须真的意味着查过了没问题。"""
    if p.returncode != 0:
        sys.exit(f"{what} 失败: {(p.stderr or p.stdout or '').strip()[:300]}")
    return p


# ─────────────────────────── 截图 ───────────────────────────

def capture_raw(platform, path):
    if platform == "android":
        with open(path, "wb") as fh:
            p = subprocess.run(["adb", "exec-out", "screencap", "-p"], stdout=fh)
        if p.returncode != 0:
            sys.exit("adb screencap 失败")
    else:
        p = run(["xcrun", "simctl", "io", "booted", "screenshot", path])
        if p.returncode != 0:
            sys.exit(f"simctl screenshot 失败: {p.stderr}")


def cmd_shot(args):
    platform = args.platform or detect_platform()
    os.makedirs(args.out, exist_ok=True)

    idx = len([f for f in os.listdir(args.out)
               if f.endswith(".png") and not f.endswith(".raw.png")])
    stem = os.path.join(args.out, f"{idx:04d}")
    raw_path = stem + ".raw.png"
    capture_raw(platform, raw_path)

    img = Image.open(raw_path).convert("RGB")
    dev_w, dev_h = img.size

    # 缩放到模型友好的尺寸。长边 900px 左右在识别率和 token 之间比较平衡；
    # 再小会丢失小字号按钮，再大 token 涨得比精度快。
    scale = min(1.0, args.max_side / max(dev_w, dev_h))
    if scale < 1.0:
        img = img.resize((round(dev_w * scale), round(dev_h * scale)), Image.LANCZOS)
    img_w, img_h = img.size
    png_path = stem + ".png"
    img.save(png_path, optimize=True)

    meta = {
        "index": idx,
        "platform": platform,
        "device_size": [dev_w, dev_h],
        "image_size": [img_w, img_h],
        "scale": round(scale, 6),
        "image": png_path,
        "raw": raw_path,
        "ts": time.time(),
        "note": "模型只在 image_size 坐标系里给坐标，换算交给 tap --ref",
    }
    with open(stem + ".json", "w") as fh:
        json.dump(meta, fh, indent=2)

    if not args.keep_raw:
        os.remove(raw_path)
        meta["raw"] = None

    print(json.dumps(meta, ensure_ascii=False))


# ─────────────────────────── 坐标换算与输入 ───────────────────────────

def to_device(x, y, ref, space):
    """把图像坐标换算成设备坐标。这是纯视觉驱动最容易错的地方——
    截图被缩放过，模型看到的是缩放后的图，直接拿它的坐标去点必然偏。"""
    if space == "device":
        return int(x), int(y)
    if not ref:
        sys.exit("--space image 时必须提供 --ref <shot 输出的 json>，否则无法换算")
    with open(ref) as fh:
        meta = json.load(fh)
    s = meta["scale"]
    dx, dy = int(round(x / s)), int(round(y / s))
    dw, dh = meta["device_size"]
    if not (0 <= dx < dw and 0 <= dy < dh):
        sys.exit(f"换算后坐标 ({dx},{dy}) 超出屏幕 {dw}x{dh}，模型给的坐标有问题")
    return dx, dy


def tap_device(platform, x, y):
    if platform == "android":
        p = run(["adb", "shell", "input", "tap", str(x), str(y)])
    else:
        if not have("idb"):
            sys.exit("iOS 模拟器点击需要 idb（xcrun simctl 不支持注入触摸）。\n"
                     "  安装: brew tap facebook/fb && brew install idb-companion && pipx install fb-idb\n"
                     "  或改用 mobile-mcp / Maestro 作为输入后端")
        p = run(["idb", "ui", "tap", str(x), str(y)])
    if p.returncode != 0:
        sys.exit(f"点击失败: {p.stderr}")


def cmd_tap(args):
    platform = args.platform or detect_platform()
    x, y = to_device(args.x, args.y, args.ref, args.space)
    tap_device(platform, x, y)
    print(json.dumps({"tapped_device": [x, y], "from": [args.x, args.y], "space": args.space}))


def cmd_swipe(args):
    platform = args.platform or detect_platform()
    x1, y1 = to_device(*map(float, args.start.split(",")), args.ref, args.space)
    x2, y2 = to_device(*map(float, args.end.split(",")), args.ref, args.space)
    if platform == "android":
        must(run(["adb", "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(args.ms)]), "swipe")
    else:
        must(run(["idb", "ui", "swipe", str(x1), str(y1), str(x2), str(y2)]), "swipe")
    print(json.dumps({"swipe": [[x1, y1], [x2, y2]]}))


def cmd_text(args):
    platform = args.platform or detect_platform()
    if platform == "android":
        # adb input text 不支持非 ASCII。中文要用 ADBKeyBoard 输入法，
        # 或者在测试数据里避开中文（推荐后者：冒烟用例的输入值自己定）。
        if not args.value.isascii():
            print(json.dumps({
                "warning": "adb input text 不支持中文。请安装 ADBKeyBoard 并用 broadcast 输入，"
                           "或把测试数据换成 ASCII。",
            }, ensure_ascii=False))
        esc = args.value.replace(" ", "%s").replace("'", "\\'")
        must(run(["adb", "shell", "input", "text", esc]), "input text")
    else:
        must(run(["idb", "ui", "text", args.value]), "idb text")
    print(json.dumps({"typed": args.value}, ensure_ascii=False))


def cmd_key(args):
    platform = args.platform or detect_platform()
    amap = {"back": "4", "home": "3", "enter": "66", "tab": "61", "del": "67"}
    if platform == "android":
        must(run(["adb", "shell", "input", "keyevent", amap.get(args.name, args.name)]), "keyevent")
    else:
        imap = {"back": "escape", "enter": "return", "home": "home"}
        must(run(["idb", "ui", "key", imap.get(args.name, args.name)]), "idb key")
    print(json.dumps({"key": args.name}))


def cmd_deeplink(args):
    platform = args.platform or detect_platform()
    if platform == "android":
        must(run(["adb", "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", args.url]), "deeplink")
    else:
        must(run(["xcrun", "simctl", "openurl", "booted", args.url]), "deeplink")
    print(json.dumps({"opened": args.url}))


# ─────────────────── 零成本 oracle：像素差分与异常检测 ───────────────────

def cmd_diff(args):
    """点击后屏幕到底变没变。这一步不花 token，却是纯视觉模式能不能用的关键：
    没有它，agent 会在点空的情况下一路往下走，最后给出一份全绿的假报告。"""
    a = Image.open(args.a).convert("RGB")
    b = Image.open(args.b).convert("RGB")
    if a.size != b.size:
        b = b.resize(a.size)
    diff = ImageChops.difference(a, b).convert("L")
    stat = ImageStat.Stat(diff)
    mean = stat.mean[0]
    hist = diff.histogram()
    total = a.size[0] * a.size[1]
    changed = sum(hist[13:]) / total

    verdict = ("no_change" if changed < 0.005 else
               "minor" if changed < 0.05 else "changed")
    print(json.dumps({
        "changed_ratio": round(changed, 4),
        "mean_delta": round(mean, 2),
        "verdict": verdict,
        "hint": {
            "no_change": "点击很可能没命中。不要继续往下走，重新定位这一步。",
            "minor": "只有局部变化（可能是按钮按下态或 toast），确认是否达到预期。",
            "changed": "屏幕已切换。",
        }[verdict],
    }, ensure_ascii=False))


def cmd_inspect(args):
    """不调模型的视觉异常检测：红屏、白屏、疑似永久 loading。
    这些是没有 VM Service 时仅存的免费信号，先用它们过滤，
    过滤不掉的再交给模型看。"""
    img = Image.open(args.image).convert("RGB")
    small = img.resize((80, 160))
    n = 80 * 160

    r, g, b = (c.histogram() for c in small.split())
    # 红屏判定：整体强红。用通道均值差比逐像素判断快，且对压缩噪声更稳。
    mean_r = sum(i * v for i, v in enumerate(r)) / n
    mean_g = sum(i * v for i, v in enumerate(g)) / n
    mean_b = sum(i * v for i, v in enumerate(b)) / n
    reddish = mean_r > 130 and (mean_r - mean_g) > 60 and (mean_r - mean_b) > 60

    gray = small.convert("L")
    variance = ImageStat.Stat(gray).var[0]
    ghist = gray.histogram()
    dominant = max(ghist) / n          # 单一灰度占比

    flags = []
    if reddish:
        flags.append("flutter_error_screen")   # debug 下的红屏错误页
    if variance < 25:
        flags.append("blank_screen")           # 纯色，通常是渲染失败或白屏
    elif dominant > 0.88:
        flags.append("mostly_empty")           # 近乎空白，可能还在 loading 或列表为空

    print(json.dumps({
        "flags": flags,
        "channel_means": [round(mean_r, 1), round(mean_g, 1), round(mean_b, 1)],
        "luma_variance": round(variance, 1),
        "dominant_ratio": round(dominant, 3),
        "ok": not flags,
        "caveat": "只是零成本预筛。没有 flag 不代表页面正确，只代表不是明显异常。",
    }, ensure_ascii=False))


# ─────────────────────────── 错误采集 ───────────────────────────

FLUTTER_ERR = re.compile(
    r"(EXCEPTION CAUGHT BY|RenderFlex overflowed|FATAL EXCEPTION|"
    r"Unhandled Exception|StateError|NoSuchMethodError|RangeError|"
    r"setState\(\) called after dispose|A RenderFlex overflowed|"
    r"HttpException|SocketException|FormatException)",
    re.I,
)


def cmd_errors(args):
    """没有 VM Service 时的 oracle 替代品。
    Flutter 的框架异常会打到 logcat / os_log，抓得到，只是没有结构化的堆栈。"""
    platform = args.platform or detect_platform()
    if platform == "android":
        # logcat 的 -t/-T 只接受行数或绝对时间戳，不接受 "60s" 这类相对时长
        # （那是 macOS `log show --last` 的语法）——传错参数时 logcat 报错到
        # stderr、stdout 为空，会被当成"无异常"。相对窗口要先取设备时间算绝对秒。
        p = None
        ts = run(["adb", "shell", "date", "+%s"])
        if ts.returncode == 0 and ts.stdout.strip().isdigit():
            since_epoch = int(ts.stdout.strip()) - args.since
            p = run(["adb", "logcat", "-d", "-T", f"{since_epoch}.000"])
            if p.returncode != 0:
                p = None
        if p is None:
            # 取不到设备时间/旧设备不认 -T：退化为全量 dump，宁可多看不可漏看
            p = must(run(["adb", "logcat", "-d"]), "adb logcat")
        lines = p.stdout.splitlines()
    else:
        p = must(run(["xcrun", "simctl", "spawn", "booted", "log", "show",
                      "--last", f"{args.since}s", "--style", "compact"], timeout=60),
                 "log show")
        lines = p.stdout.splitlines()

    hits, context = [], []
    for i, line in enumerate(lines):
        if FLUTTER_ERR.search(line):
            hits.append({"line_no": i, "text": line.strip()[:400]})
            context.extend(lines[max(0, i - 2): i + 8])

    print(json.dumps({
        "window_seconds": args.since,
        "error_count": len(hits),
        "errors": hits[:20],
        "context_tail": [l.strip()[:300] for l in context[-40:]],
        "ok": not hits,
        "caveat": "logcat 只是 VM Service 的降级替代：拿不到被 catch 吞掉的异常，"
                  "也拿不到 HTTP 状态码。能装 marionette/agent_lens 就别只靠这个。",
    }, ensure_ascii=False))


# ─────────────────────────── CLI ───────────────────────────

def main():
    ap = argparse.ArgumentParser(description="L3 视觉兜底驱动")
    ap.add_argument("--platform", choices=["android", "ios"], help="不指定则自动探测")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("shot", help="截图并输出坐标元信息")
    s.add_argument("--out", default=".smoke/frames")
    s.add_argument("--max-side", type=int, default=900)
    s.add_argument("--keep-raw", action="store_true")
    s.set_defaults(func=cmd_shot)

    s = sub.add_parser("tap")
    s.add_argument("--x", type=float, required=True)
    s.add_argument("--y", type=float, required=True)
    s.add_argument("--space", choices=["image", "device"], default="image")
    s.add_argument("--ref", help="shot 输出的 .json，--space image 时必需")
    s.set_defaults(func=cmd_tap)

    s = sub.add_parser("swipe")
    s.add_argument("--from", dest="start", required=True, metavar="X,Y")
    s.add_argument("--to", dest="end", required=True, metavar="X,Y")
    s.add_argument("--space", choices=["image", "device"], default="image")
    s.add_argument("--ref")
    s.add_argument("--ms", type=int, default=300)
    s.set_defaults(func=cmd_swipe)

    s = sub.add_parser("text")
    s.add_argument("value")
    s.set_defaults(func=cmd_text)

    s = sub.add_parser("key")
    s.add_argument("name")
    s.set_defaults(func=cmd_key)

    s = sub.add_parser("deeplink")
    s.add_argument("--url", required=True)
    s.set_defaults(func=cmd_deeplink)

    s = sub.add_parser("diff", help="两帧之间是否真的变了（零 token）")
    s.add_argument("a")
    s.add_argument("b")
    s.set_defaults(func=cmd_diff)

    s = sub.add_parser("inspect", help="红屏/白屏/空内容检测（零 token）")
    s.add_argument("image")
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("errors", help="从 logcat/oslog 抓 Flutter 异常")
    s.add_argument("--since", type=int, default=60)
    s.set_defaults(func=cmd_errors)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
