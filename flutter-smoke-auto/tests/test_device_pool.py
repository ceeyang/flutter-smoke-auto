#!/usr/bin/env python3
"""device_pool.py 的回归测试：设备认领/所有权/pin/上限。

注册表路径用 FSA_DEVICE_POOL 指到临时文件，内存用 FSA_MEM_GB 注入，
不碰真实模拟器（claim 走 --udid 直连路径，不做设备发现）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL = os.path.join(SKILL_DIR, "scripts", "device_pool.py")


class TestDevicePool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fsa-pool-")
        self.reg = os.path.join(self.tmp, "pool.json")
        self.env = dict(os.environ, FSA_DEVICE_POOL=self.reg, FSA_MEM_GB="24")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pool(self, *args):
        return subprocess.run([sys.executable, POOL, *args],
                              capture_output=True, text=True, env=self.env)

    def claim(self, udid, owner, platform="ios", *extra):
        return self.pool("claim", "--udid", udid, "--owner", owner,
                         "--platform", platform, *extra)

    def test_claim_and_conflict(self):
        r = self.claim("SIM-A", "sessionA")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # 别的会话抢同一台 → 拒绝并说明被谁占用
        r2 = self.claim("SIM-A", "sessionB")
        self.assertEqual(r2.returncode, 1, r2.stdout + r2.stderr)
        self.assertIn("sessionA", r2.stdout + r2.stderr)
        # 同一会话重复 claim 幂等
        r3 = self.claim("SIM-A", "sessionA")
        self.assertEqual(r3.returncode, 0, r3.stdout + r3.stderr)

    def test_release_mine_only(self):
        self.claim("SIM-A", "sessionA")
        self.claim("EMU-1", "sessionB", "android")
        r = self.pool("release", "--mine", "--owner", "sessionA")
        self.assertEqual(r.returncode, 0)
        data = json.load(open(self.reg))
        self.assertNotIn("SIM-A", data["devices"])
        self.assertIn("EMU-1", data["devices"])      # 别人的不动

    def test_pinned_needs_explicit_unpin(self):
        """用户 pin 的分配：普通 release 拿不掉，--unpin（代表用户明示）才行。"""
        r = self.pool("assign", "--udid", "SIM-A", "--owner", "sessionA",
                      "--platform", "ios", "--pin")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r2 = self.pool("release", "--udid", "SIM-A", "--owner", "sessionA")
        self.assertEqual(r2.returncode, 1)
        self.assertIn("pin", (r2.stdout + r2.stderr).lower())
        r3 = self.pool("release", "--udid", "SIM-A", "--owner", "sessionA", "--unpin")
        self.assertEqual(r3.returncode, 0)
        self.assertNotIn("SIM-A", json.load(open(self.reg))["devices"])

    def test_per_platform_cap(self):
        """移动端每端默认最多 2 台。"""
        self.claim("SIM-A", "sessionA")
        self.claim("SIM-B", "sessionB")
        r = self.claim("SIM-C", "sessionC")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("2", r.stdout + r.stderr)

    def test_memory_budget(self):
        """内存预算：FSA_MEM_GB=12 预留 8G 后只剩 4G，两台 android(3G) 装不下。"""
        env = dict(self.env, FSA_MEM_GB="12")
        run = lambda *a: subprocess.run([sys.executable, POOL, *a],
                                        capture_output=True, text=True, env=env)
        r1 = run("claim", "--udid", "EMU-1", "--owner", "a", "--platform", "android")
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        r2 = run("claim", "--udid", "EMU-2", "--owner", "b", "--platform", "android")
        self.assertEqual(r2.returncode, 1, r2.stdout + r2.stderr)
        self.assertIn("内存", r2.stdout + r2.stderr)
        # --force 放行
        r3 = run("claim", "--udid", "EMU-2", "--owner", "b", "--platform", "android", "--force")
        self.assertEqual(r3.returncode, 0, r3.stdout + r3.stderr)

    def test_list_marks_stale(self):
        self.claim("SIM-A", "sessionA")
        data = json.load(open(self.reg))
        data["devices"]["SIM-A"]["claimed_at"] = 0        # 1970 年，必然陈旧
        json.dump(data, open(self.reg, "w"))
        r = self.pool("list")
        self.assertEqual(r.returncode, 0)
        self.assertIn("STALE", r.stdout)
        # 陈旧锁只提示不自动回收：claim 冲突依旧拒绝
        r2 = self.claim("SIM-A", "sessionB")
        self.assertEqual(r2.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
