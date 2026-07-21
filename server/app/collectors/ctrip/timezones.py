"""Conservative IATA-to-IANA timezone hints for provider-local flight times.

Ctrip's batch response currently sends naive local timestamps and omits timezone fields.
Only airports in this explicit map are inferred; unknown airports remain unnormalizable
instead of being assigned a guessed offset.
"""

from __future__ import annotations

_AIRPORT_TIMEZONES: dict[str, str] = {
    # Mainland China and nearby Greater China hubs.
    "CAN": "Asia/Shanghai",
    "CGO": "Asia/Shanghai",
    "CTU": "Asia/Shanghai",
    "DLC": "Asia/Shanghai",
    "FOC": "Asia/Shanghai",
    "HAK": "Asia/Shanghai",
    "HGH": "Asia/Shanghai",
    "HRB": "Asia/Shanghai",
    "KMG": "Asia/Shanghai",
    "NKG": "Asia/Shanghai",
    "PEK": "Asia/Shanghai",
    "PKX": "Asia/Shanghai",
    "PVG": "Asia/Shanghai",
    "SHA": "Asia/Shanghai",
    "SHE": "Asia/Shanghai",
    "SZX": "Asia/Shanghai",
    "TAO": "Asia/Shanghai",
    "TSN": "Asia/Shanghai",
    "WUH": "Asia/Shanghai",
    "XIY": "Asia/Shanghai",
    "XMN": "Asia/Shanghai",
    "HKG": "Asia/Hong_Kong",
    "TPE": "Asia/Taipei",
    # Japan and Korea.
    "FUK": "Asia/Tokyo",
    "HND": "Asia/Tokyo",
    "ITM": "Asia/Tokyo",
    "KIX": "Asia/Tokyo",
    "NGO": "Asia/Tokyo",
    "NRT": "Asia/Tokyo",
    "OKA": "Asia/Tokyo",
    "CTS": "Asia/Tokyo",
    "SDJ": "Asia/Tokyo",
    "GMP": "Asia/Seoul",
    "ICN": "Asia/Seoul",
    "PUS": "Asia/Seoul",
    # Common Asia-Pacific hubs.
    "BKK": "Asia/Bangkok",
    "CGK": "Asia/Jakarta",
    "HAN": "Asia/Ho_Chi_Minh",
    "HKT": "Asia/Bangkok",
    "KUL": "Asia/Kuala_Lumpur",
    "MNL": "Asia/Manila",
    "SIN": "Asia/Singapore",
    "SGN": "Asia/Ho_Chi_Minh",
    # Common long-haul hubs.
    "AMS": "Europe/Amsterdam",
    "CDG": "Europe/Paris",
    "FRA": "Europe/Berlin",
    "LHR": "Europe/London",
    "IST": "Europe/Istanbul",
    "JFK": "America/New_York",
    "LAX": "America/Los_Angeles",
    "ORD": "America/Chicago",
    "SFO": "America/Los_Angeles",
    "SEA": "America/Los_Angeles",
    "SYD": "Australia/Sydney",
    "MEL": "Australia/Melbourne",
    "YVR": "America/Vancouver",
}


def airport_timezone(code: str | None) -> str | None:
    if not code:
        return None
    return _AIRPORT_TIMEZONES.get(code.strip().upper())
