#!/usr/bin/env python3
"""shard_flows.py 的回归测试：按资源写冲突分车道。"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARD = os.path.join(SKILL_DIR, "scripts", "shard_flows.py")


def flow(tags, name="t"):
    tag_lines = "\n".join(f"  - {t}" for t in tags)
    return f"appId: a\nname: {name}\ntags:\n{tag_lines}\n---\n- launchApp:\n    clearState: true\n"


class TestShardFlows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fsa-shard-")
        self.flows = os.path.join(self.tmp, ".smoke", "flows")
        self.out = os.path.join(self.tmp, ".smoke", "lanes")
        os.makedirs(self.flows)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, fname, content):
        with open(os.path.join(self.flows, fname), "w") as fh:
            fh.write(content)

    def shard(self, lanes=2):
        return subprocess.run(
            [sys.executable, SHARD, "--flows", self.flows, "--lanes", str(lanes), "--out", self.out],
            capture_output=True, text=True)

    def lanes(self):
        out = {}
        if os.path.isdir(self.out):
            for f in sorted(os.listdir(self.out)):
                with open(os.path.join(self.out, f)) as fh:
                    out[f] = [os.path.basename(l.strip()) for l in fh if l.strip()]
        return out

    def lane_of(self, fname):
        for lane, files in self.lanes().items():
            if fname in files:
                return lane
        return None

    def test_write_conflict_same_lane(self):
        """写同一资源的用例必须同车道（车道内串行）。"""
        self.write("smoke-01-cold-start.yaml", flow(["smoke", "readonly"], "cold"))
        self.write("smoke-02-post.yaml", flow(["smoke", "mutates-posts"], "post"))
        self.write("smoke-03-del-post.yaml", flow(["smoke", "mutates-posts"], "delpost"))
        self.write("smoke-04-profile.yaml", flow(["smoke", "mutates-profile"], "profile"))
        r = self.shard(2)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lane_of("smoke-02-post.yaml"),
                         self.lane_of("smoke-03-del-post.yaml"))

    def test_chained_resources_same_lane(self):
        """资源链传递：A 写 posts+cart，B 写 cart → A、B 同车道。"""
        self.write("smoke-01-cold-start.yaml", flow(["readonly"], "cold"))
        self.write("a.yaml", flow(["mutates-posts", "mutates-cart"], "a"))
        self.write("b.yaml", flow(["mutates-cart"], "b"))
        self.shard(2)
        self.assertEqual(self.lane_of("a.yaml"), self.lane_of("b.yaml"))

    def test_readonly_spread_and_lane_count(self):
        """只读用例摊平到各车道；车道数不超过 --lanes。"""
        self.write("smoke-01-cold-start.yaml", flow(["readonly"], "cold"))
        for i in range(2, 8):
            self.write(f"smoke-0{i}-ro{i}.yaml", flow(["readonly"], f"ro{i}"))
        r = self.shard(3)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lanes = self.lanes()
        self.assertLessEqual(len(lanes), 3)
        self.assertGreater(len(lanes), 1)          # 真的摊开了，不是全塞一条
        sizes = [len(v) for v in lanes.values()]
        self.assertLessEqual(max(sizes) - min(sizes), 2)

    def test_unlabeled_conservative_together(self):
        """没有资源标签的用例视为互相冲突，全部同车道——并行是声明出来的。"""
        self.write("smoke-01-cold-start.yaml", flow(["readonly"], "cold"))
        self.write("u1.yaml", "appId: a\nname: u1\n---\n- launchApp:\n    clearState: true\n")
        self.write("u2.yaml", "appId: a\nname: u2\n---\n- launchApp:\n    clearState: true\n")
        self.write("r1.yaml", flow(["mutates-posts"], "r1"))
        r = self.shard(3)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.lane_of("u1.yaml"), self.lane_of("u2.yaml"))
        self.assertIn("未声明", r.stdout + r.stderr)   # 提醒它们被保守串行了

    def test_cold_start_exactly_once(self):
        self.write("smoke-01-cold-start.yaml", flow(["readonly"], "cold"))
        self.write("a.yaml", flow(["mutates-a"], "a"))
        self.write("b.yaml", flow(["mutates-b"], "b"))
        self.shard(2)
        count = sum(files.count("smoke-01-cold-start.yaml") for files in self.lanes().values())
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
