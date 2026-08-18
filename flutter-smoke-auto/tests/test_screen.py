#!/usr/bin/env python3
"""screen.py 纯函数部分的回归测试：diff / inspect 阈值判定、坐标换算。

不需要设备。需要 Pillow（screen.py 本来就依赖它），没装则整体跳过。
errors 子命令依赖真机/模拟器，无法在这里覆盖——改它之后按 tests/README.md
的真实项目验证清单在设备上确认一次。

用法:
    python3 tests/test_screen.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

try:
    from PIL import Image
except ImportError:
    print("跳过 test_screen.py：未安装 Pillow（pip install Pillow）")
    sys.exit(0)

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(SKILL_DIR, "scripts")
SCREEN = os.path.join(SCRIPTS, "screen.py")

sys.path.insert(0, SCRIPTS)
import screen  # noqa: E402


def run_screen(*args):
    return subprocess.run([sys.executable, SCREEN, *args],
                          capture_output=True, text=True)


class TestDiff(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="screen-test-")

    def img(self, name, color, size=(100, 200)):
        path = os.path.join(self.dir, name)
        Image.new("RGB", size, color).save(path)
        return path

    def test_identical_frames_no_change(self):
        a = self.img("a.png", (120, 130, 140))
        b = self.img("b.png", (120, 130, 140))
        r = run_screen("diff", a, b)
        self.assertEqual(json.loads(r.stdout)["verdict"], "no_change", r.stdout + r.stderr)

    def test_page_switch_is_changed(self):
        a = self.img("a.png", (255, 255, 255))
        b = self.img("b.png", (30, 30, 30))
        r = run_screen("diff", a, b)
        self.assertEqual(json.loads(r.stdout)["verdict"], "changed", r.stdout + r.stderr)


class TestInspect(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="screen-test-")

    def test_blank_screen_flagged(self):
        path = os.path.join(self.dir, "blank.png")
        Image.new("RGB", (100, 200), (255, 255, 255)).save(path)
        r = run_screen("inspect", path)
        self.assertIn("blank_screen", json.loads(r.stdout)["flags"], r.stdout + r.stderr)

    def test_red_error_screen_flagged(self):
        path = os.path.join(self.dir, "red.png")
        Image.new("RGB", (100, 200), (211, 47, 47)).save(path)   # Flutter debug 红屏色
        r = run_screen("inspect", path)
        self.assertIn("flutter_error_screen", json.loads(r.stdout)["flags"], r.stdout + r.stderr)

    def test_normal_content_ok(self):
        path = os.path.join(self.dir, "normal.png")
        img = Image.new("RGB", (100, 200))
        px = img.load()
        for x in range(100):
            for y in range(200):
                px[x, y] = ((x * 37) % 256, (y * 53) % 256, ((x + y) * 11) % 256)
        img.save(path)
        r = run_screen("inspect", path)
        self.assertTrue(json.loads(r.stdout)["ok"], r.stdout + r.stderr)


class TestToDevice(unittest.TestCase):
    """坐标换算：L3 最容易错的一环，缩放比和越界判定不能回归。"""

    def ref(self, scale, device=(1080, 2400)):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump({"scale": scale, "device_size": list(device),
                       "image_size": [round(device[0] * scale), round(device[1] * scale)]}, fh)
        return path

    def test_image_coords_scaled_back_to_device(self):
        self.assertEqual(screen.to_device(200, 400, self.ref(0.5), "image"), (400, 800))

    def test_device_space_passthrough(self):
        self.assertEqual(screen.to_device(200, 400, None, "device"), (200, 400))

    def test_out_of_bounds_rejected(self):
        with self.assertRaises(SystemExit):
            screen.to_device(2000, 400, self.ref(0.5), "image")   # 换算后 x=4000 > 1080

    def test_image_space_without_ref_rejected(self):
        with self.assertRaises(SystemExit):
            screen.to_device(10, 10, None, "image")


if __name__ == "__main__":
    unittest.main(verbosity=2)
