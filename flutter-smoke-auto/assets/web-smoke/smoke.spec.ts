// Web 冒烟用例模板 — 复制后按 .smoke/plan.md 对应用例填充。
// 与移动端 flow 一一对应：同一份 plan.md、同一份 registry.json。
// 命名: smoke-<序号>-<动作>.spec.ts
import { test } from '@playwright/test';
import { byId, launchApp, expectNoErrorText, expectHasContent } from './helpers';

test('SMOKE-01 冷启动到首页', async ({ page }) => {
  await launchApp(page);
  // 断言 1/3 到达
  await byId(page, 'home_root').waitFor({ timeout: 15_000 });
  // 断言 2/3 无错
  await expectNoErrorText(page);
  // 断言 3/3 有内容
  await expectHasContent(page, 'home_feed_list');
});

test('SMOKE-02 登录', async ({ page }) => {
  await launchApp(page, '/login');
  await byId(page, 'login_phone_input').click();
  await page.keyboard.type(process.env.TEST_PHONE ?? '13800000000');
  await byId(page, 'login_submit_btn').click();
  await byId(page, 'home_root').waitFor({ timeout: 15_000 });
  await expectNoErrorText(page);
});
