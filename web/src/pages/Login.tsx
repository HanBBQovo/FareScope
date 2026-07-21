import { useEffect, useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, LogIn, PlaneTakeoff, UserPlus } from 'lucide-react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'

import { getSession, login, register, sessionQueryKey } from '@/api/auth'
import { ThemeToggleButton } from '@/components/theme'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { BRAND_NAME } from '@/config'

type AuthMode = 'login' | 'register'

const MIN_REGISTRATION_PASSWORD_LENGTH = 4

export default function Login() {
  const [mode, setMode] = useState<AuthMode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const session = useQuery({ queryKey: sessionQueryKey, queryFn: getSession, retry: false })
  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname || '/overview'
  const isRegister = mode === 'register'

  useEffect(() => {
    document.title = `${isRegister ? '注册' : '登录'} - ${BRAND_NAME}`
  }, [isRegister])

  if (session.data?.authenticated) return <Navigate to={from} replace />

  const switchMode = (nextMode: string) => {
    setMode(nextMode as AuthMode)
    setError('')
    setPassword('')
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')

    const normalizedUsername = username.trim().toLowerCase()
    if (!/^[a-z0-9][a-z0-9_.-]{2,63}$/.test(normalizedUsername)) {
      setError('用户名需为 3-64 位字母、数字、下划线、点或短横线，且以字母或数字开头')
      return
    }
    if (isRegister && password.length < MIN_REGISTRATION_PASSWORD_LENGTH) {
      setError(`密码至少需要 ${MIN_REGISTRATION_PASSWORD_LENGTH} 个字符`)
      return
    }

    setSubmitting(true)
    try {
      const nextSession = isRegister
        ? await register(normalizedUsername, password)
        : await login(normalizedUsername, password)
      queryClient.setQueryData(sessionQueryKey, nextSession)
      navigate(from, { replace: true })
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : `${isRegister ? '注册' : '登录'}失败`)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-dvh bg-background">
      <div className="fixed right-4 top-4">
        <ThemeToggleButton compact />
      </div>
      <main className="mx-auto flex w-full max-w-md flex-col justify-center px-4 py-12 sm:px-6">
        <div className="mb-8 flex items-center gap-3">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <PlaneTakeoff className="size-5" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <h1 className="text-xl font-semibold">{BRAND_NAME}</h1>
            <p className="text-sm text-muted-foreground">机票价格观察台</p>
          </div>
        </div>

        <div className="flex flex-col gap-6">
          <div className="flex flex-col gap-1">
            <h2 className="text-lg font-semibold">{isRegister ? '创建账户' : '登录账户'}</h2>
            <p className="text-sm text-muted-foreground">
              {isRegister ? '创建账户后即可保存航线订阅、价格历史和通知渠道。' : '访问你的航线订阅、历史价格和通知设置。'}
            </p>
          </div>

          <Tabs value={mode} onValueChange={switchMode} className="w-full">
            <TabsList className="grid h-10 w-full grid-cols-2">
              <TabsTrigger value="login"><LogIn data-icon="inline-start" />登录</TabsTrigger>
              <TabsTrigger value="register"><UserPlus data-icon="inline-start" />注册</TabsTrigger>
            </TabsList>
          </Tabs>

          {error ? (
            <Alert variant="destructive">
              <AlertTitle>{isRegister ? '注册失败' : '登录失败'}</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          <form onSubmit={handleSubmit} className="flex flex-col gap-6">
            <FieldGroup>
              <Field data-invalid={Boolean(error) || undefined}>
                <FieldLabel htmlFor="username">用户名</FieldLabel>
                <Input
                  id="username"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  autoComplete="username"
                  autoFocus
                  required
                  minLength={3}
                  maxLength={64}
                  aria-invalid={Boolean(error)}
                />
                {isRegister ? <FieldDescription>3-64 位字母、数字、下划线、点或短横线</FieldDescription> : null}
              </Field>
              <Field data-invalid={Boolean(error) || undefined}>
                <FieldLabel htmlFor="password">密码</FieldLabel>
                <Input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete={isRegister ? 'new-password' : 'current-password'}
                  required
                  minLength={isRegister ? MIN_REGISTRATION_PASSWORD_LENGTH : undefined}
                  aria-invalid={Boolean(error)}
                />
                {isRegister ? <FieldDescription>至少 4 个字符，无复杂度要求</FieldDescription> : null}
              </Field>
            </FieldGroup>

            <Button
              type="submit"
              className="w-full"
              disabled={submitting || !username.trim() || !password}
            >
              {submitting ? <Loader2 data-icon="inline-start" className="animate-spin" /> : isRegister ? <UserPlus data-icon="inline-start" /> : <LogIn data-icon="inline-start" />}
              {isRegister ? '创建账户' : '登录'}
            </Button>
          </form>
        </div>
      </main>
    </div>
  )
}
