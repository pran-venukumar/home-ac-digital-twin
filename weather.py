"""
Live weather fetcher using:
  1. Nominatim (OpenStreetMap) — geocode city name to lat/lon
  2. yr.no (MET Norway)        — fetch current temperature

No API key required. Free for personal use.
"""

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
YR_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
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
