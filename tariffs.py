"""
Indian residential electricity tariffs by state.

Rates are approximate domestic/residential blended averages (₹/kWh) for
moderate consumption (201–500 units/month), which is typical for a home
running 1–3 ACs. Sources: respective state DISCOM tariff orders, FY 2024-25.

These are blended averages across slabs — actual bills are slab-based.
Use as a reasonable estimate for simulation cost calculations.
"""

# State name (as returned by Nominatim) → (rate ₹/kWh, DISCOM name)
STATE_TARIFFS: dict[str, tuple[float, str]] = {
    # South India
    "Tamil Nadu":           (5.50, "TANGEDCO"),
    "Karnataka":            (6.00, "BESCOM / KPDCLs"),
    "Kerala":               (5.20, "KSEB"),
    "Andhra Pradesh":       (6.50, "APEPDCL / APSPDCL"),
    "Telangana":            (7.00, "TSSPDCL / TSNPDCL"),
    "Puducherry":           (4.50, "PPCL"),

    # West India
    "Maharashtra":          (8.00, "MSEDCL"),
    "Gujarat":              (5.50, "DGVCL / MGVCL / PGVCL / UGVCL"),
    "Goa":                  (3.50, "GPDCL"),
    "Rajasthan":            (7.00, "JVVNL / AVVNL / JdVVNL"),

    # North India
    "Delhi":                (6.50, "BSES Rajdhani / BSES Yamuna / TPDDL"),
    "Uttar Pradesh":        (6.00, "UPPCL"),
    "Haryana":              (6.50, "DHBVN / UHBVN"),
    "Punjab":               (6.00, "PSPCL"),
    "Himachal Pradesh":     (4.00, "HPSEBL"),
    "Uttarakhand":          (4.50, "UPCL"),
    "Jammu and Kashmir":    (3.50, "JKPDCL"),
    "Chandigarh":           (4.50, "CSPDCL"),

    # East India
    "West Bengal":          (7.00, "CESC / WBSEDCL"),
    "Odisha":               (5.50, "TPCODL / NESCO / WESCO / SOUTHCO"),
    "Bihar":                (6.50, "SBPDCL / NBPDCL"),
    "Jharkhand":            (6.00, "JBVNL"),
    "Assam":                (6.00, "APDCL"),

    # Central India
    "Madhya Pradesh":       (6.50, "MPEZ / MPMKVVCL"),
    "Chhattisgarh":         (5.00, "CSPDCL"),

    # Default fallback
    "_default":             (6.50, "National average estimate"),
}


def tariff_for_state(state_name: str) -> tuple[float, str]:
    """
    Look up electricity tariff for a given state name.
    Does a fuzzy match (case-insensitive, partial) to handle
    variations from Nominatim (e.g. 'Tamil Nadu' vs 'Tamilnadu').

    Returns (rate_inr_per_kwh, discom_name).
    """
    if not state_name:
        return STATE_TARIFFS["_default"]

    state_lower = state_name.lower().strip()
    for key, val in STATE_TARIFFS.items():
        if key == "_default":
            continue
        if key.lower() in state_lower or state_lower in key.lower():
            return val

    return STATE_TARIFFS["_default"]


def extract_state_from_display_name(display_name: str) -> str:
    """
    Nominatim display_name is comma-separated from specific to general:
      e.g. 'Chennai, Chennai District, Tamil Nadu, India'
    We scan parts from right to left (skipping 'India') to find a state match.
    """
    parts = [p.strip() for p in display_name.split(",")]
    # Skip the last part ('India') and try each part right-to-left
    for part in reversed(parts[:-1]):
        rate, discom = tariff_for_state(part)
        if discom != STATE_TARIFFS["_default"][1]:   # found a real match
            return part
    return ""
