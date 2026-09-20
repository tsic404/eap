import { render, type RenderOptions, type RenderResult } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactElement } from "react";

import zhCN from "../messages/zh-CN.json";

/**
 * Renders a component inside `NextIntlClientProvider` (zh-CN catalog) so
 * `useTranslations` resolves during unit tests. Tests assert Chinese copy, so
 * the default locale catalog is loaded.
 */
export function renderWithIntl(
  ui: ReactElement,
  options?: Omit<RenderOptions, "wrapper">,
): RenderResult {
  return render(ui, {
    ...options,
    wrapper: ({ children }) => (
      <NextIntlClientProvider locale="zh-CN" messages={zhCN}>
        {children}
      </NextIntlClientProvider>
    ),
  });
}
