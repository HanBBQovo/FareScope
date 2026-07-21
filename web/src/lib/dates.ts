export function formatDateInput(value: Date): string {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function dateFromToday(days: number, base = new Date()): string {
  const value = new Date(base)
  value.setHours(12, 0, 0, 0)
  value.setDate(value.getDate() + days)
  return formatDateInput(value)
}

export function addDays(date: string | number, days?: number): string {
  const inputDate = typeof date === 'number' ? formatDateInput(new Date()) : date
  const offset = typeof date === 'number' ? date : days || 0
  const [year, month, day] = inputDate.split('-').map(Number)
  const value = new Date(year, month - 1, day, 12)
  value.setDate(value.getDate() + offset)
  return formatDateInput(value)
}

export function minutesToTime(value?: number): string {
  if (value === undefined) return ''
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`
}

export function timeToMinutes(value: string): number | undefined {
  if (!value) return undefined
  const [hours, minutes] = value.split(':').map(Number)
  return hours * 60 + minutes
}
