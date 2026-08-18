#!/usr/bin/env bash
# 测试完整性 pre-commit hook
#
# 安装:
#   cp assets/pre-commit-hook.sh .git/hooks/pre-commit
#   chmod +x .git/hooks/pre-commit
#
# 作用: agent（或人）提交前自动检查暂存区里有没有弱化测试的改动。
# 这一层的价值在于它不依赖任何人的自觉——agent 改完自己就被拦下，
# 不需要它记得去跑检查。

set -e

# 查找顺序：仓库内固定位置（推荐，随项目走）→ 项目级 skill → 全局 skill
CHECKER=""
for c in \
  ".smoke/scripts/check_test_integrity.py" \
  "${SKILL_DIR:-.claude/skills/flutter-smoke-auto}/scripts/check_test_integrity.py" \
  "$HOME/.claude/skills/flutter-smoke-auto/scripts/check_test_integrity.py"; do
  [[ -f "$c" ]] && { CHECKER="$c"; break; }
done

if [[ -z "$CHECKER" ]]; then
  echo "跳过测试完整性检查：找不到 check_test_integrity.py"
  echo "（按 SKILL.md「首次落地」一节把它拷到 .smoke/scripts/）"
  exit 0
fi

python3 "$CHECKER" --staged || {
  echo
  echo "提交被拦下。以上改动会降低测试的实际覆盖——回滚它，把该项按 APP_DEFECT 上报。"
  echo
  echo "如果这是必要的改动，放行流程见 references/test-integrity.md「放行的正确姿势」："
  echo "由人工复核理由并亲自执行提交。不要让 agent 自己决定跳过这一步。"
  exit 1
}
