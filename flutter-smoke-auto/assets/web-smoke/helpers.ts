// Flutter Web 冒烟测试助手。
// 契约：选择器只走 byId()，id 来自 .smoke/registry.json（与移动端共用一份）。
// Flutter 3.19+ 的 Semantics(identifier:) 在 Web 上渲染为 DOM 属性
// flt-semantics-identifier，这是三端共用契约表的基础。
import { Page, expect } from '@playwright/test';

/** 唯一合法的业务元素定位方式。别用 getByText —— check_registry.py 会拦。 */
export const byId = (page: Page, id: string) =>
  page.locator(`[flt-semantics-identifier="${id}"]`);

/**
 * 打开 App 并等语义树就绪。
 * 前提：App 在 SMOKE_TEST 编译开关下调用了 ensureSemantics()（见 flutter-web.md），
 * 否则 Flutter Web 默认不生成语义 DOM，所有 byId 都找不到。
 */
export async function launchApp(page: Page, path = '/') {
  await page.goto(path);
  await page
    .locator('flt-semantics-host [flt-semantics-identifier], [flt-semantics-identifier]')
    .first()
    .waitFor({ state: 'attached', timeout: 20_000 });
}

/** 「无错」业务不变量：错误提示没有 id，只能按文案兜。这是 text 匹配的唯一豁免点。
 *  注意：正文里合法出现这些词的页面（"失败率"、"错误码说明"）会误伤——
 *  那种页面别用全局版，给报错容器埋个 id，改断言 byId(page, '<错误容器id>') 数量为 0。 */
export async function expectNoErrorText(page: Page) {
  await expect(page.getByText(/错误|失败|异常|Error|Failed|Exception/)).toHaveCount(0);
}

/** 「有内容」不变量：列表至少渲染出一项，而不是永久 loading。 */
export async function expectHasContent(page: Page, listId: string) {
  await expect(byId(page, listId).locator('[flt-semantics-identifier]').first())
    .toBeAttached({ timeout: 15_000 });
}
