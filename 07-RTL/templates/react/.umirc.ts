import { defineConfig } from 'umi';

export default defineConfig({
  npmClient: 'npm',
  title: '项目原型',
  publicPath: './',
  history: { type: 'hash' },
  mfsu: false,
  routeLoader: { moduleType: 'cjs' },
  routes: [
    {
      path: '/',
      component: '@/layouts/PrototypeLayout',
      routes: [
        { path: '/', component: '@/pages/Review' },
        { path: '/guide', component: '@/pages/Guide' },
      ],
    },
  ],
});
