#!/usr/bin/env python3
"""静态扫描 Flutter 项目，产出业务图谱 app-map.json。

启发式扫描，目标是把人（或 agent）需要精读的文件从几百个缩小到几十个，
不追求精确。跑完必须人工/agent 校正，不要直接当作事实使用。

用法:
    python scan_app.py --project /path/to/flutter_app --out .smoke/app-map.json
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict

# ---------- 正则 ----------
RE_ROUTE_STR = re.compile(r"""['"](/[a-zA-Z0-9_\-/:]*)['"]""")
RE_GOROUTE = re.compile(r"GoRoute\s*\(", re.S)
RE_NAMED_ROUTE = re.compile(r"""(?:routeName|path|name)\s*[:=]\s*['"]([^'"]+)['"]""")
RE_PUSH = re.compile(r"""(?:pushNamed|goNamed|push|go|pushReplacementNamed)\s*\(\s*(?:context\s*,\s*)?['"]([^'"]+)['"]""")
RE_CLASS_WIDGET = re.compile(
    r"class\s+(\w+)\s+extends\s+(StatelessWidget|StatefulWidget|ConsumerWidget|ConsumerStatefulWidget|HookWidget)"
)
RE_ONPRESSED = re.compile(r"(onPressed|onTap|onSubmitted|onLongPress)\s*:")
RE_BUTTON = re.compile(
    r"\b(ElevatedButton|TextButton|OutlinedButton|IconButton|FloatingActionButton|"
    r"CupertinoButton|FilledButton|InkWell|GestureDetector|ListTile)\b"
)
RE_FIELD = re.compile(r"\b(TextField|TextFormField|CupertinoTextField|DropdownButton\w*|Checkbox|Switch|Radio)\b")
RE_TEXT_LITERAL = re.compile(r"""(?:Text|SelectableText)\s*\(\s*(?:const\s+)?['"]([^'"]{1,40})['"]""")
RE_SEMANTICS_ID = re.compile(r"""identifier\s*:\s*['"]([^'"]+)['"]""")
RE_SEMANTIC_LABEL = re.compile(r"""semanticLabel\s*:\s*['"]([^'"]+)['"]""")
RE_HTTP = re.compile(r"\b(?:http|dio|client)\s*\.\s*(get|post|put|delete|patch)\s*[<(]", re.I)
RE_ENDPOINT = re.compile(r"""['"](/(?:api|v\d)[a-zA-Z0-9_\-/{}:.]*)['"]""")
RE_AUTH_HINT = re.compile(r"\b(login|signIn|signUp|register|auth|token|logout|otp|verifyCode)\b", re.I)
RE_PAY_HINT = re.compile(r"\b(pay|payment|checkout|order|purchase|subscribe|wallet)\b", re.I)

# 页面文件名启发
SCREEN_SUFFIX = ("page", "screen", "view")

CRITICAL_HINTS = {
    "auth": RE_AUTH_HINT,
    "payment": RE_PAY_HINT,
}


def iter_dart_files(lib_dir):
    for root, dirs, files in os.walk(lib_dir):
        dirs[:] = [d for d in dirs if d not in {".dart_tool", "build", ".git", "generated"}]
        for f in files:
            if f.endswith(".dart") and not f.endswith((".g.dart", ".freezed.dart", ".gr.dart")):
                yield os.path.join(root, f)


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def classify_screen(path, classes):
    base = os.path.basename(path).replace(".dart", "")
    if any(base.endswith(s) for s in SCREEN_SUFFIX):
        return True
    return any(c.lower().endswith(SCREEN_SUFFIX) for c in classes)


def scan_file(path, rel):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError as exc:
        print(f"  ! 读取失败 {rel}: {exc}", file=sys.stderr)
        return None

    classes = RE_CLASS_WIDGET.findall(src)
    class_names = [c[0] for c in classes]

    routes = sorted(set(RE_NAMED_ROUTE.findall(src)) | {r for r in RE_ROUTE_STR.findall(src) if r.startswith("/")})
    navigations = sorted(set(RE_PUSH.findall(src)))

    interactive = []
    for m in RE_BUTTON.finditer(src):
        window = src[m.start(): m.start() + 400]
        label_m = RE_TEXT_LITERAL.search(window)
        interactive.append({
            "widget": m.group(1),
            "line": line_of(src, m.start()),
            "label": label_m.group(1) if label_m else None,
            "has_handler": bool(RE_ONPRESSED.search(window)),
        })

    fields = [{"widget": m.group(1), "line": line_of(src, m.start())} for m in RE_FIELD.finditer(src)]

    existing_ids = sorted(set(RE_SEMANTICS_ID.findall(src)))
    existing_labels = sorted(set(RE_SEMANTIC_LABEL.findall(src)))

    api_calls = sorted({m.group(1).lower() for m in RE_HTTP.finditer(src)})
    endpoints = sorted(set(RE_ENDPOINT.findall(src)))

    tags = [name for name, pat in CRITICAL_HINTS.items() if pat.search(src)]

    if not any([classes, routes, interactive, fields, endpoints]):
        return None

    return {
        "file": rel,
        "is_screen": classify_screen(path, class_names),
        "classes": class_names,
        "routes": routes,
        "navigates_to": navigations,
        "interactive": interactive,
        "input_fields": fields,
        "existing_semantics_ids": existing_ids,
        "existing_semantic_labels": existing_labels,
        "api_methods": api_calls,
        "endpoints": endpoints,
        "tags": tags,
        # 粗略打分：交互越多、有网络调用、命中关键业务词 → 越可能是主流程
        "weight": (
            len(interactive) * 2
            + len(fields) * 2
            + len(endpoints) * 3
            + len(tags) * 10
            + (5 if classify_screen(path, class_names) else 0)
        ),
    }


def find_docs(project):
    docs = []
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in {".git", "build", ".dart_tool", "node_modules", "ios", "android"}]
        depth = root[len(project):].count(os.sep)
        if depth > 2:
            dirs[:] = []
            continue
        for f in files:
            if f.lower().endswith((".md", ".txt")) and "CHANGELOG" not in f.upper():
                docs.append(os.path.relpath(os.path.join(root, f), project))
    return sorted(docs)[:40]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="Flutter 项目根目录")
    ap.add_argument("--out", default=".smoke/app-map.json")
    ap.add_argument("--top", type=int, default=25, help="候选主流程页面数量")
    args = ap.parse_args()

    project = os.path.abspath(args.project)
    lib_dir = os.path.join(project, "lib")
    if not os.path.isdir(lib_dir):
        sys.exit(f"找不到 {lib_dir}，--project 应指向 Flutter 项目根目录")

    entries = []
    for path in iter_dart_files(lib_dir):
        rel = os.path.relpath(path, project)
        info = scan_file(path, rel)
        if info:
            entries.append(info)

    screens = [e for e in entries if e["is_screen"]]
    all_routes = sorted({r for e in entries for r in e["routes"]})
    nav_graph = defaultdict(list)
    for e in entries:
        for target in e["navigates_to"]:
            nav_graph[e["file"]].append(target)

    candidates = sorted(entries, key=lambda e: -e["weight"])[: args.top]

    result = {
        "project": project,
        "summary": {
            "dart_files_scanned": len(entries),
            "screens": len(screens),
            "routes": len(all_routes),
            "already_instrumented": sum(len(e["existing_semantics_ids"]) for e in entries),
        },
        "routes": all_routes,
        "nav_graph": dict(nav_graph),
        "doc_candidates": find_docs(project),
        "smoke_candidates": [
            {k: e[k] for k in ("file", "classes", "routes", "tags", "weight", "endpoints")}
            for e in candidates
        ],
        "files": entries,
        "caveat": "启发式扫描结果，必须人工/agent 复核后再作为事实使用",
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    s = result["summary"]
    print(f"扫描完成: {s['dart_files_scanned']} 个文件, {s['screens']} 个页面, {s['routes']} 条路由, "
          f"已有 {s['already_instrumented']} 个 semantics identifier")
    print(f"输出: {args.out}")
    print("\n主流程候选（按权重）:")
    for c in result["smoke_candidates"][:10]:
        tag = f" [{','.join(c['tags'])}]" if c["tags"] else ""
        print(f"  {c['weight']:>4}  {c['file']}{tag}")
    print("\n下一步: 精读上述文件与路由定义，校正后再进入 Phase 2")


if __name__ == "__main__":
    main()
