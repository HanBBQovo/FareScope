import { Info } from 'lucide-react'

import type { ResponseMeta } from '@/api/fares'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'

export function DataModeNotice({ meta }: { meta: ResponseMeta }) {
  if (meta.mode === 'live') return null
  return (
    <Alert>
      <Info aria-hidden="true" />
      <AlertDescription className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">服务端演示模式</Badge>
        当前响应由服务端明确标记为演示模式；前端不会在接口失败时生成或替换报价数据。
      </AlertDescription>
    </Alert>
  )
}
