#!/usr/bin/env python3
"""按资源写冲突把用例分成可并行的车道（端内并行用）。

独立性是声明出来的，不是猜出来的：flow 头部 tags 里写
    tags: [smoke, mutates-posts]      # 写了 posts 资源
    tags: [smoke, readonly]           # 只读
分车道规则：
    - mutates 相同资源（含传递闭包）→ 同一车道（车道内串行）
    - readonly → 摊平到最短车道
    - 没有任何资源声明 → 视为互相冲突，全部同一车道并提醒（保守优先）
    - 冷启动锚点用例只进 lane-1，全局一次

用法:
    shard_flows.py --flows .smoke/flows --lanes 2 --out .smoke/lanes
输出 .smoke/lanes/lane-<i>.txt（每行一个 flow 路径），供
    run_smoke.sh --from-list .smoke/lanes/lane-1.txt --device <该车道设备>
消费。Web 端不需要本工具：playwright workers 的 context 天然隔离，
并行钥匙在账号（helpers.ts 的 laneEnv）。

退出码: 0 正常 / 2 参数或环境错误
"""

import argparse
import os
import re
import sys

RE_TAG_ITEM = re.compile(r"^\s*-\s*([A-Za-z0-9_\-]+)\s*$", re.M)
RE_TAGS_INLINE = re.compile(r"^tags\s*:\s*\[([^\]]*)\]", re.M)
COLD_HINT = re.compile(r"smoke[-_]?0*1\b|cold|launch", re.I)


def read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def tags_of(text):
    """取 --- 之前 header 里的 tags（块列表或行内列表）。"""
    header = text.split("\n---", 1)[0]
    m = RE_TAGS_INLINE.search(header)
    if m:
        return {t.strip().strip("'\"") for t in m.group(1).split(",") if t.strip()}
    m = re.search(r"^tags\s*:\s*$", header, re.M)
    if not m:
        return set()
    tail = header[m.end():]
    tags = set()
    for line in tail.splitlines():
        if not line.strip():
            continue
        im = RE_TAG_ITEM.match(line)
        if not im:
            break  # tags 块结束
        tags.add(im.group(1))
    return tags


class UnionFind:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def main():
    ap = argparse.ArgumentParser(description="按写冲突分车道")
    ap.add_argument("--flows", required=True)
    ap.add_argument("--lanes", type=int, default=2)
    ap.add_argument("--out", required=True, help="车道清单输出目录")
    args = ap.parse_args()

    if not os.path.isdir(args.flows):
        sys.exit(f"找不到 flow 目录: {args.flows}")
    if args.lanes < 1:
        sys.exit("--lanes 至少为 1")

    mains = []
    for root, dirs, files in os.walk(args.flows):
        dirs[:] = [d for d in dirs if d not in ("web", "subflows")]
        for f in sorted(files):
            if f.endswith((".yaml", ".yml")):
                mains.append(os.path.join(root, f))
    if not mains:
        sys.exit(f"{args.flows} 下没有 flow")

    cold = next((f for f in sorted(mains) if COLD_HINT.search(os.path.basename(f))), None)

    groups_uf = UnionFind()          # 节点 = flow 路径与资源名（加前缀区分）
    readonly, unlabeled = [], []
    for f in mains:
        if f == cold:
            continue
        tags = tags_of(read(f))
        res = {t[len("mutates-"):] for t in tags if t.startswith("mutates-")}
        if res:
            for r in res:
                groups_uf.union("f:" + f, "r:" + r)
        elif "readonly" in tags:
            readonly.append(f)
        else:
            unlabeled.append(f)

    # 未声明资源的用例：视为互相冲突（共享伪资源），保守串行
    for f in unlabeled:
        groups_uf.union("f:" + f, "r:__undeclared__")
    if unlabeled:
        print(f"提醒：{len(unlabeled)} 条用例未声明资源标签（mutates-*/readonly），"
              f"按互相冲突保守处理，全部串行在同一车道。给它们补标签才能并行。",
              file=sys.stderr)

    # 聚组
    groups = {}
    for key in list(groups_uf.p):
        if key.startswith("f:"):
            groups.setdefault(groups_uf.find(key), []).append(key[2:])

    # 大组优先塞进当前最短车道
    lanes = [[] for _ in range(args.lanes)]
    for group in sorted(groups.values(), key=len, reverse=True):
        min(lanes, key=len).extend(sorted(group))
    for f in sorted(readonly):
        min(lanes, key=len).append(f)
    if cold:
        lanes[0].insert(0, cold)

    lanes = [l for l in lanes if l]
    os.makedirs(args.out, exist_ok=True)
    for old in os.listdir(args.out):
        if old.startswith("lane-") and old.endswith(".txt"):
            os.remove(os.path.join(args.out, old))
    for i, lane in enumerate(lanes, 1):
        path = os.path.join(args.out, f"lane-{i}.txt")
        with open(path, "w") as fh:
            fh.write("\n".join(lane) + "\n")
        print(f"lane-{i}: {len(lane)} 条 → {path}")
        for f in lane:
            print(f"    {os.path.basename(f)}")


if __name__ == "__main__":
    main()
