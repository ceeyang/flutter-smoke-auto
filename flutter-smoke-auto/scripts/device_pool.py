#!/usr/bin/env python3
"""设备池：跨会话的模拟器所有权注册表（端内并行车道用）。

解决的问题：多个 Claude 会话/车道各自起 maestro 时抢同一台模拟器——
iOS 上两个自动化会话打同一台会直接打崩 SpringBoard（PITFALLS 2026-08-18），
Android 上则互相污染 clearState。规则：一台设备同时只属于一个 owner。

注册表默认在 ~/.flutter-smoke/device-pool.json（机器级：模拟器是机器资源，
跨项目跨会话共享一份账本）。FSA_DEVICE_POOL 可覆盖（测试用）。

用法:
    device_pool.py claim --platform ios --owner sessionA            # 自动挑：已启动的优先
    device_pool.py claim --platform ios --model "iPhone 15" --owner sessionA
    device_pool.py claim --udid <UDID> --platform ios --owner sessionA   # 指定设备
    device_pool.py assign --udid <UDID> --platform android --owner sessionA --pin
                                                                    # 用户手动分配并锁定
    device_pool.py release --mine --owner sessionA                  # 释放自己名下（pin 的不动）
    device_pool.py release --udid <UDID> --owner sessionA [--unpin] # --unpin 代表用户明示解除
    device_pool.py list [--json]

上限（claim 时核算，assign 是用户意志不设限）:
    - 每端并发 ≤ FSA_MAX_PER_PLATFORM（默认 2）
    - 内存预算：FSA_MEM_GB（默认读系统）- 8G 预留 ≥ 已认领成本 + 新设备成本
      成本按 android 3G / ios 2.5G 估。--force 可越过，后果自负。

owner 建议用 CLAUDE_SESSION_ID；陈旧锁（>2h）只在 list 里标 STALE 提示人工处理，
不自动回收——抢错一台正在跑的设备比泄漏一把锁贵得多。

退出码: 0 成功 / 1 拒绝（冲突、上限、pin） / 2 参数或环境错误
"""

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time

REG_PATH = os.environ.get("FSA_DEVICE_POOL",
                          os.path.expanduser("~/.flutter-smoke/device-pool.json"))
COST_GB = {"android": 3.0, "ios": 2.5}
RESERVED_GB = 8.0
STALE_SECONDS = 2 * 3600


def total_mem_gb():
    env = os.environ.get("FSA_MEM_GB")
    if env:
        return float(env)
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"],
                                 capture_output=True, text=True).stdout.strip()
            return int(out) / (1024 ** 3)
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal"):
                    return int(line.split()[1]) / (1024 ** 2)
    except Exception:
        pass
    return 16.0  # 探测不到就按 16G 保守算


class Registry:
    """文件锁保护的注册表。with 块内读改写都是原子的。"""

    def __enter__(self):
        os.makedirs(os.path.dirname(REG_PATH) or ".", exist_ok=True)
        self.fh = open(REG_PATH, "a+")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        self.fh.seek(0)
        text = self.fh.read().strip()
        self.data = json.loads(text) if text else {"devices": {}}
        return self

    def save(self):
        tmp = REG_PATH + f".{os.getpid()}.tmp"
        with open(tmp, "w") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, REG_PATH)

    def __exit__(self, *exc):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()
        return False


def discover(platform, model):
    """自动挑设备：已启动的优先（边际内存成本 0），其次可启动的。"""
    try:
        if platform == "ios":
            out = subprocess.run(["xcrun", "simctl", "list", "devices", "available", "-j"],
                                 capture_output=True, text=True).stdout
            devs = [d for v in json.loads(out)["devices"].values() for d in v]
            if model:
                devs = [d for d in devs if model.lower() in d.get("name", "").lower()]
            devs.sort(key=lambda d: d.get("state") != "Booted")  # Booted 在前
            return [(d["udid"], d.get("name", ""), d.get("state", "")) for d in devs]
        out = subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout
        booted = [(l.split("\t")[0], "", "Booted") for l in out.splitlines()[1:]
                  if "\t" in l and l.split("\t")[1].strip() == "device"]
        avds = subprocess.run(["emulator", "-list-avds"],
                              capture_output=True, text=True).stdout.split()
        if model:
            avds = [a for a in avds if model.lower() in a.lower()]
        return booted + [(a, a, "Shutdown") for a in avds]
    except Exception as exc:
        sys.exit(f"设备发现失败: {exc}")


def check_caps(reg, platform, force):
    cap = int(os.environ.get("FSA_MAX_PER_PLATFORM", "2"))
    same = [d for d in reg.data["devices"].values() if d["platform"] == platform]
    if len(same) >= cap:
        print(f"拒绝：{platform} 端已认领 {len(same)} 台，达到每端上限 {cap}"
              f"（FSA_MAX_PER_PLATFORM 可调）。先 release 或换端。")
        return False
    budget = total_mem_gb() - RESERVED_GB
    used = sum(COST_GB.get(d["platform"], 1.0) for d in reg.data["devices"].values())
    need = COST_GB.get(platform, 1.0)
    if used + need > budget:
        if force:
            print(f"警告：内存预算超限（已用 {used:.1f}G + 新增 {need:.1f}G > "
                  f"预算 {budget:.1f}G），--force 放行，机器卡顿自负。")
            return True
        print(f"拒绝：内存预算不足——已认领 {used:.1f}G + 新增 {need:.1f}G > "
              f"可用 {budget:.1f}G（总内存-预留8G）。释放设备或 --force。")
        return False
    return True


def cmd_claim(args):
    with Registry() as reg:
        devs = reg.data["devices"]
        if args.udid:
            cur = devs.get(args.udid)
            if cur:
                if cur["owner"] == args.owner:
                    print(f"已在名下（幂等）：{args.udid} owner={args.owner}")
                    return 0
                print(f"拒绝：{args.udid} 已被 {cur['owner']} 占用"
                      f"（{'pin' if cur.get('pinned') else 'claim'}，"
                      f"{int((time.time()-cur['claimed_at'])/60)} 分钟前）。")
                return 1
            if not check_caps(reg, args.platform, args.force):
                return 1
            devs[args.udid] = {"platform": args.platform, "model": args.model or "",
                               "owner": args.owner, "pinned": False,
                               "claimed_at": time.time()}
            reg.save()
            print(f"已认领：{args.udid} → {args.owner}")
            return 0
        # 自动挑选
        for udid, name, state in discover(args.platform, args.model):
            if udid in devs:
                continue
            if not check_caps(reg, args.platform, args.force):
                return 1
            devs[udid] = {"platform": args.platform, "model": name,
                          "owner": args.owner, "pinned": False,
                          "claimed_at": time.time()}
            reg.save()
            boot_hint = "" if state == "Booted" else "（未启动，跑之前记得 boot）"
            print(f"已认领：{udid} {name} → {args.owner} {boot_hint}")
            return 0
        print(f"拒绝：没有可认领的 {args.platform} 设备"
              + (f"（型号含 '{args.model}'）" if args.model else "") + "。")
        return 1


def cmd_assign(args):
    with Registry() as reg:
        cur = reg.data["devices"].get(args.udid)
        if cur and cur["owner"] != args.owner and cur.get("pinned"):
            print(f"拒绝：{args.udid} 已被 pin 给 {cur['owner']}，先对它 release --unpin。")
            return 1
        reg.data["devices"][args.udid] = {
            "platform": args.platform, "model": args.model or "",
            "owner": args.owner, "pinned": bool(args.pin), "claimed_at": time.time()}
        reg.save()
        print(f"已分配：{args.udid} → {args.owner}" + ("（pinned）" if args.pin else ""))
        return 0


def cmd_release(args):
    with Registry() as reg:
        devs = reg.data["devices"]
        if args.udid:
            cur = devs.get(args.udid)
            if not cur:
                print(f"{args.udid} 不在注册表里，无需释放。")
                return 0
            if cur.get("pinned") and not args.unpin:
                print(f"拒绝：{args.udid} 是用户 pin 的分配，释放需要 --unpin（代表用户明示解除）。")
                return 1
            del devs[args.udid]
            reg.save()
            print(f"已释放：{args.udid}")
            return 0
        if args.mine:
            gone = [u for u, d in devs.items()
                    if d["owner"] == args.owner and not d.get("pinned")]
            for u in gone:
                del devs[u]
            reg.save()
            kept = [u for u, d in devs.items() if d["owner"] == args.owner]
            print(f"已释放 {len(gone)} 台" + (f"，pin 的保留：{kept}" if kept else ""))
            return 0
        print("release 需要 --udid 或 --mine")
        return 2


def cmd_list(args):
    with Registry() as reg:
        devs = reg.data["devices"]
        if args.json:
            print(json.dumps(devs, ensure_ascii=False, indent=2))
            return 0
        if not devs:
            print("设备池为空。")
            return 0
        now = time.time()
        for udid, d in sorted(devs.items()):
            age_min = int((now - d["claimed_at"]) / 60)
            flags = []
            if d.get("pinned"):
                flags.append("PIN")
            if now - d["claimed_at"] > STALE_SECONDS:
                flags.append("STALE")
            print(f"{udid}  {d['platform']:<7} {d.get('model',''):<20} "
                  f"owner={d['owner']}  {age_min}min  {' '.join(flags)}")
        if any(now - d["claimed_at"] > STALE_SECONDS for d in devs.values()):
            print("\nSTALE = 占用超 2 小时。确认那个会话已死后手动 release，"
                  "不自动回收——抢错正在跑的设备比泄漏锁贵。")
        return 0


def main():
    ap = argparse.ArgumentParser(description="模拟器设备池")
    sub = ap.add_subparsers(dest="cmd", required=True)

    default_owner = os.environ.get("CLAUDE_SESSION_ID", "")

    c = sub.add_parser("claim")
    c.add_argument("--platform", required=True, choices=["android", "ios"])
    c.add_argument("--udid", help="指定设备；省略则自动挑（已启动的优先）")
    c.add_argument("--model", help="型号过滤，如 'iPhone 15' 或 avd 名子串")
    c.add_argument("--owner", default=default_owner)
    c.add_argument("--force", action="store_true", help="越过内存预算（不越过所有权）")
    c.set_defaults(func=cmd_claim)

    a = sub.add_parser("assign")
    a.add_argument("--udid", required=True)
    a.add_argument("--platform", required=True, choices=["android", "ios"])
    a.add_argument("--model")
    a.add_argument("--owner", required=True)
    a.add_argument("--pin", action="store_true", help="锁定：只有用户明示（release --unpin）能解除")
    a.set_defaults(func=cmd_assign)

    r = sub.add_parser("release")
    r.add_argument("--udid")
    r.add_argument("--mine", action="store_true")
    r.add_argument("--owner", default=default_owner)
    r.add_argument("--unpin", action="store_true")
    r.set_defaults(func=cmd_release)

    l = sub.add_parser("list")
    l.add_argument("--json", action="store_true")
    l.set_defaults(func=cmd_list)

    args = ap.parse_args()
    if getattr(args, "owner", None) == "" and args.cmd in ("claim", "release"):
        sys.exit("需要 --owner（或设置 CLAUDE_SESSION_ID 环境变量）标识会话身份")
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
