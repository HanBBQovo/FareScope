# FareScope Web

FareScope 的正式 Web 前端。当前产品骨架覆盖总览、航线探索、订阅管理、价格历史、采集状态和通知设置，页面通过类型化 API 层与后端解耦。

技术栈：Vite 6、React 19、TypeScript、React Router、TanStack Query、Tailwind CSS 3、shadcn/ui（Radix）、Lucide、Recharts。

## 本地运行

```bash
npm install
npm run dev
npm run lint
npm run build
```

开发服务器默认运行在 `http://localhost:5278`，并把 `/api` 代理到 `APP_API_PORT`（默认 `16824`）；按仓库 README 启动时显式设置为 API 使用的端口。

## 数据模式

- 正式请求统一经过 `src/api/client.ts`，使用服务端 HttpOnly Cookie 会话。
- 票价领域契约集中在 `src/api/fares.ts`，页面不直接调用 `fetch`。
- 注册和登录只使用用户名与密码，密码最少 4 位、没有复杂度规则，也不需要重复确认、邮箱或邮箱验证。
- 页面只展示真实 API 数据；失败、空数据和过期数据均使用明确状态，不回退到演示报价。

## 路由

| 地址 | 页面 |
| --- | --- |
| `/overview` | 价格与订阅总览 |
| `/explore` | 单程/往返航班查询、直飞筛选 |
| `/subscriptions` | 用户订阅与阈值管理 |
| `/history` | 持久化价格观测趋势 |
| `/collection` | 采集任务、拦截与解析失败状态 |
| `/notifications` | 告警规则、事件、投递记录以及 Webhook、PushPlus、Telegram、Bark 渠道 |

页面采用 `lazy()` 分包，`Dashboard.tsx` 只负责产品外壳与导航，具体页面放在 `src/pages/`。

## 开发约定

1. 新接口先在 `src/api/` 定义类型和查询函数，再在页面中使用 TanStack Query。
2. 所有服务端状态使用稳定的 query key，写操作成功后更新或失效对应缓存。
3. 页面必须处理 loading、empty、error 和数据来源标识。
4. 金额在接口中使用分为单位，展示前统一除以 `100`。
5. 表单使用 `FieldGroup` / `Field`，组件优先复用 `src/components/ui/`。
6. 新页面必须增加正式路由和侧栏入口，并通过 `npm run lint && npm run build`。

## 当前边界

- 邮箱仅保留为未来可选通知渠道，不参与账号注册或登录；当前没有 SMTP 发送后端。
- 生产构建显式拆分 React、查询、Radix、图标和图表依赖；当前构建无大 chunk 警告。
- 价格预测、购买建议等能力只有在历史样本量和数据质量达到门槛后才应开放。
