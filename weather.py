"""
Live weather fetcher using:
  1. Nominatim (OpenStreetMap) — geocode city name to lat/lon
  2. yr.no (MET Norway)        — fetch current temperature

No API key required. Free for personal use.
"""

from collections import defaultdict
from datetime import date, timedelta

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
YR_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HEADERS = {"User-Agent": "HVAC-DigitalTwin/1.0 (personal use)"}


def fetch_current_temp(city: str) -> dict:
    """
    Returns:
        {
            "temp_c": float,
            "city": str,
            "lat": float,
            "lon": float,
            "fetched_at": str,   # UTC ISO timestamp from yr.no
            "error": str | None,
        }
    """
    # Step 1 — geocode
    try:
        geo = requests.get(
            NOMINATIM_URL,
            params={"q": city, "format": "json", "limit": 1},
            headers=HEADERS,
            timeout=8,
        )
        geo.raise_for_status()
        results = geo.json()
    except Exception as e:
        return {"error": f"Geocoding failed: {e}", "temp_c": None}

    if not results:
        return {"error": f"City '{city}' not found.", "temp_c": None}

    lat = float(results[0]["lat"])
    lon = float(results[0]["lon"])
    full_display = results[0].get("display_name", city)
    display = full_display.split(",")[0]

    # Step 2 — fetch weather from yr.no
    try:
        yr = requests.get(
            YR_URL,
            params={"lat": round(lat, 4), "lon": round(lon, 4)},
            headers=HEADERS,
            timeout=8,
        )
        yr.raise_for_status()
        data = yr.json()
    except Exception as e:
        return {"error": f"Weather fetch failed: {e}", "temp_c": None}

    try:
        entry = data["properties"]["timeseries"][0]
        temp_c = entry["data"]["instant"]["details"]["air_temperature"]
        fetched_at = entry["time"]
    except (KeyError, IndexError) as e:
        return {"error": f"Unexpected response format: {e}", "temp_c": None}

    return {
        "temp_c": temp_c,
        "city": display,
        "display_name": full_display,
        "lat": lat,
        "lon": lon,
        "fetched_at": fetched_at,
        "error": None,
    }


def fetch_historical_daily_means(lat: float, lon: float) -> dict:
    """
    Fetch 10-year historical daily mean temperatures from the Open-Meteo archive API
    and return a dict mapping (month, day) -> mean_temp_c averaged across all 10 years.

    Args:
        lat: Latitude of the location.
        lon: Longitude of the location.

    Returns:
        dict[tuple[int, int], float] mapping (month, day) -> mean temperature in °C.

    Raises:
        requests.HTTPError: if the API returns a non-2xx response.
    """
    today = date.today()
    start_date = date(today.year - 10, today.month, today.day)
    end_date = today - timedelta(days=1)

    resp = requests.get(
        OPEN_METEO_ARCHIVE_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "daily": "temperature_2m_mean",
            "timezone": "auto",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    temps = daily.get("temperature_2m_mean", [])

    # Accumulate sum and count per (month, day)
    accum: dict[tuple[int, int], list[float]] = defaultdict(list)
    for date_str, temp in zip(dates, temps):
        if temp is None:
            continue
        d = date.fromisoformat(date_str)
        accum[(d.month, d.day)].append(temp)

    return {key: sum(vals) / len(vals) for key, vals in accum.items()}
