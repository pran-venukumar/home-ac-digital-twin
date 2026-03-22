"""
model_search.py — AC model lookup for Indian market

Provides:
  - LOCAL_DB: curated list of Indian-market AC models (30+ entries)
  - search_ac_models_online(query) -> list[dict]
  - search_models(query, online=False) -> list[dict]

Each dict has: brand, model, label, capacity_kw, cop_rated, tonnage
"""

import re
import urllib.parse

# ---------------------------------------------------------------------------
# Local curated database
# ---------------------------------------------------------------------------

LOCAL_DB: list[dict] = [
    # Samsung
    {"brand": "Samsung", "model": "WindFree 1.0T",       "label": "Samsung WindFree 1.0T  (3.52 kW)",        "capacity_kw": 3.517, "cop_rated": 3.8, "tonnage": 1.0},
    {"brand": "Samsung", "model": "WindFree 1.5T",       "label": "Samsung WindFree 1.5T  (5.28 kW)",        "capacity_kw": 5.275, "cop_rated": 4.0, "tonnage": 1.5},
    {"brand": "Samsung", "model": "WindFree 2.0T",       "label": "Samsung WindFree 2.0T  (7.03 kW)",        "capacity_kw": 7.034, "cop_rated": 3.9, "tonnage": 2.0},
    {"brand": "Samsung", "model": "WindFree 2.5T",       "label": "Samsung WindFree 2.5T  (8.79 kW)",        "capacity_kw": 8.793, "cop_rated": 3.8, "tonnage": 2.5},
    {"brand": "Samsung", "model": "WindFree 3.0T",       "label": "Samsung WindFree 3.0T (10.55 kW)",        "capacity_kw": 10.55, "cop_rated": 3.7, "tonnage": 3.0},
    {"brand": "Samsung", "model": "WindFree Elite 1.5T", "label": "Samsung WindFree Elite 1.5T  (5.28 kW)",  "capacity_kw": 5.275, "cop_rated": 4.9, "tonnage": 1.5},
    {"brand": "Samsung", "model": "WindFree Elite 2.0T", "label": "Samsung WindFree Elite 2.0T  (7.03 kW)",  "capacity_kw": 7.034, "cop_rated": 4.7, "tonnage": 2.0},
    # LG
    {"brand": "LG", "model": "Dual Inverter 1.0T", "label": "LG Dual Inverter 1.0T  (3.52 kW)",  "capacity_kw": 3.517, "cop_rated": 3.9, "tonnage": 1.0},
    {"brand": "LG", "model": "Dual Inverter 1.5T", "label": "LG Dual Inverter 1.5T  (5.28 kW)",  "capacity_kw": 5.275, "cop_rated": 4.2, "tonnage": 1.5},
    {"brand": "LG", "model": "Dual Inverter 2.0T", "label": "LG Dual Inverter 2.0T  (7.03 kW)",  "capacity_kw": 7.034, "cop_rated": 4.0, "tonnage": 2.0},
    {"brand": "LG", "model": "Artcool 1.5T",       "label": "LG Artcool 1.5T  (5.28 kW)",        "capacity_kw": 5.275, "cop_rated": 4.5, "tonnage": 1.5},
    {"brand": "LG", "model": "Artcool 2.0T",       "label": "LG Artcool 2.0T  (7.03 kW)",        "capacity_kw": 7.034, "cop_rated": 4.3, "tonnage": 2.0},
    # Daikin
    {"brand": "Daikin", "model": "FTKF 1.0T",  "label": "Daikin FTKF 1.0T  (3.52 kW)",  "capacity_kw": 3.517, "cop_rated": 4.0, "tonnage": 1.0},
    {"brand": "Daikin", "model": "FTKF 1.5T",  "label": "Daikin FTKF 1.5T  (5.28 kW)",  "capacity_kw": 5.275, "cop_rated": 4.3, "tonnage": 1.5},
    {"brand": "Daikin", "model": "FTKF 2.0T",  "label": "Daikin FTKF 2.0T  (7.03 kW)",  "capacity_kw": 7.034, "cop_rated": 4.1, "tonnage": 2.0},
    {"brand": "Daikin", "model": "MTKM 1.5T",  "label": "Daikin MTKM 1.5T  (5.28 kW)",  "capacity_kw": 5.275, "cop_rated": 5.2, "tonnage": 1.5},
    {"brand": "Daikin", "model": "MTKM 2.0T",  "label": "Daikin MTKM 2.0T  (7.03 kW)",  "capacity_kw": 7.034, "cop_rated": 5.0, "tonnage": 2.0},
    # Voltas
    {"brand": "Voltas", "model": "Adjustable Inverter 1.5T", "label": "Voltas Adjustable Inverter 1.5T  (5.28 kW)", "capacity_kw": 5.275, "cop_rated": 3.9, "tonnage": 1.5},
    {"brand": "Voltas", "model": "Adjustable Inverter 2.0T", "label": "Voltas Adjustable Inverter 2.0T  (7.03 kW)", "capacity_kw": 7.034, "cop_rated": 3.8, "tonnage": 2.0},
    {"brand": "Voltas", "model": "Gold Inverter 1.5T",       "label": "Voltas Gold Inverter 1.5T  (5.28 kW)",       "capacity_kw": 5.275, "cop_rated": 4.1, "tonnage": 1.5},
    # Hitachi
    {"brand": "Hitachi", "model": "Kashikoi 1.5T", "label": "Hitachi Kashikoi 1.5T  (5.28 kW)", "capacity_kw": 5.275, "cop_rated": 4.0, "tonnage": 1.5},
    {"brand": "Hitachi", "model": "Kashikoi 2.0T", "label": "Hitachi Kashikoi 2.0T  (7.03 kW)", "capacity_kw": 7.034, "cop_rated": 3.9, "tonnage": 2.0},
    {"brand": "Hitachi", "model": "RAS 1.5T",      "label": "Hitachi RAS 1.5T  (5.28 kW)",      "capacity_kw": 5.275, "cop_rated": 4.2, "tonnage": 1.5},
    # Panasonic
    {"brand": "Panasonic", "model": "CS-CU Inverter 1.0T", "label": "Panasonic CS-CU 1.0T  (3.52 kW)", "capacity_kw": 3.517, "cop_rated": 4.1, "tonnage": 1.0},
    {"brand": "Panasonic", "model": "CS-CU Inverter 1.5T", "label": "Panasonic CS-CU 1.5T  (5.28 kW)", "capacity_kw": 5.275, "cop_rated": 4.4, "tonnage": 1.5},
    {"brand": "Panasonic", "model": "CS-CU Inverter 2.0T", "label": "Panasonic CS-CU 2.0T  (7.03 kW)", "capacity_kw": 7.034, "cop_rated": 4.2, "tonnage": 2.0},
    # Blue Star
    {"brand": "Blue Star", "model": "IC Inverter 1.5T", "label": "Blue Star IC 1.5T  (5.28 kW)", "capacity_kw": 5.275, "cop_rated": 4.0, "tonnage": 1.5},
    {"brand": "Blue Star", "model": "IC Inverter 2.0T", "label": "Blue Star IC 2.0T  (7.03 kW)", "capacity_kw": 7.034, "cop_rated": 3.9, "tonnage": 2.0},
    {"brand": "Blue Star", "model": "BI Inverter 1.5T", "label": "Blue Star BI 1.5T  (5.28 kW)", "capacity_kw": 5.275, "cop_rated": 4.3, "tonnage": 1.5},
    # Carrier
    {"brand": "Carrier", "model": "Emperia 1.5T", "label": "Carrier Emperia 1.5T  (5.28 kW)", "capacity_kw": 5.275, "cop_rated": 4.0, "tonnage": 1.5},
    {"brand": "Carrier", "model": "Emperia 2.0T", "label": "Carrier Emperia 2.0T  (7.03 kW)", "capacity_kw": 7.034, "cop_rated": 3.9, "tonnage": 2.0},
    {"brand": "Carrier", "model": "Cicero 1.5T",  "label": "Carrier Cicero 1.5T  (5.28 kW)",  "capacity_kw": 5.275, "cop_rated": 4.2, "tonnage": 1.5},
    # Whirlpool
    {"brand": "Whirlpool", "model": "3D Cool Inverter 1.5T", "label": "Whirlpool 3D Cool Inverter 1.5T  (5.28 kW)", "capacity_kw": 5.275, "cop_rated": 4.0, "tonnage": 1.5},
    {"brand": "Whirlpool", "model": "3D Cool Inverter 2.0T", "label": "Whirlpool 3D Cool Inverter 2.0T  (7.03 kW)", "capacity_kw": 7.034, "cop_rated": 3.9, "tonnage": 2.0},
    # Custom sentinel
    {"brand": "Custom", "model": "Custom", "label": "Custom", "capacity_kw": 5.275, "cop_rated": 4.0, "tonnage": 1.5},
]

# ---------------------------------------------------------------------------
# Internet search layer
# ---------------------------------------------------------------------------

_KW_PATTERN   = re.compile(r'(\d+\.?\d*)\s*kW', re.IGNORECASE)
_TON_PATTERN  = re.compile(r'(\d+\.?\d*)\s*[Tt]on(?:ne)?', re.IGNORECASE)
_COP_PATTERN  = re.compile(r'COP[:\s]+(\d+\.?\d*)', re.IGNORECASE)
_STAR_PATTERN = re.compile(r'(\d)\s*[Ss]tar', re.IGNORECASE)

# Approximate COP from BEE star rating (5-star inverter India)
_STAR_TO_COP = {5: 4.5, 4: 4.0, 3: 3.5, 2: 3.0, 1: 2.5}

_DDGAPI = (
    "https://api.duckduckgo.com/?q={q}"
    "+AC+inverter+specifications+India+BEE+rating"
    "&format=json&no_html=1&skip_disambig=1"
)


def search_ac_models_online(query: str) -> list[dict]:
    """
    Query DuckDuckGo Instant Answers API and attempt to extract AC model specs
    from RelatedTopics / Results text.  Returns a list of dicts with the same
    schema as LOCAL_DB, or [] on any error.
    """
    try:
        import requests  # deferred import so the module loads without requests installed
    except ImportError:
        return []

    try:
        url = _DDGAPI.format(q=urllib.parse.quote_plus(query))
        resp = requests.get(url, timeout=3)
        data = resp.json()
    except Exception:
        return []

    results: list[dict] = []
    seen_labels: set[str] = set()

    # Collect all text snippets from RelatedTopics + Results
    snippets: list[str] = []
    for item in data.get("RelatedTopics", []):
        if isinstance(item, dict):
            text = item.get("Text") or item.get("Result") or ""
            if text:
                snippets.append(text)
        # DuckDuckGo sometimes nests topics
        for sub in item.get("Topics", []) if isinstance(item, dict) else []:
            text = sub.get("Text") or sub.get("Result") or ""
            if text:
                snippets.append(text)
    for item in data.get("Results", []):
        if isinstance(item, dict):
            text = item.get("Text") or item.get("Result") or ""
            if text:
                snippets.append(text)

    for text in snippets:
        # Skip short snippets that are unlikely to have useful info
        if len(text) < 15:
            continue

        capacity_kw: float | None = None
        tonnage: float | None = None
        cop: float | None = None

        # Extract capacity in kW
        m = _KW_PATTERN.search(text)
        if m:
            try:
                capacity_kw = float(m.group(1))
                tonnage = round(capacity_kw / 3.517, 1)
            except ValueError:
                pass

        # Fallback: extract tonnage directly
        if tonnage is None:
            m = _TON_PATTERN.search(text)
            if m:
                try:
                    tonnage = float(m.group(1))
                    capacity_kw = round(tonnage * 3.517, 3)
                except ValueError:
                    pass

        # Extract COP
        m = _COP_PATTERN.search(text)
        if m:
            try:
                cop = float(m.group(1))
            except ValueError:
                pass

        # Fallback COP from star rating
        if cop is None:
            m = _STAR_PATTERN.search(text)
            if m:
                try:
                    stars = int(m.group(1))
                    cop = _STAR_TO_COP.get(stars, 4.0)
                except ValueError:
                    pass

        if capacity_kw is None or tonnage is None:
            continue

        cop = cop if cop is not None else 4.0

        # Try to derive a model name from the snippet (first ~60 chars)
        snippet_head = text[:60].strip()
        label = f"{snippet_head}  ({capacity_kw:.2f} kW)"
        if label in seen_labels:
            continue
        seen_labels.add(label)

        results.append({
            "brand": "Online",
            "model": snippet_head,
            "label": label,
            "capacity_kw": round(capacity_kw, 3),
            "cop_rated": round(cop, 2),
            "tonnage": tonnage,
        })

    return results


# ---------------------------------------------------------------------------
# Main search function
# ---------------------------------------------------------------------------

def search_models(query: str, online: bool = False) -> list[dict]:
    """
    Search for AC models.

    Args:
        query:  Search string (case-insensitive, matched against brand + model + label).
                Empty string returns all local models.
        online: If True, also call DuckDuckGo and merge results.

    Returns:
        Up to 20 results sorted by brand then tonnage.  Each result has:
        brand, model, label, capacity_kw, cop_rated, tonnage.
    """
    q = query.strip().lower()

    # --- Local search ---
    if q:
        local_hits = [
            r for r in LOCAL_DB
            if q in r["brand"].lower()
            or q in r["model"].lower()
            or q in r["label"].lower()
        ]
    else:
        local_hits = list(LOCAL_DB)

    # --- Online search ---
    online_hits: list[dict] = []
    if online and q:
        raw_online = search_ac_models_online(query)
        existing_labels = {r["label"] for r in local_hits}
        online_hits = [r for r in raw_online if r["label"] not in existing_labels]

    combined = local_hits + online_hits

    # Sort: brand alphabetically, then tonnage numerically
    combined.sort(key=lambda r: (r["brand"], r["tonnage"]))

    return combined[:20]
