#!/usr/bin/env python3
"""闸门脚本的回归测试。

每个用例都是一次真实的作弊/误用路径，来自对闸门的实测：
先有失败（闸门放行了不该放的），后有修复。改闸门前先跑这个。

用法:
    python3 tests/test_gates.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTEGRITY = os.path.join(SKILL_DIR, "scripts", "check_test_integrity.py")
REGISTRY = os.path.join(SKILL_DIR, "scripts", "check_registry.py")
SELECT = os.path.join(SKILL_DIR, "scripts", "select_flows.py")


def sh(cmd, cwd, **kw):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, **kw)


class GitRepo:
    """临时 git 仓库，模拟目标项目。"""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="fsa-test-")
        sh(["git", "init", "-q", "."], self.dir)
        sh(["git", "config", "user.email", "t@t"], self.dir)
        sh(["git", "config", "user.name", "t"], self.dir)

    def write(self, rel, content):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)

    def commit(self, msg="c"):
        sh(["git", "add", "-A"], self.dir)
        sh(["git", "commit", "-qm", msg], self.dir)

    def rm(self, rel):
        os.remove(os.path.join(self.dir, rel))

    def integrity(self, *args):
        return sh([sys.executable, INTEGRITY, *args], self.dir)

    def registry_check(self, *args):
        return sh([sys.executable, REGISTRY, *args], self.dir)

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


FLOW_TWO_ASSERTS = """appId: com.example.app
---
- launchApp:
    clearState: true
- assertVisible:
    id: "home_root"
- assertVisible:
    id: "feed_item"
"""


class TestIntegrityGate(unittest.TestCase):
    def setUp(self):
        self.repo = GitRepo()

    def tearDown(self):
        self.repo.cleanup()

    def test_smoke_flows_dir_is_recognized_as_test_path(self):
        """曾经的洞：.smoke/flows 不在 TEST_PATH 里，整个默认布局对闸门不可见。"""
        self.repo.write(".smoke/flows/smoke-01.yaml", FLOW_TWO_ASSERTS)
        self.repo.commit()
        # 删一条断言 + 给剩下那条加 optional: true
        self.repo.write(".smoke/flows/smoke-01.yaml", """appId: com.example.app
---
- launchApp:
    clearState: true
- assertVisible:
    id: "home_root"
    optional: true
""")
        r = self.repo.integrity("--base", "HEAD")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("optional", r.stdout)

    def test_untracked_new_flow_is_scanned(self):
        """曾经的洞：首轮生成、未 commit 的 flow 对 git diff 不可见。"""
        self.repo.write("README.md", "x")
        self.repo.commit()
        self.repo.write(".smoke/flows/new.yaml", """appId: a
---
- assertVisible:
    id: "x"
    optional: true
""")
        r = self.repo.integrity("--base", "HEAD")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_assert_visible_to_exists_downgrade_blocked(self):
        """曾经的洞：文档声称拦 assertVisible→assertExists，实际 WEAK_MATCHER 里没有。"""
        self.repo.write("e2e/f.yaml", 'appId: a\n---\n- assertVisible:\n    id: "review_detail_title"\n')
        self.repo.commit()
        self.repo.write("e2e/f.yaml", 'appId: a\n---\n- assertExists:\n    id: "review_detail_title"\n')
        r = self.repo.integrity("--base", "HEAD")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("matcher_weakened", r.stdout)

    def test_assertion_target_swap_warned(self):
        """断言目标从业务元素换成容器：至少要在输出里留痕（warn），供人复核。"""
        self.repo.write("e2e/f.yaml", 'appId: a\n---\n- assertVisible:\n    id: "review_detail_title"\n')
        self.repo.commit()
        self.repo.write("e2e/f.yaml", 'appId: a\n---\n- assertVisible:\n    id: "app_root_container"\n')
        r = self.repo.integrity("--base", "HEAD")
        self.assertIn("assertion_target_changed", r.stdout, r.stdout + r.stderr)

    # ── 回归保护：原有能力不能因为修洞而丢 ──

    def test_pure_addition_passes(self):
        self.repo.write("e2e/f.yaml", 'appId: a\n---\n- assertVisible:\n    id: "x"\n')
        self.repo.commit()
        self.repo.write("e2e/f.yaml",
                        'appId: a\n---\n- assertVisible:\n    id: "x"\n- assertVisible:\n    id: "y"\n')
        r = self.repo.integrity("--base", "HEAD")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_deleted_test_file_blocked(self):
        self.repo.write("test/a_test.dart", "void main() { expect(1, 1); }")
        self.repo.commit()
        self.repo.rm("test/a_test.dart")
        r = self.repo.integrity("--base", "HEAD")
        self.assertEqual(r.returncode, 1)
        self.assertIn("test_file_deleted", r.stdout)

    def test_added_skip_blocked(self):
        self.repo.write("spec/x.spec.ts", 'test("a", () => { expect(a).toBe(1); });\n')
        self.repo.commit()
        self.repo.write("spec/x.spec.ts", 'test.skip("a", () => { expect(a).toBe(1); });\n')
        r = self.repo.integrity("--base", "HEAD")
        self.assertEqual(r.returncode, 1)

    def test_timeout_inflation_blocked(self):
        self.repo.write("e2e/f.yaml", "appId: a\n---\n- waitForAnimationToEnd:\n    timeout: 5000\n")
        self.repo.commit()
        self.repo.write("e2e/f.yaml", "appId: a\n---\n- waitForAnimationToEnd:\n    timeout: 30000\n")
        r = self.repo.integrity("--base", "HEAD")
        self.assertEqual(r.returncode, 1)
        self.assertIn("timeout_inflated", r.stdout)

    def test_app_code_not_scanned(self):
        """业务代码里的 verify/assert 字样不该触发闸门。"""
        self.repo.write("lib/crypto/flows/verify.dart", "bool verify(x) => assertValid(x);\n")
        self.repo.commit()
        self.repo.write("lib/crypto/flows/verify.dart", "bool verify(x) => true;\n")
        r = self.repo.integrity("--base", "HEAD")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_committed_weakening_caught_via_state_baseline(self):
        """曾经的洞：弱化改动先 commit 再跑闸门 → 默认 --base HEAD 的 diff 为空，全盲。
        默认基准应优先取 .smoke/state.json 记录的冒烟起点 commit。"""
        self.repo.write("e2e/f.yaml", FLOW_TWO_ASSERTS)
        self.repo.commit()
        head = sh(["git", "rev-parse", "HEAD"], self.repo.dir).stdout.strip()
        self.repo.write(".smoke/state.json", json.dumps({"commit": head}))
        self.repo.write("e2e/f.yaml", """appId: com.example.app
---
- launchApp:
    clearState: true
- assertVisible:
    id: "home_root"
    optional: true
""")
        self.repo.commit("weakened and committed")
        r = self.repo.integrity()          # 不带 --base：默认基准应读 state.json
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("optional", r.stdout)

    def test_block_output_does_not_teach_bypass(self):
        """闸门失败输出里不能印可直接复制的绕过开关——放行流程属于给人看的文档，
        不该出现在 agent 会读到的错误流里。"""
        self.repo.write("e2e/f.yaml", 'appId: a\n---\n- assertVisible:\n    id: "x"\n')
        self.repo.commit()
        self.repo.rm("e2e/f.yaml")
        r = self.repo.integrity("--base", "HEAD")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("--warn-only", r.stdout)


REG = {"login_submit_btn": {"file": "lib/login.dart", "type": "button"},
       "home_root": {"file": "lib/home.dart", "type": "screen"}}


class TestRegistryGate(unittest.TestCase):
    def setUp(self):
        self.repo = GitRepo()
        self.repo.write(".smoke/registry.json", json.dumps(REG))

    def tearDown(self):
        self.repo.cleanup()

    def check(self, *extra):
        return self.repo.registry_check(
            "--flows", ".smoke/flows", "--registry", ".smoke/registry.json", *extra)

    def test_quoted_text_selector_is_error(self):
        """曾经的洞：- tapOn: "登录" 带引号时正则漏检，且 text 只是 warning。"""
        self.repo.write(".smoke/flows/f.yaml",
                        'appId: a\n---\n- launchApp:\n    clearState: true\n- tapOn: "登录"\n')
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_inline_map_text_selector_is_error(self):
        self.repo.write(".smoke/flows/f.yaml",
                        'appId: a\n---\n- launchApp:\n    clearState: true\n- assertVisible: { text: "首页" }\n')
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_error_text_regex_in_assert_not_visible_allowed(self):
        """无错断言（assertNotVisible + 错误文案正则）是 skill 自己推荐的写法，不能拦。"""
        self.repo.write(".smoke/flows/f.yaml",
                        'appId: a\n---\n- launchApp:\n    clearState: true\n'
                        '- assertVisible:\n    id: "home_root"\n'
                        '- assertNotVisible:\n    text: ".*(错误|失败|Error).*"\n')
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_unknown_id_is_error(self):
        self.repo.write(".smoke/flows/f.yaml",
                        'appId: a\n---\n- launchApp:\n    clearState: true\n- tapOn:\n    id: "ghost_btn"\n')
        r = self.check()
        self.assertEqual(r.returncode, 1)
        self.assertIn("ghost_btn", r.stdout)

    def test_allow_text_downgrades(self):
        self.repo.write(".smoke/flows/f.yaml",
                        'appId: a\n---\n- launchApp:\n    clearState: true\n- tapOn: "登录"\n')
        r = self.check("--allow-text")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_env_var_input_not_flagged(self):
        self.repo.write(".smoke/flows/f.yaml",
                        'appId: a\n---\n- launchApp:\n    clearState: true\n'
                        '- tapOn:\n    id: "login_submit_btn"\n- inputText: "${TEST_PHONE}"\n')
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_input_text_literal_not_treated_as_selector(self):
        """inputText 的值是输入内容不是选择器，不该被当 text 选择器拦。"""
        self.repo.write(".smoke/flows/f.yaml",
                        'appId: a\n---\n- launchApp:\n    clearState: true\n'
                        '- tapOn:\n    id: "login_submit_btn"\n- inputText: "smoke test 20260815"\n')
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_source_audit_catches_registry_drift(self):
        """--source 对账：registry 声称的 identifier 不在源码里 → 错误。"""
        self.repo.write("lib/login.dart",
                        "Semantics(identifier: 'login_submit_btn', child: btn)")
        self.repo.write("lib/home.dart", "// identifier 被人删了")
        self.repo.write(".smoke/flows/f.yaml",
                        'appId: a\n---\n- launchApp:\n    clearState: true\n- tapOn:\n    id: "login_submit_btn"\n')
        r = self.check("--source", ".")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("home_root", r.stdout)

    def test_web_spec_unknown_id_is_error(self):
        """Web 端（Playwright spec）的 byId 选择器同样必须在契约表内。"""
        self.repo.write(".smoke/flows/web/smoke.spec.ts",
                        "import { byId, launchApp } from './helpers';\n"
                        "test('smoke', async ({ page }) => {\n"
                        "  await launchApp(page);\n"
                        "  await byId(page, 'ghost_btn').click();\n"
                        "});\n")
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("ghost_btn", r.stdout)

    def test_web_spec_get_by_text_is_error(self):
        """Web spec 里用 getByText 定位业务元素 = 移动端的 text: 选择器，同样拦。"""
        self.repo.write(".smoke/flows/web/smoke.spec.ts",
                        "test('smoke', async ({ page }) => {\n"
                        "  await page.getByText('登录').click();\n"
                        "});\n")
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_source_audit_comment_mention_is_still_drift(self):
        """曾经的洞：对账是子串检查，注释里提一句 id 就能通过——
        自愈压力下"让对账变绿"的最省力路径恰好是加一行注释。
        对账要求 identifier 以带引号形式存在于源码。"""
        self.repo.write("lib/login.dart", "// TODO: login_submit_btn moved elsewhere\nclass LoginPage {}\n")
        self.repo.write("lib/home.dart", "Semantics(identifier: 'home_root', child: x)")
        r = self.repo.registry_check("--registry", ".smoke/registry.json", "--source", ".")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("login_submit_btn", r.stdout)

    def test_runflow_missing_target_is_error(self):
        """runFlow 引用的 subflow 不存在：生成期就拦断链，别等设备上执行才发现。"""
        self.repo.write(".smoke/flows/f.yaml",
                        'appId: a\n---\n- launchApp:\n    clearState: true\n'
                        '- runFlow:\n    file: ../subflows/nope.yaml\n'
                        '- assertVisible:\n    id: "home_root"\n')
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("nope.yaml", r.stdout)

    def test_web_spec_known_id_passes(self):
        self.repo.write(".smoke/flows/web/smoke.spec.ts",
                        "test('smoke', async ({ page }) => {\n"
                        "  await byId(page, 'login_submit_btn').click();\n"
                        "});\n")
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_subflows_are_scanned(self):
        """实测反馈：登录抽成 subflow 后，里面用到的 id 被误报「未使用」，
        表外 id 也漏检。subflows 目录（flows 的兄弟目录）必须纳入扫描。"""
        self.repo.write(".smoke/flows/f.yaml",
                        'appId: a\n---\n- launchApp:\n    clearState: true\n'
                        '- runFlow:\n    file: ../subflows/login.yaml\n'
                        '- assertVisible:\n    id: "home_root"\n')
        self.repo.write(".smoke/subflows/login.yaml",
                        '- tapOn:\n    id: "login_submit_btn"\n- tapOn:\n    id: "ghost_in_subflow"\n')
        r = self.check()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("ghost_in_subflow", r.stdout)          # subflow 里的表外 id 要拦
        self.assertNotIn("未被任何用例使用", r.stdout)         # login_submit_btn 已在 subflow 用到

    def test_subflow_no_clear_state_warning(self):
        """subflow 没有 launchApp 是正常的（被主 flow 引用），不该报 clearState 警告。"""
        self.repo.write(".smoke/flows/f.yaml",
                        'appId: a\n---\n- launchApp:\n    clearState: true\n'
                        '- assertVisible:\n    id: "home_root"\n- tapOn:\n    id: "login_submit_btn"\n')
        self.repo.write(".smoke/subflows/login.yaml",
                        '- tapOn:\n    id: "login_submit_btn"\n')
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("clearState", r.stdout)

    def test_missing_clear_state_warned(self):
        self.repo.write(".smoke/flows/f.yaml",
                        'appId: a\n---\n- launchApp\n- tapOn:\n    id: "login_submit_btn"\n')
        r = self.check()
        self.assertIn("clearState", r.stdout)


FLOW_LOGIN = ('appId: a\n---\n- launchApp:\n    clearState: true\n'
              '- tapOn:\n    id: "login_submit_btn"\n- assertVisible:\n    id: "home_root"\n')
FLOW_FEED = ('appId: a\n---\n- launchApp:\n    clearState: true\n'
             '- assertVisible:\n    id: "feed_list"\n')
FLOW_COLD = ('appId: a\n---\n- launchApp:\n    clearState: true\n'
             '- assertVisible:\n    id: "home_root"\n')


class TestSelectFlows(unittest.TestCase):
    """定向执行的选用例工具：改动/关键词 → 受影响用例清单（永远含冷启动锚点）。"""

    def setUp(self):
        self.repo = GitRepo()
        self.repo.write(".smoke/registry.json", json.dumps({
            "login_submit_btn": {"file": "lib/auth/login_page.dart"},
            "home_root": {"file": "lib/home/home_page.dart"},
            "feed_list": {"file": "lib/home/feed.dart"},
        }))
        self.repo.write(".smoke/flows/smoke-01-cold-start.yaml", FLOW_COLD)
        self.repo.write(".smoke/flows/smoke-02-login.yaml", FLOW_LOGIN)
        self.repo.write(".smoke/flows/smoke-03-feed.yaml", FLOW_FEED)
        self.repo.write("lib/auth/login_page.dart", "id: login_submit_btn")
        self.repo.write("lib/home/feed.dart", "id: feed_list")
        self.repo.commit()

    def tearDown(self):
        self.repo.cleanup()

    def select(self, *args):
        return sh([sys.executable, SELECT, "--flows", ".smoke/flows",
                   "--registry", ".smoke/registry.json", *args], self.repo.dir)

    def test_changed_maps_to_affected_flow_plus_cold_start(self):
        self.repo.write("lib/auth/login_page.dart", "id: login_submit_btn  // edited")
        r = self.select("--changed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("smoke-02-login.yaml", r.stdout)
        self.assertIn("smoke-01-cold-start.yaml", r.stdout)   # 冷启动锚点永远带上
        self.assertNotIn("smoke-03-feed.yaml", r.stdout)      # 不相关的不跑

    def test_keyword_selection(self):
        r = self.select("--keyword", "login")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("smoke-02-login.yaml", r.stdout)
        self.assertIn("smoke-01-cold-start.yaml", r.stdout)
        self.assertNotIn("smoke-03-feed.yaml", r.stdout)

    def test_id_in_subflow_maps_back_to_main_flow(self):
        """id 只出现在 subflow 里时，要反查到引用它的主 flow。"""
        self.repo.write(".smoke/subflows/login.yaml", '- tapOn:\n    id: "login_submit_btn"\n')
        self.repo.write(".smoke/flows/smoke-02-login.yaml",
                        'appId: a\n---\n- launchApp:\n    clearState: true\n'
                        '- runFlow:\n    file: ../subflows/login.yaml\n'
                        '- assertVisible:\n    id: "home_root"\n')
        self.repo.commit()
        self.repo.write("lib/auth/login_page.dart", "id: login_submit_btn  // edited")
        r = self.select("--changed")
        self.assertIn("smoke-02-login.yaml", r.stdout, r.stdout + r.stderr)

    def test_no_mapping_warns_and_returns_cold_start_only(self):
        """改动映射不到任何用例：只跑冷启动，并提醒可能要为新功能补用例。"""
        self.repo.write("lib/brand_new/widget.dart", "new stuff")
        r = self.select("--changed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("smoke-01-cold-start.yaml", r.stdout)
        self.assertNotIn("smoke-02-login.yaml", r.stdout)
        self.assertIn("补用例", r.stderr)

    def test_changed_logic_file_falls_back_to_feature_dir(self):
        """曾经的洞：改无埋点的逻辑文件（service 层）→ 精确映射为空 → 只跑冷启动还全绿。
        应按 feature 目录归属圈入同目录埋点对应的用例。"""
        self.repo.write("lib/auth/auth_service.dart", "class AuthService {}")
        self.repo.commit()
        self.repo.write("lib/auth/auth_service.dart", "class AuthService { login() {} }")
        r = self.select("--changed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("smoke-02-login.yaml", r.stdout)
        self.assertNotIn("smoke-03-feed.yaml", r.stdout)

    def test_keyword_ignores_subflow_reference_in_body(self):
        """曾经的洞：--only login 做全文内容匹配，所有引用 login subflow 的用例
        都含 "login" 字样 → 定向近似全量。关键词只匹配文件名/用例名。"""
        self.repo.write(".smoke/flows/smoke-03-feed.yaml",
                        'appId: a\n---\n- launchApp:\n    clearState: true\n'
                        '- runFlow:\n    file: ../subflows/login.yaml\n'
                        '- assertVisible:\n    id: "feed_list"\n')
        r = self.select("--keyword", "login")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("smoke-02-login.yaml", r.stdout)
        self.assertNotIn("smoke-03-feed.yaml", r.stdout)

    def test_web_keyword_selects_spec(self):
        self.repo.write(".smoke/flows/web/smoke-01-cold-start.spec.ts", "byId(page,'home_root')")
        self.repo.write(".smoke/flows/web/smoke-02-login.spec.ts", "byId(page,'login_submit_btn')")
        r = self.select("--keyword", "login", "--web")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("smoke-02-login.spec.ts", r.stdout)
        self.assertIn("smoke-01-cold-start.spec.ts", r.stdout)
        self.assertNotIn("feed", r.stdout)

    def test_failed_selects_failed_flows_plus_cold_start(self):
        """真实项目实测的洞：修复轮没有"只重跑失败用例"的工具入口，
        agent 图省事整轮全量重跑（同一失败集连跑两遍）。--failed 读上一轮
        junit，只圈失败用例 + 冷启动。maestro 的 testcase name 取 flow 的
        name: 字段。"""
        self.repo.write(".smoke/flows/smoke-02-login.yaml",
                        'appId: a\nname: SMOKE-02 登录\n---\n'
                        '- launchApp:\n    clearState: true\n'
                        '- tapOn:\n    id: "login_submit_btn"\n')
        self.repo.write(".smoke/runs/1/artifacts/results.xml",
                        '<?xml version="1.0"?><testsuites><testsuite tests="3" failures="1">'
                        '<testcase name="SMOKE-02 登录"><failure>boom</failure></testcase>'
                        '<testcase name="smoke-03-feed"/>'
                        '<testcase name="smoke-01-cold-start"/>'
                        '</testsuite></testsuites>')
        r = self.select("--failed", ".smoke/runs/1/artifacts/results.xml")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("smoke-02-login.yaml", r.stdout)
        self.assertIn("smoke-01-cold-start.yaml", r.stdout)   # 冷启动锚点永远带上
        self.assertNotIn("smoke-03-feed.yaml", r.stdout)      # 上轮过了的不重跑

    def test_failed_matches_by_filename_stem_when_no_name(self):
        """flow 没写 name: 字段时 maestro 用文件名主干当 testcase name。"""
        self.repo.write(".smoke/runs/1/artifacts/results.xml",
                        '<testsuite><testcase name="smoke-03-feed"><failure/></testcase>'
                        '<testcase name="smoke-02-login"/></testsuite>')
        r = self.select("--failed", ".smoke/runs/1/artifacts/results.xml")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("smoke-03-feed.yaml", r.stdout)
        self.assertNotIn("smoke-02-login.yaml", r.stdout)

    def test_failed_all_green_refuses(self):
        """上一轮全绿：没有可重跑的失败，明确拒绝（退出码 2）——
        而不是静默退化成空跑或全量。"""
        self.repo.write(".smoke/runs/1/artifacts/results.xml",
                        '<testsuite><testcase name="smoke-02-login"/></testsuite>')
        r = self.select("--failed", ".smoke/runs/1/artifacts/results.xml")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("全绿", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
