// 放到 <项目>/.smoke/flows/web/ 下使用。
// 服务由 run_smoke.sh 起（python http.server 指向 build/web），
// 这里只读 SMOKE_BASE_URL；单独调试时手动起服务再跑 npx playwright test。
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  timeout: 90_000,            // 与「单条用例 90 秒」的冒烟纪律一致
  retries: 0,                 // 冒烟不静默重试；flaky 交给分诊规则处理
  workers: 1,                 // 串行，避免共享后端状态互相污染
  use: {
    baseURL: process.env.SMOKE_BASE_URL || 'http://localhost:8788',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
