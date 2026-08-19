// 放到 <项目>/.smoke/flows/web/ 下使用。
// 服务由 run_smoke.sh 起（python http.server 指向 build/web），
// 这里只读 SMOKE_BASE_URL；单独调试时手动起服务再跑 npx playwright test。
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  timeout: 90_000,            // 与「单条用例 90 秒」的冒烟纪律一致
  retries: 0,                 // 冒烟不静默重试；flaky 交给分诊规则处理
  // 并行：每个 worker 独立 browser context（cookie/storage/HTTP 缓存互不可见），
  // 浏览器层天然隔离；会互踩的只有后端账号数据。workers>1 前先确认各 spec
  // 资源独立（readonly / 不同 mutates），账号用 helpers 的 laneEnv 按 worker 分。
  // 默认 1（串行保守）；run_smoke.sh --workers N 或 SMOKE_WORKERS=N 提升。
  workers: Number(process.env.SMOKE_WORKERS ?? 1),
  use: {
    baseURL: process.env.SMOKE_BASE_URL || 'http://localhost:8788',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
