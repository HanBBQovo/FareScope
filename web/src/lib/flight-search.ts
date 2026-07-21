import type { FlightSearchParams } from '@/api/fares'
import { dateFromToday } from '@/lib/dates'

export function defaultFlightSearchValues(): FlightSearchParams {
  const departureDate = dateFromToday(30)
  return {
    tripType: 'oneway',
    origin: 'SHA',
    destination: 'TYO',
    departureDate,
    returnDate: dateFromToday(35),
    directOnly: false,
    passengers: 1,
  }
}
