#!/usr/bin/env python3
"""校验冒烟测试里用到的选择器都存在于契约表中，并可选地与源码对账。

这是防止"选择器幻觉"的闸门：生成阶段就拦下表外选择器，
而不是等到设备/浏览器上跑起来才发现元素找不到。

覆盖两种产物：
  - Maestro flow（*.yaml，Android/iOS）：内置迷你解析器，零依赖。
    flow 是本 skill 自己生成的受控格式（列表命令 + 标量/行内映射/块映射），
    子集解析足够；手写的奇形怪状 YAML 不在保证范围内。
  - Playwright spec（*.spec.ts/js，Web）：提取 byId('...') 与
    [flt-semantics-identifier="..."]，同样对表校验；getByText 视同 text 选择器。

text 选择器默认是错误（文案会改、会做国际化）；唯一例外是
assertNotVisible / expectNoErrorText 类"无错断言"——错误提示本来就没有 id。

用法:
    python check_registry.py --flows .smoke/flows --registry .smoke/registry.json
    python check_registry.py --flows .smoke/flows --registry .smoke/registry.json --source .
    python check_registry.py --registry .smoke/registry.json --source .   # 只做源码对账（Phase 2）

退出码: 0 通过 / 1 有错误 / 2 参数或环境错误
"""

import argparse
import json
import os
import re
import sys

# 值是选择器的命令；inputText 等的值是输入内容，不是选择器
SELECTOR_CMDS = {"tapOn", "doubleTapOn", "longPressOn", "assertVisible",
                 "assertNotVisible", "copyTextFrom", "scrollUntilVisible",
                 "extendedWaitUntil"}
# 这些命令里的 text 选择器放行：错误提示/空态文案没有 id，无错断言只能按文案匹配
TEXT_OK_CMDS = {"assertNotVisible"}

RE_HARDCODED_SECRET = re.compile(
    r"""(?:password|pwd|passwd|token|apikey|api_key|secret)\s*:\s*['"]?(?!\$\{)([^\s'"$][^'"\n]{2,})""",
    re.I,
)

# Web spec 提取
RE_WEB_BYID = re.compile(r"""\bbyId\s*\(\s*[^,]*,\s*['"]([^'"]+)['"]""")
RE_WEB_ATTR = re.compile(r"""flt-semantics-identifier=["']([^"']+)["']""")
RE_WEB_TEXT = re.compile(r"""\bgetByText\s*\(""")


# ─────────────────── Maestro flow 迷你解析 ───────────────────

def strip_quotes(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def parse_inline_map(s):
    """{ k: v, k2: v2 } → dict。只处理一层，值里不含逗号嵌套。"""
    body = s.strip()[1:-1]
    out = {}
    for part in body.split(","):
        if ":" in part:
            k, _, v = part.partition(":")
            out[strip_quotes(k)] = strip_quotes(v)
    return out


def parse_block_map(lines, i, indent):
    """从 lines[i] 起解析缩进 >= indent 的块映射，返回 (dict, 下一行号)。"""
    out = {}
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        cur = len(line) - len(line.lstrip())
        if cur < indent or line.lstrip().startswith("- "):
            break
        key, _, val = line.strip().partition(":")
        val = val.strip()
        if val == "":
            sub, i2 = parse_block_map(lines, i + 1, cur + 1)
            out[strip_quotes(key)] = sub
            i = i2
        else:
            out[strip_quotes(key)] = parse_inline_map(val) if val.startswith("{") else strip_quotes(val)
            i += 1
    return out, i


def parse_flow_commands(text):
    """返回 [(命令名, 值)]，值为 str/dict/None。只解析 --- 之后的命令文档。"""
    parts = re.split(r"^---\s*$", text, maxsplit=1, flags=re.M)
    body = parts[1] if len(parts) > 1 else text
    lines = body.splitlines()
    cmds, i = [], 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or not stripped.startswith("- "):
            i += 1
            continue
        item = stripped[2:].strip()
        base_indent = len(line) - len(line.lstrip())
        if ":" not in item:
            cmds.append((item, None))          # - inputRandomEmail
            i += 1
            continue
        cmd, _, val = item.partition(":")
        cmd, val = cmd.strip(), val.strip()
        if val == "":
            sub, i2 = parse_block_map(lines, i + 1, base_indent + 1)
            cmds.append((cmd, sub))
            i = i2
        else:
            cmds.append((cmd, parse_inline_map(val) if val.startswith("{") else strip_quotes(val)))
            i += 1
    return cmds


def selectors_from_value(val):
    """从命令值里递归取 (kind, value)。kind ∈ {id, text}。"""
    found = []
    if isinstance(val, str):
        if not val.startswith("$"):
            found.append(("text", val))
    elif isinstance(val, dict):
        if "id" in val and isinstance(val["id"], str):
            found.append(("id", val["id"]))
        if "text" in val and isinstance(val["text"], str) and not val["text"].startswith("$"):
            found.append(("text", val["text"]))
        for key in ("element", "visible", "notVisible"):
            if key in val:
                found.extend(selectors_from_value(val[key]))
    return found


# ─────────────────── 检查 ───────────────────

def check_maestro_flow(rel, path, text, known, allow_text, is_subflow=False):
    errors, warnings, used = [], [], set()
    for cmd, val in parse_flow_commands(text):
        if cmd == "runFlow":
            # 断链在生成期就拦：引用的 subflow 不存在，到设备上才炸，
            # 而且 select_flows 的反查链也会静默漏选
            target = val.get("file") if isinstance(val, dict) else val
            if isinstance(target, str) and target and not target.startswith("$"):
                tpath = os.path.normpath(os.path.join(os.path.dirname(path), target))
                if not os.path.isfile(tpath):
                    errors.append(f"{rel}: runFlow 引用的文件不存在: {target} → 补上该 subflow 或改引用路径")
            continue
        if cmd not in SELECTOR_CMDS:
            continue
        for kind, sel in selectors_from_value(val):
            if kind == "id":
                used.add(sel)
                if sel not in known:
                    errors.append(f"{rel}: 选择器 '{sel}' 不在契约表中 → 回 Phase 2 补埋点，不要改成 text:")
            elif cmd in TEXT_OK_CMDS:
                pass  # 无错断言按文案匹配是推荐写法
            else:
                msg = f"{rel}: {cmd} 用了文本选择器 '{sel}' → 文案变更或国际化会让它失效，应改用 id"
                (warnings if allow_text else errors).append(msg)

    # subflow 被主 flow 引用，本来就没有 launchApp，不查 clearState
    if not is_subflow and "clearState" not in text:
        warnings.append(f"{rel}: launchApp 未设置 clearState，起始态可能被上一条用例污染")
    for m in RE_HARDCODED_SECRET.finditer(text):
        errors.append(f"{rel}: 疑似硬编码凭据 '{m.group(0)[:40]}...' → 改用 ${{ENV_VAR}} 注入")
    return errors, warnings, used


def check_web_spec(rel, text, known, allow_text):
    errors, warnings, used = [], [], set()
    ids = set(RE_WEB_BYID.findall(text)) | set(RE_WEB_ATTR.findall(text))
    for sel in sorted(ids):
        used.add(sel)
        if sel not in known:
            errors.append(f"{rel}: 选择器 '{sel}' 不在契约表中 → 回 Phase 2 补埋点")
    if RE_WEB_TEXT.search(text):
        msg = (f"{rel}: 用了 getByText 定位 → 等价于移动端的 text: 选择器，"
               f"业务元素应走 byId；无错断言请用 helpers 里的 expectNoErrorText")
        (warnings if allow_text else errors).append(msg)
    for m in RE_HARDCODED_SECRET.finditer(text):
        errors.append(f"{rel}: 疑似硬编码凭据 '{m.group(0)[:40]}...' → 改用环境变量注入")
    return errors, warnings, used


def audit_source(registry, source_root):
    """registry ↔ 源码对账：声称埋过的 identifier 必须真的在声称的文件里。
    Phase 2 改完源码就该跑一次，别等 flow 上设备才发现契约表是空头支票。"""
    errors = []
    for ident, meta in sorted(registry.items()):
        rel = meta.get("file") if isinstance(meta, dict) else None
        if not rel:
            errors.append(f"registry['{ident}'] 缺少 file 字段，无法对账")
            continue
        path = os.path.join(source_root, rel)
        if not os.path.isfile(path):
            errors.append(f"registry['{ident}'] 指向的文件不存在: {rel}")
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
        except OSError as exc:
            errors.append(f"registry['{ident}'] 文件读取失败 {rel}: {exc}")
            continue
        # 要求带引号出现：注释里提一句 id 不算埋点——否则"让对账变绿"的
        # 最省力路径就是加一行注释，这恰是本闸门最该堵的路径形态
        if f"'{ident}'" not in src and f'"{ident}"' not in src:
            errors.append(f"registry['{ident}'] 在 {rel} 里找不到带引号出现的该 identifier —— "
                          f"埋点被删/改名了（注释里提到不算），契约表已过期")
    return errors


# ─────────────────── CLI ───────────────────

def main():
    ap = argparse.ArgumentParser(description="选择器契约闸门")
    ap.add_argument("--flows", help="flow/spec 目录（省略则只做 --source 对账）")
    ap.add_argument("--registry", required=True)
    ap.add_argument("--source", help="Flutter 项目根目录，给出则做 registry↔源码对账")
    ap.add_argument("--allow-text", action="store_true",
                    help="把文本选择器从错误降为警告（不建议，逃生口）")
    args = ap.parse_args()

    if not (args.flows or args.source):
        sys.exit("--flows 与 --source 至少给一个")

    with open(args.registry, encoding="utf-8") as fh:
        registry = json.load(fh)
    known = set(registry.keys())

    errors, warnings, used = [], [], set()

    if args.source:
        errors.extend(audit_source(registry, args.source))

    flows, specs = [], []
    if args.flows:
        if not os.path.isdir(args.flows):
            sys.exit(f"找不到 flow 目录: {args.flows}")
        flows, specs = [], []
        scan_roots = [args.flows]
        # subflow 约定放在 flows 的兄弟目录（避免被 maestro test 当独立用例执行），
        # 但选择器同样要对表校验，否则子 flow 里的 id 会被误报「未使用」或漏检
        sib = os.path.join(os.path.dirname(os.path.abspath(args.flows.rstrip("/"))), "subflows")
        if os.path.isdir(sib):
            scan_roots.append(sib)
        for base in scan_roots:
            for root, _dirs, files in os.walk(base):
                for f in sorted(files):
                    p = os.path.join(root, f)
                    if f.endswith((".yaml", ".yml")):
                        flows.append(p)
                    elif re.search(r"\.(spec|test)\.(ts|js|mjs|tsx)$", f):
                        specs.append(p)
        if not flows and not specs:
            sys.exit(f"{args.flows} 下没有找到任何 flow(.yaml) 或 spec(.spec.ts)")

        for path in flows + specs:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            rel = os.path.relpath(path, os.path.dirname(os.path.abspath(args.flows.rstrip("/"))))
            if path.endswith((".yaml", ".yml")):
                is_sub = f"{os.sep}subflows{os.sep}" in path
                e, w, u = check_maestro_flow(rel, path, text, known, args.allow_text, is_subflow=is_sub)
            else:
                e, w, u = check_web_spec(rel, text, known, args.allow_text)
            errors.extend(e)
            warnings.extend(w)
            used |= u

    unused = sorted(known - used) if args.flows else []

    print(f"契约表 {len(known)} 项"
          + (f"，检查 {len(flows)} 个 flow + {len(specs)} 个 web spec，实际用到 {len(used)} 项"
             if args.flows else "")
          + ("，已与源码对账" if args.source else ""))
    for w in warnings:
        print(f"  [警告] {w}")
    for e in errors:
        print(f"  [错误] {e}")
    if unused:
        print(f"  [提示] {len(unused)} 个已埋点但未被任何用例使用: {', '.join(unused[:8])}"
              + (" ..." if len(unused) > 8 else ""))

    if errors:
        print(f"\n失败：{len(errors)} 个错误。修掉后再进入 Phase 5。")
        sys.exit(1)
    print("\n通过：所有选择器均可追溯到契约表。")


if __name__ == "__main__":
    main()
