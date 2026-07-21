import { Badge } from '@/components/ui/badge'
import type { PriceStatus } from '@/api/fares'

const statusMeta: Record<PriceStatus, { label: string; variant: 'secondary' | 'outline' | 'destructive' }> = {
  current: { label: '数据新鲜', variant: 'secondary' },
  stale: { label: '数据已过期', variant: 'outline' },
  unavailable: { label: '暂无报价', variant: 'destructive' },
}

export function PriceFreshnessBadge({ status }: { status: PriceStatus }) {
  const meta = statusMeta[status]
  return <Badge variant={meta.variant}>{meta.label}</Badge>
}
