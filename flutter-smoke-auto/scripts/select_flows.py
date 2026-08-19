#!/usr/bin/env python3
"""定向执行的选用例工具：把「这次改了什么」翻译成「该跑哪几条用例」。

契约表就是影响面地图：git diff 出改动文件 → registry 里 file 字段命中的 id →
用到这些 id 的 flow/spec（含 subflow 反查主 flow）→ 加上冷启动锚点。
输出每行一个文件路径，直接喂给 run_smoke.sh --only-file 或 playwright。

用法:
    python select_flows.py --flows .smoke/flows --registry .smoke/registry.json --changed
    python select_flows.py --flows .smoke/flows --registry .smoke/registry.json --changed --base HEAD~3
    python select_flows.py --flows .smoke/flows --keyword login            # 手动圈范围
    python select_flows.py --flows .smoke/flows --keyword login --web     # 输出 web spec
    python select_flows.py --flows .smoke/flows --failed .smoke/runs/<ts>/artifacts/results.xml
                                                                          # 修复轮：只重跑上轮失败

规则:
    - 冷启动用例（文件名含 smoke-01 / cold / launch，取不到则字典序第一条）永远包含
    - --changed 映射不到任何用例时只输出冷启动，并在 stderr 提醒补用例
    - --failed 按 junit testcase name 匹配 flow 的 name: 字段或文件名主干；上轮全绿则拒绝（退出 2）
    - 零第三方依赖

退出码: 0 正常 / 2 参数或环境错误
"""

import argparse
import json
import os
import re
import subprocess
import sys

RE_ID = re.compile(r"""\bid\s*:\s*['"]?([A-Za-z0-9_.\-]+)""")
RE_WEB_ID = re.compile(r"""\bbyId\s*\(\s*[^,]*,\s*['"]([^'"]+)['"]|flt-semantics-identifier=["']([^"']+)["']""")
RE_RUNFLOW = re.compile(r"""runFlow\s*:(?:\s*\{?\s*file\s*:)?\s*['"]?([^\s'"}\n#]+)""")
RE_YAML_NAME = re.compile(r"^name\s*:\s*(.+)$", re.M)
RE_SPEC_TITLE = re.compile(r"""\b(?:test|it)(?:\.\w+)?\s*\(\s*['"`]([^'"`]+)""")
COLD_HINT = re.compile(r"smoke[-_]?0*1\b|cold|launch", re.I)

# 目录回退映射时剥掉的分层目录名：lib/features/auth/data → feature 目录 lib/features/auth
LAYER_DIRS = {"data", "domain", "presentation", "ui", "pages", "widgets", "screens",
              "views", "models", "services", "repositories", "providers", "blocs",
              "cubits", "controllers", "state", "components"}
# feature 目录退到这些层级说明太泛（会圈进全量），放弃回退、走提醒
GENERIC_DIRS = {"lib", "lib/src", "lib/features", "lib/modules", "lib/app"}


def read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def ids_in(path):
    text = read(path)
    if path.endswith((".yaml", ".yml")):
        return set(RE_ID.findall(text))
    return {a or b for a, b in RE_WEB_ID.findall(text)}


def changed_files(base):
    out = set()
    for cmd in (["git", "diff", "--name-only", base, "--", "lib/"],
                ["git", "ls-files", "--others", "--exclude-standard", "lib/"]):
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode == 0:
            out |= {l.strip() for l in p.stdout.splitlines() if l.strip()}
    return out


def feature_dir(path):
    """文件归属的 feature 目录：从所在目录向上剥掉纯分层目录名。"""
    d = os.path.dirname(os.path.normpath(path))
    while os.path.basename(d) in LAYER_DIRS:
        d = os.path.dirname(d)
    return d


def keyword_haystack(path):
    """关键词只匹配文件名和用例名，不做全文匹配——
    否则所有引用 login subflow 的用例都含 "login"，定向会变成近似全量。"""
    names = []
    text = read(path)
    if path.endswith((".yaml", ".yml")):
        names = RE_YAML_NAME.findall(text)
    else:
        names = RE_SPEC_TITLE.findall(text)
    return " ".join([os.path.basename(path)] + names).lower()


def pick_cold_start(files):
    for f in sorted(files):
        if COLD_HINT.search(os.path.basename(f)):
            return f
    return sorted(files)[0] if files else None


def main():
    ap = argparse.ArgumentParser(description="定向执行选用例")
    ap.add_argument("--flows", required=True)
    ap.add_argument("--registry")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--changed", action="store_true", help="按 git 改动推导（需 --registry）")
    mode.add_argument("--keyword", help="按文件名/用例名关键词圈定")
    mode.add_argument("--failed", metavar="RESULTS_XML",
                      help="按上一轮 junit 结果只圈失败用例（修复轮重跑用）")
    ap.add_argument("--base", default="HEAD", help="--changed 的对比基准")
    ap.add_argument("--web", action="store_true", help="输出 web spec 而非移动端 flow")
    args = ap.parse_args()

    if not os.path.isdir(args.flows):
        sys.exit(f"找不到 flow 目录: {args.flows}")

    # 收集候选：移动端 = flows 下的 yaml（不含 web/ 与 subflows）；web = flows/web 下的 spec
    mains, subflows = [], []
    for root, dirs, files in os.walk(args.flows):
        if args.web:
            if os.path.basename(root) != "web" and f"{os.sep}web{os.sep}" not in root + os.sep:
                dirs[:] = [d for d in dirs if True]
            for f in sorted(files):
                if re.search(r"\.(spec|test)\.(ts|js|mjs|tsx)$", f):
                    mains.append(os.path.join(root, f))
        else:
            dirs[:] = [d for d in dirs if d != "web"]
            for f in sorted(files):
                if f.endswith((".yaml", ".yml")):
                    mains.append(os.path.join(root, f))
    sib = os.path.join(os.path.dirname(os.path.abspath(args.flows.rstrip("/"))), "subflows")
    if os.path.isdir(sib):
        for f in sorted(os.listdir(sib)):
            if f.endswith((".yaml", ".yml")):
                subflows.append(os.path.join(sib, f))
    if not mains:
        sys.exit(f"{args.flows} 下没有候选用例（{'web spec' if args.web else 'yaml flow'}）")

    cold = pick_cold_start(mains)
    selected = set()

    if args.keyword:
        kw = args.keyword.lower()
        for f in mains:
            if kw in keyword_haystack(f):
                selected.add(f)
    elif args.failed:
        # 修复轮的工具入口。没有它时"只重跑失败的那几条"是纪律没有抓手，
        # agent 会图省事整轮全量重跑（真实项目实测：同一失败集连跑两遍全量）
        import xml.etree.ElementTree as ET
        try:
            root = ET.parse(args.failed).getroot()
        except (OSError, ET.ParseError) as exc:
            sys.exit(f"读不了 junit 结果 {args.failed}: {exc}")
        failed_names = {tc.get("name", "").strip() for tc in root.iter("testcase")
                        if any(ch.tag in ("failure", "error") for ch in tc)}
        failed_names.discard("")
        if not failed_names:
            print("上一轮全绿，没有可重跑的失败用例。要再验证请用 --changed 或 --all 全量。",
                  file=sys.stderr)
            sys.exit(2)
        for f in mains:
            stem = os.path.splitext(os.path.basename(f))[0]
            text = read(f)
            names = {n.strip().strip("'\"") for n in
                     (RE_YAML_NAME.findall(text) if f.endswith((".yaml", ".yml"))
                      else RE_SPEC_TITLE.findall(text))}
            if stem in failed_names or names & failed_names:
                selected.add(f)
        if not selected:
            sys.exit(f"junit 里的失败用例（{', '.join(sorted(failed_names)[:5])}）"
                     f"没有匹配到任何 flow——用例被改名/删除了？")
    else:
        if not args.registry:
            sys.exit("--changed 需要 --registry")
        with open(args.registry, encoding="utf-8") as fh:
            registry = json.load(fh)
        changed = changed_files(args.base)
        norm = {os.path.normpath(c) for c in changed}
        reg_files = {os.path.normpath(meta.get("file", ""))
                     for meta in registry.values() if isinstance(meta, dict)}
        affected_ids = {ident for ident, meta in registry.items()
                        if isinstance(meta, dict)
                        and os.path.normpath(meta.get("file", "")) in norm}
        # 回退映射：改动文件没有埋点（典型：service/repository 逻辑层）时，
        # 按 feature 目录归属圈入同 feature 埋点对应的用例——否则逻辑改动
        # 只跑冷启动还全绿，定向验证给出假安全感
        unmatched = norm - reg_files
        fb_feats = {feature_dir(c) for c in unmatched} - GENERIC_DIRS
        if fb_feats:
            fb_ids = {ident for ident, meta in registry.items()
                      if isinstance(meta, dict)
                      and feature_dir(os.path.normpath(meta.get("file", ""))) in fb_feats}
            if fb_ids - affected_ids:
                print(f"精确映射未命中的改动按 feature 目录归属圈入 "
                      f"{len(fb_ids - affected_ids)} 个埋点（{', '.join(sorted(fb_feats))}）",
                      file=sys.stderr)
            affected_ids |= fb_ids
        # 直接命中的主用例
        for f in mains:
            if ids_in(f) & affected_ids:
                selected.add(f)
        # subflow 命中 → 反查引用它的主 flow
        hit_subs = {os.path.basename(s) for s in subflows if ids_in(s) & affected_ids}
        if hit_subs:
            for f in mains:
                if not f.endswith((".yaml", ".yml")):
                    continue
                refs = {os.path.basename(m) for m in RE_RUNFLOW.findall(read(f))}
                if refs & hit_subs:
                    selected.add(f)
        if changed and not selected:
            print(f"改动（{len(changed)} 个文件）未映射到任何用例，本次只跑冷启动锚点——"
                  f"它不构成对该改动的验证：\n"
                  f"  - 新功能 → 按 Phase 2–4 增量补用例（flow/spec + 埋点）\n"
                  f"  - 既有功能的逻辑/工具层改动 → 定向映射对它是盲的，"
                  f"用 --only <关键词> 手动圈范围或跑全量",
                  file=sys.stderr)

    if cold:
        selected.add(cold)
    for f in sorted(selected):
        print(f)


if __name__ == "__main__":
    main()
