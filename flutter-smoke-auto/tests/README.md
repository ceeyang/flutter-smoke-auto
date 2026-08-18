# 本 skill 自身的测试

给维护/使用这个 skill 的 agent 看的。分两层：**闸门回归测试**（可复用，进 git，
永久保留）和**真实项目验证**（临时产物，测完即删）。

## 1. 闸门回归测试（改脚本前后必跑）

```bash
python3 tests/test_gates.py        # 零第三方依赖，~3 秒
python3 tests/test_screen.py       # screen.py 纯函数（diff/inspect/坐标换算），需 Pillow，没装自动跳过
```

覆盖 `check_test_integrity.py`、`check_registry.py`、`select_flows.py`。每个用例都是一条真实的
作弊/误用路径——其中多条是这两个脚本**曾经实测放行过**的洞（`.smoke` 路径不识别、
未跟踪文件盲区、`assertVisible→assertExists` 降级、带引号的 text 选择器漏检）。

**改闸门脚本的纪律（TDD）：**

1. 先写一个会失败的用例，复现你要拦的作弊路径 / 要修的误报，跑一遍确认 RED；
2. 改脚本让它变绿；
3. 全套重跑，原有用例一个都不能红——闸门的每一次放松都必须在这里留下痕迹。

新用例的写法照抄现有模式：`GitRepo` 建临时仓库 → 写基线文件 → commit →
做出"作弊"改动 → 断言退出码和输出关键字。临时仓库用 `tempfile` 自动清理，
不会污染任何目录。

## 2. 在真实项目上验证 skill 本身（临时，测完删）

首次把 skill 用于真实项目时，按下面的清单逐项核对。验证过程中产生的
一切临时文件（试跑的 flow、造的数据、截图）放 scratchpad 或 `.smoke/runs/`
（后者在 `.gitignore` 里），**不要 commit**；验证结论如果值得留，
写进项目的 `docs/` 或本 skill 的 PITFALLS，而不是留一堆临时脚本。

| # | 验证点 | 怎么验 | 通过标准 |
|---|---|---|---|
| 1 | 扫描器有效 | Phase 1 跑 `scan_app.py`，人工抽查 top10 候选 | 主流程页面都在候选里，无大量误报 |
| 2 | 契约表落地 | Phase 2 后跑 `check_registry.py --registry ... --source .` | 对账零错误 |
| 3 | 选择器闸门 | 故意在 flow 里写一个表外 id 和一个 `text:` 选择器 | 两者都被拦（exit 1） |
| 4 | 三端定位一致 | 同一个 id 在 Android/iOS/Web 各点一次 | 三端都能命中 |
| 5 | 完整性闸门 | 故意删一条断言 / 加 `optional: true` 再跑闸门 | 被拦，且回滚后通过 |
| 6 | 自愈纪律 | 观察前三轮自愈的 `.smoke/report.md` 自愈记录 | 每条修改都有证据列 |
| 7 | 阈值校准 | 看 `screen.py diff/inspect` 在该项目上的误判率 | 记录需要调整的阈值 |

第 3、5 条是**故意作弊测试**：做完立刻 `git checkout` 恢复，别把假 bug 留在项目里。

校准出的阈值改动（900px / 0.005 / 0.88 / 3 倍线）如果通用，改回本 skill 并提交；
只对某项目成立的，写进该项目的 `.claude/PITFALLS.md`。
