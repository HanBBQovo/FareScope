import { useEffect, useMemo, useState, type ElementType } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BellRing,
  ChartNoAxesCombined,
  DatabaseZap,
  Gauge,
  LogOut,
  Menu,
  PlaneTakeoff,
  Search,
  Tags,
} from 'lucide-react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'

import { getSession, logout, sessionQueryKey } from '@/api/auth'
import { ThemeToggleButton } from '@/components/theme'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { BRAND_NAME } from '@/config'
import { clearIdentityCache } from '@/lib/identity-cache'
import { cn } from '@/lib/utils'

interface NavItem {
  path: string
  label: string
  description: string
  icon: ElementType
}

const navGroups: Array<{ label: string; items: NavItem[] }> = [
  {
    label: '价格监控',
    items: [
      { path: '/overview', label: '总览', description: '价格与订阅摘要', icon: Gauge },
      { path: '/explore', label: '航线探索', description: '查询当前航班报价', icon: Search },
      { path: '/subscriptions', label: '订阅管理', description: '维护个人价格提醒', icon: Tags },
      { path: '/history', label: '价格历史', description: '查看长期价格走势', icon: ChartNoAxesCombined },
    ],
  },
  {
    label: '系统',
    items: [
      { path: '/collection', label: '采集状态', description: '检查任务健康度', icon: DatabaseZap },
      { path: '/notifications', label: '通知与告警', description: '配置推送渠道与价格规则', icon: BellRing },
    ],
  },
]

const flatNavItems = navGroups.flatMap((group) => group.items)

function Brand() {
  return (
    <div className="flex h-16 items-center gap-3 border-b px-4">
      <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
        <PlaneTakeoff className="size-5" aria-hidden="true" />
      </div>
      <div className="min-w-0">
        <div className="truncate text-base font-semibold">{BRAND_NAME}</div>
        <div className="truncate text-xs text-muted-foreground">机票价格观察台</div>
      </div>
    </div>
  )
}

function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex flex-col gap-5 px-3 py-4" aria-label="主导航">
      {navGroups.map((group) => (
        <div key={group.label} className="flex flex-col gap-1">
          <p className="px-3 pb-1 text-xs font-medium text-muted-foreground">{group.label}</p>
          {group.items.map((item) => {
            const Icon = item.icon
            return (
              <NavLink
                key={item.path}
                to={item.path}
                onClick={onNavigate}
                className={({ isActive }) => cn(
                  'flex min-h-10 items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:bg-muted hover:text-foreground',
                )}
              >
                <Icon className="size-4 shrink-0" aria-hidden="true" />
                <span className="truncate">{item.label}</span>
              </NavLink>
            )
          })}
        </div>
      ))}
    </nav>
  )
}

export default function Dashboard() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const session = useQuery({ queryKey: sessionQueryKey, queryFn: getSession, staleTime: 60_000 })
  const currentItem = useMemo(
    () => flatNavItems.find((item) => location.pathname.startsWith(item.path)) || flatNavItems[0],
    [location.pathname],
  )

  useEffect(() => {
    document.title = `${currentItem.label} - ${BRAND_NAME}`
  }, [currentItem.label])

  const handleLogout = async () => {
    await logout()
    clearIdentityCache(queryClient)
    navigate('/login', { replace: true })
  }

  return (
    <TooltipProvider delayDuration={120}>
      <div className="flex min-h-screen bg-muted/30">
        <aside className="fixed inset-y-0 left-0 hidden w-60 border-r bg-background md:flex md:flex-col">
          <Brand />
          <div className="min-h-0 flex-1 overflow-y-auto">
          <Navigation />
          </div>
          <div className="border-t p-3">
            <div className="flex items-center gap-3 rounded-md px-2 py-2">
              <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-secondary text-xs font-semibold text-secondary-foreground">
                {session.data?.user?.username.slice(0, 1).toUpperCase() || 'F'}
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{session.data?.user?.username || 'FareScope 用户'}</div>
                <div className="truncate text-xs text-muted-foreground">用户名登录</div>
              </div>
            </div>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col md:pl-60">
          <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b bg-background/95 px-4 backdrop-blur md:px-6">
            <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
              <SheetTrigger asChild>
                <Button type="button" variant="outline" size="icon" className="md:hidden" aria-label="打开导航">
                  <Menu aria-hidden="true" />
                </Button>
              </SheetTrigger>
              <SheetContent side="left" className="flex flex-col p-0" hideClose>
                <SheetHeader className="sr-only">
                  <SheetTitle>FareScope 导航</SheetTitle>
                </SheetHeader>
                <Brand />
                <div className="min-h-0 flex-1 overflow-y-auto">
                    <Navigation onNavigate={() => setMobileOpen(false)} />
                </div>
              </SheetContent>
            </Sheet>

            <div className="min-w-0 flex-1">
              <h1 className="truncate text-base font-semibold">{currentItem.label}</h1>
              <p className="hidden truncate text-xs text-muted-foreground sm:block">{currentItem.description}</p>
            </div>

            <ThemeToggleButton compact />
            <Tooltip>
              <TooltipTrigger asChild>
                <Button type="button" variant="outline" size="icon" onClick={handleLogout} aria-label="退出登录">
                  <LogOut aria-hidden="true" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>退出登录</TooltipContent>
            </Tooltip>
          </header>

          <main className="min-w-0 flex-1 p-4 md:p-6">
            <Outlet />
          </main>
        </div>
      </div>
    </TooltipProvider>
  )
}
