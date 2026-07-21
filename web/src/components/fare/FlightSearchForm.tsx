import { useMemo, useState, type FormEvent } from 'react'
import { AlertCircle, ArrowRightLeft, Search } from 'lucide-react'

import type { FlightSearchParams, TripType } from '@/api/fares'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { dateFromToday, minutesToTime, timeToMinutes } from '@/lib/dates'
import { defaultFlightSearchValues } from '@/lib/flight-search'

interface FlightSearchFormProps {
  initialValues?: Partial<FlightSearchParams>
  submitting?: boolean
  onSubmit: (values: FlightSearchParams) => void
}

function normalizeCodeList(value: string | undefined): string | undefined {
  const codes = value
    ?.split(',')
    .map((code) => code.trim().toUpperCase())
    .filter(Boolean)
  return codes?.length ? codes.join(',') : undefined
}

export function FlightSearchForm({ initialValues, submitting = false, onSubmit }: FlightSearchFormProps) {
  const defaults = useMemo(defaultFlightSearchValues, [])
  const [values, setValues] = useState<FlightSearchParams>({ ...defaults, ...initialValues })
  const [validationError, setValidationError] = useState('')

  const update = <K extends keyof FlightSearchParams>(key: K, value: FlightSearchParams[K]) => {
    setValues((current) => ({ ...current, [key]: value }))
  }

  const swapAirports = () => {
    setValues((current) => ({ ...current, origin: current.destination, destination: current.origin }))
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const origin = values.origin.trim().toUpperCase()
    const destination = values.destination.trim().toUpperCase()
    if (!/^[A-Z]{3}$/.test(origin) || !/^[A-Z]{3}$/.test(destination) || origin === destination) {
      setValidationError('请输入不同的出发机场和到达机场三字码。')
      return
    }
    if (!values.departureDate || (values.tripType === 'roundtrip' && !values.returnDate)) {
      setValidationError('请补充完整的出发和返程日期。')
      return
    }
    if (values.tripType === 'roundtrip' && values.returnDate && values.returnDate < values.departureDate) {
      setValidationError('返程日期不能早于出发日期。')
      return
    }
    if (values.airlineCodes && values.airlineCodes.split(',').some((code) => !/^[A-Z0-9]{2,3}$/i.test(code.trim()))) {
      setValidationError('航空公司代码请使用两到三位字母或数字，并用逗号分隔。')
      return
    }
    if (!Number.isInteger(values.passengers) || values.passengers < 1 || values.passengers > 9) {
      setValidationError('成人乘客数量需在 1 到 9 人之间。')
      return
    }
    if (values.maxPriceMinor !== undefined && (!Number.isFinite(values.maxPriceMinor) || values.maxPriceMinor < 0)) {
      setValidationError('最高含税价必须是有效的非负金额。')
      return
    }
    const airportFilters = [values.departureAirports, values.arrivalAirports].filter(Boolean) as string[]
    if (airportFilters.some((value) => value.split(',').some((code) => !/^[A-Z]{3}$/i.test(code.trim())))) {
      setValidationError('机场筛选请使用三位 IATA 代码，并用逗号分隔。')
      return
    }
    if ((values.departureMinuteStart === undefined) !== (values.departureMinuteEnd === undefined)
      || (values.departureMinuteStart !== undefined && values.departureMinuteEnd !== undefined && values.departureMinuteStart >= values.departureMinuteEnd)) {
      setValidationError('出发时间筛选需要同时填写起止时间，且起点必须早于终点。')
      return
    }
    setValidationError('')
    onSubmit({
      ...values,
      origin,
      destination,
      airlineCodes: normalizeCodeList(values.airlineCodes),
      departureAirports: normalizeCodeList(values.departureAirports),
      arrivalAirports: normalizeCodeList(values.arrivalAirports),
      returnDate: values.tripType === 'roundtrip' ? values.returnDate : undefined,
    })
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-6">
      <Tabs value={values.tripType} onValueChange={(value) => update('tripType', value as TripType)}>
        <TabsList aria-label="行程类型">
          <TabsTrigger value="oneway">单程</TabsTrigger>
          <TabsTrigger value="roundtrip">往返</TabsTrigger>
        </TabsList>
      </Tabs>

      <FieldSet>
        <FieldLegend>行程</FieldLegend>
        <FieldGroup className="grid gap-4 md:grid-cols-[1fr_auto_1fr] md:items-end">
          <Field>
            <FieldLabel htmlFor="origin">出发地</FieldLabel>
            <Input id="origin" value={values.origin} onChange={(event) => update('origin', event.target.value.toUpperCase())} maxLength={3} placeholder="SHA" required />
            <FieldDescription>机场或城市三字码，例如 SHA / PVG</FieldDescription>
          </Field>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button type="button" variant="outline" size="icon" className="mb-0.5" onClick={swapAirports} aria-label="交换出发地和目的地">
                <ArrowRightLeft aria-hidden="true" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>交换出发地和目的地</TooltipContent>
          </Tooltip>
          <Field>
            <FieldLabel htmlFor="destination">目的地</FieldLabel>
            <Input id="destination" value={values.destination} onChange={(event) => update('destination', event.target.value.toUpperCase())} maxLength={3} placeholder="TYO" required />
            <FieldDescription>机场或城市三字码，例如 TYO / KIX</FieldDescription>
          </Field>
        </FieldGroup>

        <FieldGroup className="grid gap-4 md:grid-cols-3">
          <Field>
            <FieldLabel htmlFor="departure-date">出发日期</FieldLabel>
            <Input id="departure-date" type="date" min={dateFromToday(0)} value={values.departureDate} onChange={(event) => update('departureDate', event.target.value)} required />
          </Field>
          {values.tripType === 'roundtrip' ? (
            <Field>
              <FieldLabel htmlFor="return-date">返程日期</FieldLabel>
              <Input id="return-date" type="date" min={values.departureDate} value={values.returnDate || ''} onChange={(event) => update('returnDate', event.target.value)} required />
            </Field>
          ) : null}
          <Field>
            <FieldLabel htmlFor="passengers">成人</FieldLabel>
            <Select value={String(values.passengers)} onValueChange={(value) => update('passengers', Number(value))}>
              <SelectTrigger id="passengers"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {['1', '2', '3', '4', '5', '6', '7', '8', '9'].map((value) => <SelectItem key={value} value={value}>{value} 位成人</SelectItem>)}
                </SelectGroup>
              </SelectContent>
            </Select>
          </Field>
        </FieldGroup>
      </FieldSet>

      <Separator />

      <FieldSet>
        <FieldLegend>筛选条件</FieldLegend>
        <Field orientation="horizontal" className="rounded-md border bg-muted/30 px-3 py-2">
          <FieldContent>
            <FieldLabel htmlFor="direct-only">仅直飞</FieldLabel>
            <FieldDescription>只保留没有中转段的行程</FieldDescription>
          </FieldContent>
          <Switch id="direct-only" checked={values.directOnly} onCheckedChange={(checked) => update('directOnly', checked)} />
        </Field>

        <FieldGroup className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <Field>
            <FieldLabel htmlFor="airline-codes">航空公司</FieldLabel>
            <Input id="airline-codes" value={values.airlineCodes || ''} onChange={(event) => update('airlineCodes', event.target.value.toUpperCase())} placeholder="MU,NH" />
            <FieldDescription>可填多个 IATA 代码</FieldDescription>
          </Field>
          <Field>
            <FieldLabel htmlFor="max-price">结果最高价（元）</FieldLabel>
            <Input id="max-price" type="number" min="0" step="1" value={values.maxPriceMinor === undefined ? '' : values.maxPriceMinor / 100} onChange={(event) => update('maxPriceMinor', event.target.value ? Math.round(Number(event.target.value) * 100) : undefined)} placeholder="不限" />
            <FieldDescription>过滤高于此金额的报价，不会触发通知</FieldDescription>
          </Field>
          <Field>
            <FieldLabel htmlFor="max-stops">最多中转</FieldLabel>
            <Select value={values.maxStops === undefined ? 'any' : String(values.maxStops)} onValueChange={(value) => update('maxStops', value === 'any' ? undefined : Number(value))}>
              <SelectTrigger id="max-stops"><SelectValue /></SelectTrigger>
              <SelectContent><SelectGroup><SelectItem value="any">不限</SelectItem><SelectItem value="0">直飞</SelectItem><SelectItem value="1">最多 1 次</SelectItem><SelectItem value="2">最多 2 次</SelectItem><SelectItem value="3">最多 3 次</SelectItem></SelectGroup></SelectContent>
            </Select>
          </Field>
          <Field>
            <FieldLabel htmlFor="max-duration">最长行程</FieldLabel>
            <Select value={values.maxDurationMinutes === undefined ? 'any' : String(values.maxDurationMinutes)} onValueChange={(value) => update('maxDurationMinutes', value === 'any' ? undefined : Number(value))}>
              <SelectTrigger id="max-duration"><SelectValue /></SelectTrigger>
              <SelectContent><SelectGroup><SelectItem value="any">不限</SelectItem><SelectItem value="180">3 小时</SelectItem><SelectItem value="300">5 小时</SelectItem><SelectItem value="480">8 小时</SelectItem><SelectItem value="720">12 小时</SelectItem></SelectGroup></SelectContent>
            </Select>
          </Field>
        </FieldGroup>

        <FieldGroup className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <Field>
            <FieldLabel htmlFor="departure-airports">出发机场</FieldLabel>
            <Input id="departure-airports" value={values.departureAirports || ''} onChange={(event) => update('departureAirports', event.target.value.toUpperCase())} placeholder="SHA,PVG" />
          </Field>
          <Field>
            <FieldLabel htmlFor="arrival-airports">到达机场</FieldLabel>
            <Input id="arrival-airports" value={values.arrivalAirports || ''} onChange={(event) => update('arrivalAirports', event.target.value.toUpperCase())} placeholder="NRT,HND" />
          </Field>
          <Field>
            <FieldLabel htmlFor="departure-start">出发时间起</FieldLabel>
            <Input id="departure-start" type="time" value={minutesToTime(values.departureMinuteStart)} onChange={(event) => update('departureMinuteStart', timeToMinutes(event.target.value))} />
          </Field>
          <Field>
            <FieldLabel htmlFor="departure-end">出发时间止</FieldLabel>
            <Input id="departure-end" type="time" value={minutesToTime(values.departureMinuteEnd)} onChange={(event) => update('departureMinuteEnd', timeToMinutes(event.target.value))} />
          </Field>
        </FieldGroup>
      </FieldSet>

      {validationError ? (
        <Alert variant="destructive">
          <AlertCircle data-icon="inline-start" />
          <AlertTitle>请检查查询条件</AlertTitle>
          <AlertDescription>{validationError}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex flex-wrap items-center justify-end gap-3">
        <Button type="submit" disabled={submitting}>
          <Search data-icon="inline-start" aria-hidden="true" />
          {submitting ? '查询中…' : '查询价格'}
        </Button>
      </div>
    </form>
  )
}
