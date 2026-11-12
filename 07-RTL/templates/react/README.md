# RTL React 原型源码

模板使用 React 18、Umi 4 和 Ant Design 5。依赖版本见 `package.json` 和 `package-lock.json`。页面和路由位于 `src/pages/` 与 `.umirc.ts`，共享布局与样式位于 `src/layouts/`。

启动开发环境

```sh
npm ci
npm run dev
```

开发时使用 Umi 显示的本地地址。用 `npm run build` 将评审成品生成到 `dist/`，再双击成品目录中的 `index.html`。当前样例保存浏览器本地偏好。

## 设计规范与主题

视觉值按 RTL 的云呼叫设计规范。根主题与组件 Token 在 `src/layouts/PrototypeLayout.jsx`，外壳几何和表格头体高度在 `src/layouts/prototype.css`。场景需要提示或确认时，使用 `App.useApp()` 读取应用上下文。更改共用主题时用实际渲染复验字号、控件尺寸、卡片、表格和弹层。
