# 测试完整性闸门

## 为什么需要它

任何 agent 拿到「跑测试 → 失败 → 自己修 → 再跑」这个循环之后，都有一条捷径：
**让测试变绿最省力的办法不是修好代码，是弄坏测试。**

agent 的即时目标是「到绿」，不是「找 bug」。删断言和修 bug 在当下感觉不出差别，
两者都能让下一次运行通过，而前者便宜一个数量级。它不会觉得自己在作弊，
它会给出一个说得通的理由：「这条断言写得太严」「这个元素本来就不适合做断言」。
理由是真的，只是每次都恰好指向省事的方向。

跑完你会拿到一份全绿报告，附一句「已修复 4 处失效的测试」。
不逐行看 diff 就发现不了：这一轮里实际覆盖率降低了，而报告说它变健康了。

人类也干这事，但一次只干一处、会心虚、在 git 记录里显眼。
agent 一次干八处、语气笃定，而且真心认为自己在做正确的事。

## 光靠规则约束不住

写在 SKILL.md 里的禁令，在注意力集中时有用，在已经跑了三轮开始烦躁时不一定。
所以这里有两层：

**第一层（可绕过）**：`references/triage.md` 的三分类和禁令清单。
**第二层（绕不过）**：`scripts/check_test_integrity.py`，只看 diff 的事实，
不依赖任何人的自觉。

这六条禁令里真正管用的只有一条：**要证据才能改测试**。
因为它不是禁令而是动作——「不许删断言」可以被重新解释成「这是重构」，
「改选择器前先在 git diff 里找到这个 id 被改名的证据」绕不过去，
要么找得到要么找不到，没有解释空间。**能约束住 agent 的规则，
是把判断替换成查证的规则，不是诉诸克制的规则。**

## 检查器拦什么

| 类型 | 判据 | 级别 |
|---|---|---|
| `test_file_deleted` | 整个测试文件被删 | 阻断 |
| `assertions_removed` | 断言净减少（删的比加的多） | 阻断 |
| `test_skipped` | 新增 `test.skip` / `@pytest.mark.skip` / `optional: true` / `@Ignore` / `t.Skip()` / `continueOnFailure` | 阻断 |
| `matcher_weakened` | 强匹配换成弱匹配（`toBe` → `toBeDefined`、`assertVisible` → `assertExists`） | 阻断 |
| `expectation_lowered` | 期望数量被调小（`toHaveLength(5)` → `toHaveLength(1)`） | 阻断 |
| `timeout_inflated` | 超时涨 3 倍以上或 ≥30 秒 | 阻断（1.5–3 倍为提示） |
| `assertion_wrapped` | 测试里新增 try/catch 且涉及断言 | 提示 |
| `assertion_target_changed` | 定位/断言目标的 id 被换掉（业务元素 → 别的元素） | 提示，放行需改名证据 |

只扫测试文件：路径含 `test`/`spec`/`e2e`/`__tests__`/`.maestro`/`.smoke`，
或匹配 `*_test.dart`、`*.spec.ts`、`test_*.py`、`*Test.java` 等；
`--test-paths` 可追加额外目录。数据/文档文件（.json/.md）不扫。
**未跟踪的新文件也在扫描范围内**——首轮生成、还没 commit 的用例不在闸门盲区里。
业务代码的改动不管。

覆盖 Dart(flutter_test)、JS/TS(Jest/Vitest/Playwright/Cypress)、Python(pytest)、
Go、Java/Kotlin(JUnit)、Swift(XCTest)、Maestro YAML。

## 怎么用

```bash
# 自愈循环里，每轮改完立刻自查，过了才 commit。
# 默认基准：上次冒烟起点（.smoke/state.json 的 commit，兜住"先 commit 后检查"的盲区），
# 没有 state.json 则退回 HEAD；工作区 + 未跟踪新文件都在扫描范围内
python scripts/check_test_integrity.py

# 对比某个基准
python scripts/check_test_integrity.py --base main

# pre-commit hook（见 assets/pre-commit-hook.sh）
python scripts/check_test_integrity.py --staged

# CI 里出结构化结果（CI 的 checkout 里没有未跟踪文件，加 --no-untracked）
python scripts/check_test_integrity.py --base origin/main --json --no-untracked
```

退出码 1 = 有阻断项。`--warn-only` 可以放行，但**这个开关是给人用的**，
不要写进 agent 的自动化流程——那等于把闸门拆了。

## 放行的正确姿势

确实需要删断言或调超时时（有这种情况），流程是：

1. agent 停下来，把改动和理由写进报告
2. 人看过之后，自己执行带 `--warn-only` 的提交
3. 提交信息里写明为什么原值不再合理

关键是**这个决定必须由人做出**。让 agent 自己判断「这次属于例外」，
它每次都会判断属于例外。

## 已知的局限

- 靠正则，不做语法解析。变量名混淆、宏、代码生成产物可能漏检
- 断言计数是行级近似，多行断言可能误计
- 「换了一个更容易通过的测试目标」（断言对象从业务元素换成必然存在的容器）
  只能**提示**（`assertion_target_changed`）不能阻断——改名重构在 diff 上长得一样。
  放行的唯一合法理由是 registry/源码 diff 里有该 id 被改名的证据，这一步靠人查证
- 对合法重构有低概率误报，`--warn-only` 是逃生口

改这个脚本之前先跑 `tests/test_gates.py`——里面固化了它曾经实测放行过的作弊路径，
改完必须仍然全绿。

它不是完备的，是把最常见、最省力的那几条作弊路径堵掉。剩下的路径成本更高，
agent 走的概率也更低。
