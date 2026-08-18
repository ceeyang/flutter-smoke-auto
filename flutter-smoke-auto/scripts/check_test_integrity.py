#!/usr/bin/env python3
"""测试完整性闸门：扫 git diff，拦截「弱化测试」类改动。

存在的理由：任何 agent 拿到「跑测试 + 失败自己修」这个循环之后，
让测试变绿最省力的办法不是修好代码，是弄坏测试。删断言、加 skip、
调大 timeout、换成更宽松的匹配——每一步单看都有说得过去的理由，
合起来是覆盖率下降而报告说变健康了。

这个脚本不依赖 agent 的自觉。它只看 diff 的事实。

用法:
    python check_test_integrity.py                   # 默认基准：上次冒烟起点（.smoke/state.json），无则 HEAD
    python check_test_integrity.py --base main --json
    git diff | python check_test_integrity.py --stdin
    python check_test_integrity.py --staged          # pre-commit hook 用
    python check_test_integrity.py --test-paths qa/flows   # 额外的测试目录

默认识别的测试路径：test(s)/spec(s)/__tests__/e2e/integration_test/.maestro/.smoke，
以及 *_test.dart、*.spec.ts、test_*.py、*Test.java 等命名。数据文件（.json/.md）不扫。

退出码: 0 通过 / 1 发现违规 / 2 参数或环境错误
"""

import argparse
import json
import os
import re
import subprocess
import sys

# ─────────────────── 测试文件识别 ───────────────────

TEST_PATH = re.compile(
    r"(^|/)(tests?|spec|specs|__tests__|e2e|integration_test|\.maestro|maestro|\.smoke)(/|$)"
    r"|(^|/)test_[^/]+\.py$"
    r"|_test\.(dart|py|go|js|ts|tsx)$"
    r"|\.(test|spec)\.(js|jsx|ts|tsx|mjs)$"
    r"|Test[s]?\.(java|kt|cs)$",
    re.I,
)

# 数据/文档文件不扫：report.md 里复述断言、registry.json 里出现 id 都是正常内容
NON_CODE_EXT = (".md", ".json", ".txt", ".log", ".lock", ".png", ".jpg", ".webp", ".pdf")


def is_test_file(path, extra_dirs=()):
    if path.endswith(NON_CODE_EXT):
        return False
    if any(path == d or path.startswith(d.rstrip("/") + "/") for d in extra_dirs):
        return True
    return bool(TEST_PATH.search(path))


# ─────────────────── 弱化模式 ───────────────────

# 被删掉的断言（出现在 - 行里）
ASSERT_LINE = re.compile(
    r"\b(assert\w*|expect|should|require\.|assertThat|verify|"
    r"assertVisible|assertNotVisible|assertTrue|assertEquals|"
    r"XCTAssert\w*|Assert\.\w+)\b",
    re.I,
)

# 新增的跳过/忽略标记（出现在 + 行里）
ADDED_SKIP = [
    (re.compile(r"\b(test|it|describe|context)\.(skip|todo|only)\b"), "新增 test.skip/only/todo"),
    (re.compile(r"@(pytest\.mark\.)?(skip|skipif|xfail)\b"), "新增 pytest skip/xfail"),
    (re.compile(r"\bskip\s*:\s*true\b"), "新增 skip: true"),
    (re.compile(r"\boptional\s*:\s*true\b"), "新增 optional: true（Maestro 会忽略该步失败）"),
    (re.compile(r"\bcontinueOnFailure\s*:\s*true\b"), "新增 continueOnFailure"),
    (re.compile(r"\b(skip|Skip)\s*\(\s*[\"']"), "新增 skip() 调用"),
    (re.compile(r"@(Ignore|Disabled)\b"), "新增 @Ignore/@Disabled"),
    (re.compile(r"\bt\.Skip\("), "新增 t.Skip()"),
    (re.compile(r"\bskip\s*:\s*[\"']"), "新增 skip 原因标注"),
    (re.compile(r"//\s*(TODO|FIXME).*(disabled?|skip)", re.I), "注释掉的测试"),
]

# 强/弱匹配器配对：删了左边的强断言、同时加了右边的弱断言 → 判弱化
STRONG_MATCHER = re.compile(
    r"\b(toBe|toEqual|toStrictEqual|toHaveText|toHaveLength|"
    r"assertEquals|assertVisible)\b"
)
WEAK_MATCHER = re.compile(
    r"\b(toBeDefined|toBeTruthy|toBeFalsy|not\.toThrow|assertNotNull|"
    r"assertNotEmpty|isNotNull|assertExists|toMatchObject|"
    r"toBeGreaterThanOrEqual\s*\(\s*0\s*\))\b"
)

# 断言行里的定位目标（id: xxx），用于检测「断言目标被换掉」
ID_IN_ASSERT = re.compile(r"""\bid\s*:\s*['"]?([A-Za-z0-9_.\-]+)""")

# 超时/等待数值
TIMEOUT_NUM = re.compile(
    r"\b(timeout|timeoutMs|waitFor|wait_for|extendedWaitUntil|retries|maxAttempts|"
    r"implicitlyWait|setTimeout|sleep|delay)\b[^0-9\n]{0,20}(\d{2,7})",
    re.I,
)

# 断言被 try/catch 包住
ADDED_TRY = re.compile(r"^\s*(try\s*[:{]|catch\s*\(|except\b|rescue\b)")

# 断言数量被调小：expect(x).toHaveLength(N) / 期望条数
COUNT_ASSERT = re.compile(r"(toHaveLength|hasLength|length\s*==|count\s*==|size\s*==)\s*\(?\s*(\d+)")


# ─────────────────── diff 解析 ───────────────────

def default_base():
    """无显式基准时优先用上次冒烟起点（.smoke/state.json 的 commit）。
    堵「先 commit 后跑闸门」的盲区：弱化改动被顺手 commit 后，对比 HEAD
    的 diff 是空的，闸门全盲；对比冒烟起点仍然看得见整轮改动。"""
    try:
        with open(os.path.join(".smoke", "state.json"), encoding="utf-8") as fh:
            commit = str(json.load(fh).get("commit", ""))
        if commit and commit != "unknown":
            p = subprocess.run(["git", "cat-file", "-e", commit + "^{commit}"],
                               capture_output=True)
            if p.returncode == 0:
                return commit, True
    except (OSError, ValueError):
        pass
    return "HEAD", False


def get_diff(args):
    if args.stdin:
        return sys.stdin.read()
    cmd = ["git", "diff", "--unified=3"]
    if args.staged:
        cmd.append("--cached")
    elif args.base:
        cmd.append(args.base)
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"git diff 失败: {p.stderr.strip()}")
    return p.stdout


def untracked_files():
    """新生成还没 add 的文件，git diff 看不见——首轮自愈的 flow 就在这里。
    把它们整个当作新增行纳入检查，否则闸门对第一轮的产出是盲的。"""
    p = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return []
    return [l for l in p.stdout.splitlines() if l.strip()]


def parse_diff(text):
    """返回 {path: {"added": [...], "removed": [...], "deleted_file": bool}}"""
    files, cur = {}, None
    for line in text.splitlines():
        if line.startswith("diff --git"):
            m = re.search(r"b/(.+)$", line)
            cur = m.group(1) if m else None
            if cur:
                files[cur] = {"added": [], "removed": [], "deleted_file": False}
        elif cur is None:
            continue
        elif line.startswith("deleted file mode"):
            files[cur]["deleted_file"] = True
        elif line.startswith("+") and not line.startswith("+++"):
            files[cur]["added"].append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            files[cur]["removed"].append(line[1:])
    return files


# ─────────────────── 检查 ───────────────────

def max_timeout(lines):
    vals = [int(m.group(2)) for l in lines for m in TIMEOUT_NUM.finditer(l)]
    return max(vals) if vals else None


def check_file(path, data):
    findings = []
    added, removed = data["added"], data["removed"]

    def add(sev, kind, detail, evidence=""):
        findings.append({"file": path, "severity": sev, "kind": kind,
                         "detail": detail, "evidence": evidence[:200]})

    # 1. 整个测试文件被删
    if data["deleted_file"]:
        add("block", "test_file_deleted",
            "整个测试文件被删除。移除测试等于降低覆盖率，必须由人决定。")
        return findings

    # 2. 断言被删且没有等量补回
    removed_asserts = [l for l in removed if ASSERT_LINE.search(l) and l.strip()]
    added_asserts = [l for l in added if ASSERT_LINE.search(l) and l.strip()]
    net = len(removed_asserts) - len(added_asserts)
    if net > 0:
        add("block", "assertions_removed",
            f"净减少 {net} 条断言（删 {len(removed_asserts)} 增 {len(added_asserts)}）。"
            f"若确为重构，请在同一提交里说明每条被删断言由什么替代。",
            removed_asserts[0].strip() if removed_asserts else "")

    # 3. 新增跳过标记
    for line in added:
        for pat, desc in ADDED_SKIP:
            if pat.search(line):
                add("block", "test_skipped", desc + "。跳过测试不是修复。", line.strip())
                break

    # 4. 断言被换成更宽松的匹配
    for line in added:
        if WEAK_MATCHER.search(line):
            strong_removed = any(STRONG_MATCHER.search(r) for r in removed)
            if strong_removed:
                add("block", "matcher_weakened",
                    "断言被替换为更宽松的匹配（如 toBe → toBeDefined、"
                    "assertVisible → assertExists）。"
                    "这会让测试在 bug 存在时依然通过。", line.strip())
                break

    # 4b. 定位目标被换掉（id 从业务元素漂移到别的元素）。
    # 注意：git 最小 diff 里断言关键字往往是上下文行，只有 id: 行在增删里，
    # 所以这里对全部增删行提取 id，而不只看断言行。
    # 只能提示不能阻断：改名重构也长这样。放行的前提是 diff/registry 里
    # 有该 id 被改名的证据——这由人（或分诊规则）查证。
    removed_ids = {m.group(1) for l in removed for m in ID_IN_ASSERT.finditer(l)}
    added_ids = {m.group(1) for l in added for m in ID_IN_ASSERT.finditer(l)}
    lost, gained = removed_ids - added_ids, added_ids - removed_ids
    if lost and gained:
        add("warn", "assertion_target_changed",
            f"定位/断言目标从 {sorted(lost)} 换成了 {sorted(gained)}。"
            f"合法的唯一理由是 id 改名——请在 registry/源码 diff 里找到改名证据；"
            f"若新目标是 root/container 之类必然存在的容器，这就是在伪造绿灯。")

    # 5. 超时被显著调大
    old_t, new_t = max_timeout(removed), max_timeout(added)
    if old_t and new_t and new_t > old_t * 1.5:
        sev = "block" if new_t >= 30000 or new_t / old_t >= 3 else "warn"
        add(sev, "timeout_inflated",
            f"超时从 {old_t} 调到 {new_t}。调大超时是在掩盖性能退化或竞态，"
            f"不是修复。确需调整请附上为什么原值不再合理。")
    elif new_t and not old_t and new_t >= 30000:
        add("warn", "timeout_inflated", f"新增了 {new_t} 的超时，偏大。")

    # 6. 期望数量被调小
    old_counts = {int(m.group(2)) for l in removed for m in COUNT_ASSERT.finditer(l)}
    new_counts = {int(m.group(2)) for l in added for m in COUNT_ASSERT.finditer(l)}
    if old_counts and new_counts and max(new_counts) < max(old_counts):
        add("block", "expectation_lowered",
            f"期望数量从 {max(old_counts)} 降到 {max(new_counts)}。"
            f"降低期望值是把 bug 写进预期。")

    # 7. 断言被 try/catch 吞掉
    if any(ADDED_TRY.match(l) for l in added) and added_asserts:
        add("warn", "assertion_wrapped",
            "测试里新增了 try/catch/except 且涉及断言。"
            "确认不是在吞掉断言失败。")

    return findings


def main():
    ap = argparse.ArgumentParser(description="测试完整性闸门")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--base", help="对比基准，如 main / HEAD~1")
    g.add_argument("--staged", action="store_true", help="检查暂存区（pre-commit hook）")
    g.add_argument("--stdin", action="store_true", help="从标准输入读 diff")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--warn-only", action="store_true", help="只提示不阻断（不建议）")
    ap.add_argument("--test-paths", action="append", default=[],
                    help="额外视为测试目录的路径前缀（可重复），如 --test-paths qa/flows")
    ap.add_argument("--no-untracked", action="store_true",
                    help="不扫描未跟踪的新文件（默认扫描）")
    args = ap.parse_args()

    from_state = False
    if not (args.base or args.staged or args.stdin):
        args.base, from_state = default_base()

    files = parse_diff(get_diff(args))

    # 未跟踪的新文件：整个文件按「新增行」纳入
    if not (args.stdin or args.staged or args.no_untracked):
        for path in untracked_files():
            if path in files or not is_test_file(path, args.test_paths):
                continue
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().splitlines()
            except OSError:
                continue
            files[path] = {"added": lines, "removed": [], "deleted_file": False}

    test_files = {p: d for p, d in files.items() if is_test_file(p, args.test_paths)}

    findings = []
    for path, data in sorted(test_files.items()):
        findings.extend(check_file(path, data))

    blocks = [f for f in findings if f["severity"] == "block"]
    warns = [f for f in findings if f["severity"] == "warn"]

    if args.json:
        print(json.dumps({
            "test_files_changed": sorted(test_files),
            "blocking": blocks, "warnings": warns,
            "passed": not blocks,
        }, ensure_ascii=False, indent=2))
    else:
        base_note = f"（基准: 冒烟起点 {args.base[:12]}）" if from_state else ""
        print(f"检查 {len(files)} 个改动文件，其中测试文件 {len(test_files)} 个{base_note}")
        if not findings:
            print("通过：未发现弱化测试的改动。")
        for f in blocks:
            print(f"\n  [阻断] {f['file']}  {f['kind']}")
            print(f"         {f['detail']}")
            if f["evidence"]:
                print(f"         证据: {f['evidence']}")
        for f in warns:
            print(f"\n  [提示] {f['file']}  {f['kind']}: {f['detail']}")
        if blocks:
            print("\n" + "─" * 60)
            print("这些改动会降低测试的实际覆盖。回滚改动，把该项按 APP_DEFECT 上报。")
            print("若确属必要，放行流程见 references/test-integrity.md「放行的正确姿势」——")
            print("这个决定必须由人做出，不要让 agent 自己决定。")

    sys.exit(1 if blocks and not args.warn_only else 0)


if __name__ == "__main__":
    main()
