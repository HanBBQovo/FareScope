import { apiRequest } from '@/api/client'

export type ExportFormat = 'csv' | 'json'
export type ExportStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'expired' | 'deleting'

export interface PriceExportJob {
  id: string
  subscriptionId: string | null
  format: ExportFormat
  scope: 'canonical_query'
  status: ExportStatus
  rangeStart: string
  rangeEnd: string
  snapshotAt: string
  attempt: number
  maxAttempts: number
  processedRows: number
  rowCount: number | null
  sizeBytes: number | null
  checksumSha256: string | null
  fileName: string | null
  errorCode: string | null
  errorMessage: string | null
  createdAt: string
  startedAt: string | null
  completedAt: string | null
  expiresAt: string | null
  downloadReady: boolean
}

export interface PriceExportList {
  meta: { mode: 'live' | 'demo'; generatedAt: string }
  items: PriceExportJob[]
  hasMore: boolean
  nextCursor: string | null
}

export interface CreatePriceExportInput {
  subscriptionId: string
  format: ExportFormat
  rangeStart: string
  rangeEnd: string
  idempotencyKey: string
}

export const exportQueryKeys = {
  all: ['price-exports'] as const,
  list: (subscriptionId: string) => [...exportQueryKeys.all, 'list', subscriptionId] as const,
}

export async function getPriceExports(subscriptionId: string, cursor?: string): Promise<PriceExportList> {
  const query = new URLSearchParams({ subscriptionId, limit: '50' })
  if (cursor) query.set('cursor', cursor)
  return apiRequest<PriceExportList>(`/exports?${query.toString()}`)
}

export async function createPriceExport(input: CreatePriceExportInput): Promise<PriceExportJob> {
  return apiRequest<PriceExportJob>('/exports', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export async function deletePriceExport(jobId: string): Promise<void> {
  await apiRequest(`/exports/${jobId}`, { method: 'DELETE' })
}

export function priceExportDownloadUrl(jobId: string): string {
  return `/api/exports/${encodeURIComponent(jobId)}/download`
}
