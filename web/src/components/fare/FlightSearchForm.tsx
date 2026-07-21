import { useMemo, useState, type FormEvent } from 'react'
import { AlertCircle, ArrowRightLeft, Search } from 'lucide-react'

import type { FlightSearchParams, TripType } from '@/api/fares'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Combobox } from '@/components/ui/combobox'
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
import { MultiSelect } from '@/components/ui/multi-select'
import type { SelectOption } from '@/components/ui/option-types'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import {
  AIRPORTS,
  FLIGHT_LOCATIONS,
  locationSearchTerms,
} from '@/lib/airport-directory'
import { dateFromToday, minutesToTime, timeToMinutes } from '@/lib/dates'
import { defaultFlightSearchValues } from '@/lib/flight-search'

interface FlightSearchFormProps {
  initialValues?: Partial<FlightSearchParams>
  submitting?: boolean
  onSubmit: (values: FlightSearchParams) => void
}

const LOCATION_OPTIONS: SelectOption[] = FLIGHT_LOCATIONS.map((location) => ({
  value: location.code,
  label: location.kind === 'city' ? location.name : `${location.city} · ${location.name}`,
  description: `${location.country} · ${location.code}`,
  keywords: locationSearchTerms(location),
}))

const AIRPORT_OPTIONS: SelectOption[] = AIRPORTS.map((location) => ({
  value: location.code,
  label: `${location.city} · ${location.name}`,
  description: `${location.code} · ${location.country}`,
  keywords: locationSearchTerms(location),
}))

const AIRLINE_OPTIONS: SelectOption[] = [
  ['MU', '中国东方航空', 'China Eastern Airlines', ['东航', 'china eastern']],
  ['FM', '上海航空', 'Shanghai Airlines', ['上航', 'shanghai airlines']],
  ['CA', '中国国际航空', 'Air China', ['国航', 'air china']],
  ['CZ', '中国南方航空', 'China Southern Airlines', ['南航', 'china southern']],
  ['HO', '吉祥航空', 'Juneyao Air', ['吉祥', 'juneyao']],
  ['9C', '春秋航空', 'Spring Airlines', ['春秋', 'spring airlines']],
  ['NH', '全日空', 'All Nippon Airways', ['ANA', '全日本空输']],
  ['JL', '日本航空', 'Japan Airlines', ['JAL', '日航']],
  ['MM', '乐桃航空', 'Peach Aviation', ['peach', '乐桃']],
  ['GK', '捷星日本', 'Jetstar Japan', ['jetstar', '捷星']],
  ['IJ', '春秋航空日本', 'Spring Japan', ['spring japan']],
  ['7G', '星悦航空', 'StarFlyer', ['starflyer', '星悦']],
  ['BC', '天马航空', 'Skymark Airlines', ['skymark', '天马']],
  ['KE', '大韩航空', 'Korean Air', ['korean air', '大韩']],
  ['OZ', '韩亚航空', 'Asiana Airlines', ['asiana', '韩亚']],
].map(([value, label, description, keywords]) => ({
  value: value as string,
  label: label as string,
  description: `${description as string} · ${value as string}`,
  keywords: keywords as string[],
}))

function splitCodes(value: string | undefined): string[] {
  return value
    ?.split(',')
    .map((code) => code.trim().toUpperCase())
    .filter(Boolean) || []
}

function normalizeCodeList(value: string | undefined): string | undefined {
  const codes = splitCodes(value)
  return codes.length ? codes.join(',') : undefined
}

function includeUnknownCodes(options: SelectOption[], values: string[]): SelectOption[] {
  const known = new Set(options.map((option) => option.value))
  const unknown = values
    .filter((value) => !known.has(value))
    .map((value) => ({ value, label: value, description: '已保存的代码' }))
  return unknown.length ? [...unknown, ...options] : options
}

function includeUnknownLocation(options: SelectOption[], value: string): SelectOption[] {
  return options.some((option) => option.value === value)
    ? options
    : [{ value, label: value, description: '已保存的城市或机场代码' }, ...options]
}

export function FlightSearchForm({ initialValues, submitting = false, onSubmit }: FlightSearchFormProps) {
  const defaults = useMemo(defaultFlightSearchValues, [])
  const [values, setValues] = useState<FlightSearchParams>({ ...defaults, ...initialValues })
  const [validationError, setValidationError] = useState('')
  const selectedAirlines = splitCodes(values.airlineCodes)
  const selectedDepartureAirports = splitCodes(values.departureAirports)
  const selectedArrivalAirports = splitCodes(values.arrivalAirports)
  const originOptions = useMemo(
    () => includeUnknownLocation(LOCATION_OPTIONS, values.origin),
    [values.origin],
  )
  const destinationOptions = useMemo(
    () => includeUnknownLocation(LOCATION_OPTIONS, values.destination),
    [values.destination],
  )

  const update = <K extends keyof FlightSearchParams>(key: K, value: FlightSearchParams[K]) => {
    setValues((current) => ({ ...current, [key]: value }))
  }

  const swapAirports = () => {
    setValues((current) => ({
      ...current,
      origin: current.destination,
      destination: current.origin,
      departureAirports: current.arrivalAirports,
      arrivalAirports: current.departureAirports,
    }))
  }

  const setDirectOnly = (checked: boolean) => {
    setValues((current) => ({
      ...current,
      directOnly: checked,
      maxStops: checked ? 0 : current.maxStops === 0 ? undefined : current.maxStops,
    }))
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const origin = values.origin.trim().toUpperCase()
    const destination = values.destination.trim().toUpperCase()
    if (!/^[A-Z]{3}$/.test(origin) || !/^[A-Z]{3}$/.test(destination) || origin === destination) {
      setValidationError('请选择不同的出发地和目的地。')
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
      setValidationError('航空公司筛选中包含无效代码。')
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
      setValidationError('机场筛选中包含无效代码。')
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
        <FieldDescription>可以输入中文、拼音、英文或三字码搜索城市和机场。</FieldDescription>
        <FieldGroup className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] md:items-start">
          <Field>
            <FieldLabel htmlFor="origin">出发地</FieldLabel>
            <Combobox
              id="origin"
              options={originOptions}
              value={values.origin}
              onValueChange={(value) => update('origin', value)}
              ariaLabel="选择出发地"
              placeholder="搜索城市或机场"
              searchPlaceholder="例如：上海、浦东、Shanghai、PVG"
              emptyText="没有找到匹配的出发地"
              className="h-11"
              contentClassName="min-w-[min(28rem,calc(100vw-2rem))]"
            />
            <FieldDescription>当前选择会自动转换为采集所需代码</FieldDescription>
          </Field>
          <div className="flex justify-center md:pt-7">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button type="button" variant="outline" size="icon" onClick={swapAirports} aria-label="交换出发地和目的地">
                  <ArrowRightLeft aria-hidden="true" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>交换出发地和目的地</TooltipContent>
            </Tooltip>
          </div>
          <Field>
            <FieldLabel htmlFor="destination">目的地</FieldLabel>
            <Combobox
              id="destination"
              options={destinationOptions}
              value={values.destination}
              onValueChange={(value) => update('destination', value)}
              ariaLabel="选择目的地"
              placeholder="搜索城市或机场"
              searchPlaceholder="例如：东京、成田、Tokyo、NRT"
              emptyText="没有找到匹配的目的地"
              className="h-11"
              contentClassName="min-w-[min(28rem,calc(100vw-2rem))]"
            />
            <FieldDescription>东京、大阪等城市会同时搜索其主要机场</FieldDescription>
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
        <FieldDescription>这些条件只用于缩小结果范围，不需要填写任何代码。</FieldDescription>
        <Field orientation="horizontal" className="rounded-md border bg-muted/30 px-3 py-3">
          <FieldContent>
            <FieldLabel htmlFor="direct-only">仅看直飞</FieldLabel>
            <FieldDescription>开启后只显示没有换乘航段的行程</FieldDescription>
          </FieldContent>
          <Switch id="direct-only" checked={values.directOnly} onCheckedChange={setDirectOnly} />
        </Field>

        <FieldGroup className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <Field>
            <FieldLabel htmlFor="airline-codes">航空公司</FieldLabel>
            <MultiSelect
              id="airline-codes"
              ariaLabel="筛选航空公司"
              options={includeUnknownCodes(AIRLINE_OPTIONS, selectedAirlines)}
              value={selectedAirlines}
              onValueChange={(next) => update('airlineCodes', next.length ? next.join(',') : undefined)}
              placeholder="不限航空公司"
              searchPlaceholder="搜索航司中文名或英文名"
              maxPreviewItems={1}
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="max-price">结果最高价（元）</FieldLabel>
            <Input id="max-price" type="number" min="0" step="1" value={values.maxPriceMinor === undefined ? '' : values.maxPriceMinor / 100} onChange={(event) => update('maxPriceMinor', event.target.value ? Math.round(Number(event.target.value) * 100) : undefined)} placeholder="不限" />
            <FieldDescription>只影响结果展示，不会触发通知</FieldDescription>
          </Field>
          <Field data-disabled={values.directOnly || undefined}>
            <FieldLabel htmlFor="max-stops">最多中转</FieldLabel>
            <Select disabled={values.directOnly} value={values.maxStops === undefined ? 'any' : String(values.maxStops)} onValueChange={(value) => update('maxStops', value === 'any' ? undefined : Number(value))}>
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

        <FieldGroup className="grid gap-4 lg:grid-cols-2">
          <Field>
            <FieldLabel htmlFor="departure-airports">指定出发机场</FieldLabel>
            <MultiSelect
              id="departure-airports"
              ariaLabel="筛选出发机场"
              options={includeUnknownCodes(AIRPORT_OPTIONS, selectedDepartureAirports)}
              value={selectedDepartureAirports}
              onValueChange={(next) => update('departureAirports', next.length ? next.join(',') : undefined)}
              placeholder="不限具体机场"
              searchPlaceholder="例如：浦东、虹桥、PVG"
              maxPreviewItems={2}
              contentClassName="min-w-[min(30rem,calc(100vw-2rem))]"
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="arrival-airports">指定到达机场</FieldLabel>
            <MultiSelect
              id="arrival-airports"
              ariaLabel="筛选到达机场"
              options={includeUnknownCodes(AIRPORT_OPTIONS, selectedArrivalAirports)}
              value={selectedArrivalAirports}
              onValueChange={(next) => update('arrivalAirports', next.length ? next.join(',') : undefined)}
              placeholder="不限具体机场"
              searchPlaceholder="例如：成田、羽田、NRT"
              maxPreviewItems={2}
              contentClassName="min-w-[min(30rem,calc(100vw-2rem))]"
            />
          </Field>
        </FieldGroup>

        <FieldGroup className="grid gap-4 sm:grid-cols-2">
          <Field>
            <FieldLabel htmlFor="departure-start">最早出发时间</FieldLabel>
            <Input id="departure-start" type="time" value={minutesToTime(values.departureMinuteStart)} onChange={(event) => update('departureMinuteStart', timeToMinutes(event.target.value))} />
          </Field>
          <Field>
            <FieldLabel htmlFor="departure-end">最晚出发时间</FieldLabel>
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

      <div className="flex justify-end">
        <Button type="submit" className="w-full sm:w-auto" disabled={submitting}>
          <Search data-icon="inline-start" aria-hidden="true" />
          {submitting ? '查询中…' : '查询价格'}
        </Button>
      </div>
    </form>
  )
}
