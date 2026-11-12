import { App as AntApp, ConfigProvider } from 'antd';
import { Outlet, useLocation } from 'umi';
import './prototype.css';

export default function PrototypeLayout() {
  const { pathname } = useLocation();
  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: '#1677ff',
          colorPrimaryHover: '#0958d9',
          colorText: '#262626',
          colorTextSecondary: '#595959',
          colorTextDescription: '#8c8c8c',
          colorTextDisabled: '#bfbfbf',
          colorBgLayout: '#f0f2f5',
          colorBgContainer: '#ffffff',
          colorBorder: '#d9d9d9',
          colorBorderSecondary: '#e8e8e8',
          colorFillAlter: '#fafafa',
          controlItemBgActive: '#e6f4ff',
          colorSuccess: '#389e0d',
          colorWarning: '#d46b08',
          colorError: '#cf1322',
          fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif',
          fontSize: 14,
          lineHeight: 1.6,
          borderRadius: 6,
          controlHeight: 34,
          controlHeightSM: 28,
        },
        components: {
          Button: { contentFontSize: 13, contentFontSizeSM: 12 },
          Input: { inputFontSize: 13, inputFontSizeSM: 12 },
          InputNumber: { fontSize: 13 },
          Select: { fontSize: 13, optionFontSize: 13 },
          Form: { labelFontSize: 13, itemMarginBottom: 18 },
          Table: {
            cellFontSize: 13,
            cellFontSizeMD: 13,
            cellFontSizeSM: 13,
            cellPaddingBlock: 14,
            cellPaddingInline: 14,
            headerBg: '#fafafa',
            headerColor: '#595959',
            rowHoverBg: '#f5f8ff',
            lineHeight: 22 / 13,
          },
          Card: { borderRadiusLG: 8, bodyPadding: 16 },
          Modal: { borderRadiusLG: 10, titleFontSize: 17 },
          Pagination: { itemSize: 30, itemSizeSM: 28, borderRadius: 5, fontSize: 12 },
        },
      }}
    >
      <AntApp>
        <div className="prototype-shell">
          <header className="prototype-header">项目原型</header>
          <aside className="prototype-sidebar">
            <nav aria-label="原型导航">
              <a href="#/" aria-current={pathname === '/' ? 'page' : undefined}>
                工作台
              </a>
              <a
                id="rtl-guide-link"
                href="#/guide"
                aria-current={pathname === '/guide' ? 'page' : undefined}
              >
                使用说明
              </a>
            </nav>
          </aside>
          <main className="prototype-main">
            <Outlet />
          </main>
        </div>
      </AntApp>
    </ConfigProvider>
  );
}
