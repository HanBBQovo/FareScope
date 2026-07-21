export interface SegmentConnectionPoint {
  arrivalAt: string
  destination: string
  destinationName: string
  destinationTerminal: string | null
}

export interface SegmentDeparturePoint {
  departureAt: string
  origin: string
  originName: string
  originTerminal: string | null
}

export interface LocalDateTimeParts {
  date: string
  time: string
}

export function formatDurationMinutes(value: number): string {
  const safeMinutes = Number.isFinite(value) ? Math.max(0, Math.round(value)) : 0
  const hours = Math.floor(safeMinutes / 60)
  const minutes = safeMinutes % 60
  if (hours === 0) return `${minutes} 分钟`
  if (minutes === 0) return `${hours} 小时`
  return `${hours} 小时 ${minutes} 分钟`
}

export function localDateTimeParts(value: string): LocalDateTimeParts {
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/)
  if (!match) return { date: value, time: '--:--' }
  return {
    date: `${match[1]}年${Number(match[2])}月${Number(match[3])}日`,
    time: `${match[4]}:${match[5]}`,
  }
}

export function calculateLayoverMinutes(
  current: SegmentConnectionPoint,
  next: SegmentDeparturePoint,
): number | null {
  const arrival = Date.parse(current.arrivalAt)
  const departure = Date.parse(next.departureAt)
  if (!Number.isFinite(arrival) || !Number.isFinite(departure) || departure < arrival) return null
  return Math.round((departure - arrival) / 60_000)
}

export function terminalChangeLabel(
  current: SegmentConnectionPoint,
  next: SegmentDeparturePoint,
): string | null {
  const arrivalTerminal = current.destinationTerminal?.trim()
  const departureTerminal = next.originTerminal?.trim()
  if (!arrivalTerminal || !departureTerminal) return null
  if (arrivalTerminal === departureTerminal) return `${arrivalTerminal} 航站楼内中转`
  return `${arrivalTerminal} → ${departureTerminal}，需要换航站楼`
}
