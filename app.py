"""
Home AC Digital Twin — Multi-Unit Streamlit Dashboard

Run with:
    streamlit run app.py
"""

import calendar
import math
import re
from datetime import date, datetime, timezone, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from hvac_model import HVACModel, HVACParams, RoomParams, SimParams
from weather import fetch_current_temp, fetch_historical_daily_means
from tariffs import tariff_for_state, extract_state_from_display_name
from model_search import search_models


@st.cache_data(ttl=600)
def get_live_temp(city: str) -> dict:
    return fetch_current_temp(city)


@st.cache_data(ttl=86400)
def get_historical_means(lat: float, lon: float) -> dict:
    return fetch_historical_daily_means(lat, lon)


def estimate_initial_temp(
    outdoor_temp_c: float,
    hour: float,            # local hour, 0–23.99
    r_value: float,         # wall R-value in m²·K/W
    floor_area_m2: float,
    ceiling_height_m: float,
    diurnal_range_c: float = 8.0,   # peak-to-trough outdoor swing
    thermal_mass_mult: float = 3.0,
) -> float:
    """
    Estimate room temperature at `hour` using:
      1. Sinusoidal outdoor profile anchored to the current reading
      2. First-order thermal response of the room (time constant τ)

    Physics:
      T_out(t) = T_mean + A·cos(ω(t − t_peak))        outdoor profile
      T_room(t) = T_mean + A·α·cos(ω(t − t_peak) − φ) indoor response

    where:
      ω  = 2π/24 rad/h  (daily cycle)
      τ  = C / UA        room time constant in hours
      α  = 1/√(1+(ωτ)²) amplitude attenuation
      φ  = arctan(ωτ)   phase lag
    """
    T_PEAK_HOUR = 14.0                      # outdoor peaks at 2 pm
    A = diurnal_range_c / 2.0              # sinusoidal amplitude
    omega = 2 * math.pi / 24.0            # rad/h

    # Back-calculate daily mean from current reading and time of day
    T_mean = outdoor_temp_c - A * math.cos(omega * (hour - T_PEAK_HOUR))

    # Room thermal properties (SI)
    side = math.sqrt(floor_area_m2)
    envelope_area = 4 * side * ceiling_height_m + floor_area_m2   # m²
    UA = envelope_area / r_value / 1000       # kW/K
    C  = floor_area_m2 * ceiling_height_m * 1.2 * 1.005 * thermal_mass_mult  # kJ/K
    tau_hours = C / (UA * 3600)              # hours

    # Steady-state sinusoidal response
    omega_tau = omega * tau_hours
    alpha = 1.0 / math.sqrt(1 + omega_tau ** 2)   # attenuation
    phi   = math.atan(omega_tau)                   # phase lag (rad)

    T_room = T_mean + A * alpha * math.cos(omega * (hour - T_PEAK_HOUR) - phi)
    return round(T_room, 1)


# ---------------------------------------------------------------------------
# Page config & CSS
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Home AC Digital Twin", page_icon="🏠", layout="wide")

st.markdown("""
<style>
[data-testid="stMetric"] {
    background: #1e293b; border-radius: 10px;
    padding: 12px 16px; border: 1px solid #334155;
}
[data-testid="stMetricLabel"] { font-size: 0.78rem; color: #94a3b8; }
[data-testid="stMetricValue"] { font-size: 1.4rem; font-weight: 700; }
div.stButton > button { border-radius: 8px; font-size: 0.85rem; }
div[data-testid="stExpander"] { border: 1px solid #334155; border-radius: 10px; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Samsung model presets
# ---------------------------------------------------------------------------

SAMSUNG_MODELS = {
    "Samsung WindFree 1.0T  (3.52 kW)": {"capacity_kw": 3.517, "cop_rated": 3.8},
    "Samsung WindFree 1.5T  (5.28 kW)": {"capacity_kw": 5.275, "cop_rated": 4.0},
    "Samsung WindFree 2.0T  (7.03 kW)": {"capacity_kw": 7.034, "cop_rated": 3.9},
    "Samsung WindFree 2.5T  (8.79 kW)": {"capacity_kw": 8.793, "cop_rated": 3.8},
    "Samsung WindFree 3.0T (10.55 kW)": {"capacity_kw": 10.55, "cop_rated": 3.7},
    "Custom":                             {"capacity_kw": 5.275, "cop_rated": 4.0},
}

# Best-in-class 5-star BEE inverter ACs available in India (2024-25)
# Keyed by nearest tonnage bucket.  cop_best = rated COP at nominal cooling.
# price_inr = approximate MRP including installation (₹).
MARKET_BENCHMARKS = {
    1.0: {
        "cop_best":  5.00,
        "model":     "Samsung WindFree Elite 1.0T AR12CYGZBWK (5-star BEE, ISEER 5.0)",
        "price_inr": 42_000,
    },
    1.5: {
        "cop_best":  5.20,
        "model":     "LG Dual Inverter 1.5T RS-Q18YNZE (5-star BEE, ISEER 5.2)",
        "price_inr": 52_000,
    },
    2.0: {
        "cop_best":  4.90,
        "model":     "Daikin FTKF60TV 2.0T (5-star BEE, ISEER 4.9)",
        "price_inr": 68_000,
    },
    2.5: {
        "cop_best":  4.70,
        "model":     "Panasonic CS/CU-SU30ZKYF 2.5T (5-star BEE, ISEER 4.7)",
        "price_inr": 82_000,
    },
    3.0: {
        "cop_best":  4.50,
        "model":     "Blue Star IC318YNUS 3.0T (5-star BEE, ISEER 4.5)",
        "price_inr": 105_000,
    },
}
# Threshold: flag as outdated if current COP is more than 15 % below best available
_OUTDATED_COP_THRESHOLD = 0.85

# Wall construction types common in Indian residential buildings → R-value (m²·K/W)
WALL_CONSTRUCTION_MAP = {
    "150mm RCC / Bare Concrete":                    0.15,
    "115mm Red Brick + Plaster (half-brick)":       0.35,
    "200mm Solid Concrete Block + Plaster":         0.45,
    "200mm Fly Ash Brick + Plaster":                0.50,
    "230mm Red Brick + Plaster (both sides)":       0.60,   # most common apartment
    "200mm Hollow Concrete Block + Plaster":        0.65,
    "200mm AAC Block + Plaster":                    1.80,   # modern construction
    "230mm Brick + Thermocol / EPS Board":          2.50,
    "230mm Brick + Rockwool Insulation":            3.00,
}

WALL_DESCRIPTIONS = {
    "150mm RCC / Bare Concrete":                    "Structural columns/slabs. Almost no insulation — heat passes through quickly.",
    "115mm Red Brick + Plaster (half-brick)":       "Thin partition wall. Poor thermal resistance, common for internal walls.",
    "200mm Solid Concrete Block + Plaster":         "Dense block wall. Better mass but low insulation.",
    "200mm Fly Ash Brick + Plaster":                "Eco-friendly brick, slightly better than red brick. Common in newer buildings.",
    "230mm Red Brick + Plaster (both sides)":       "Standard 9-inch brick wall. Most common outer wall in Indian apartments.",
    "200mm Hollow Concrete Block + Plaster":        "Hollow block traps air — better than solid block. Common in South India.",
    "200mm AAC Block + Plaster":                    "Lightweight aerated block. ~3× better insulation than brick. Increasingly popular.",
    "230mm Brick + Thermocol / EPS Board":          "Brick wall with added foam board insulation. Premium residential construction.",
    "230mm Brick + Rockwool Insulation":            "Best-in-class for Indian residential. Rare but used in green buildings.",
}

# Backwards-compatible alias for any code that still references INSULATION_MAP
INSULATION_MAP = WALL_CONSTRUCTION_MAP

FT2_TO_M2 = 0.092903   # 1 sq ft → m²
FT_TO_M   = 0.3048     # 1 ft   → m

# ---------------------------------------------------------------------------
# Tonnage recommendation
# ---------------------------------------------------------------------------

# Wall-type adjustment factors — poor insulation increases effective heat load
WALL_LOAD_FACTORS = {
    "150mm RCC / Bare Concrete":                    1.20,
    "115mm Red Brick + Plaster (half-brick)":       1.10,
    "200mm Solid Concrete Block + Plaster":         1.15,
    "200mm Fly Ash Brick + Plaster":                1.08,
    "230mm Red Brick + Plaster (both sides)":       1.00,
    "200mm Hollow Concrete Block + Plaster":        1.00,
    "200mm AAC Block + Plaster":                    0.85,
    "230mm Brick + Thermocol / EPS Board":          0.75,
    "230mm Brick + Rockwool Insulation":            0.70,
}

# Volume thresholds (cu ft) for standard brick walls → tonnage
# Based on standard Indian residential sizing guidelines
VOLUME_THRESHOLDS = [
    (900,  0.75),
    (1500, 1.0),
    (2000, 1.5),
    (3000, 2.0),
    (4000, 2.5),
    (5000, 3.0),
]

# Maps recommended tonnage → closest Samsung model key
TON_TO_MODEL = {
    0.75: "Samsung WindFree 1.0T  (3.52 kW)",   # 0.75T not in lineup, use 1.0T
    1.0:  "Samsung WindFree 1.0T  (3.52 kW)",
    1.5:  "Samsung WindFree 1.5T  (5.28 kW)",
    2.0:  "Samsung WindFree 2.0T  (7.03 kW)",
    2.5:  "Samsung WindFree 2.5T  (8.79 kW)",
    3.0:  "Samsung WindFree 3.0T (10.55 kW)",
}


def recommend_tonnage(volume_cu_ft: float, wall_type: str) -> float:
    """
    Recommend AC tonnage for a room using Indian residential guidelines.
    Applies a wall-type load factor to the raw volume, then looks up
    the nearest standard tonnage from pre-defined thresholds.
    """
    factor = WALL_LOAD_FACTORS.get(wall_type, 1.0)
    effective_volume = volume_cu_ft * factor
    for threshold, tons in VOLUME_THRESHOLDS:
        if effective_volume <= threshold:
            return tons
    # Above 5,000 effective cu ft — needs multiple units
    return round(effective_volume / 1500, 1)

DEFAULT_UNIT = {
    "room_name":    "Room",
    "model":        "Samsung WindFree 1.5T  (5.28 kW)",
    "capacity_kw":  5.275,
    "cop_rated":    4.0,
    "floor_area":   320,   # sq ft — Living Room default
    "height":       9.0,   # ft
    "insulation":   "150mm RCC / Bare Concrete",
    "setpoint":     24.0,
    "initial_temp": 30.0,
    "min_ratio":    20,
    "kp":           0.5,
}

# Per-unit overrides applied on top of DEFAULT_UNIT
UNIT_OVERRIDES = {
    1: {"floor_area": 140},   # Bedroom 1 — smaller room
}

ROOM_NAMES = ["Living Room", "Bedroom 1", "Bedroom 2", "Bedroom 3",
              "Kitchen", "Office", "Dining Room", "Guest Room"]

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

if "num_units" not in st.session_state:
    st.session_state["num_units"] = 2
if "outdoor_temp" not in st.session_state:
    st.session_state["outdoor_temp"] = 35.0
if "duration_h" not in st.session_state:
    st.session_state["duration_h"] = 4
if "diurnal_range" not in st.session_state:
    st.session_state["diurnal_range"] = 8.0
if "electricity_rate" not in st.session_state:
    st.session_state["electricity_rate"] = 6.50
if "discom_name" not in st.session_state:
    st.session_state["discom_name"] = "National average estimate"

# Initialise per-unit keys
def _init_unit(i: int):
    name = ROOM_NAMES[i] if i < len(ROOM_NAMES) else f"Room {i+1}"
    overrides = UNIT_OVERRIDES.get(i, {})
    for field, val in DEFAULT_UNIT.items():
        key = f"u{i}_{field}"
        if key not in st.session_state:
            if field == "room_name":
                st.session_state[key] = name
            else:
                st.session_state[key] = overrides.get(field, val)

# Pre-init all 8 slots — but skip the slot that a pending _add_unit_action
# is about to populate, so the _init_unit default names (e.g. "Bedroom 3")
# cannot clobber what the action handler will write.
_pending_add_slot = (
    st.session_state["num_units"]
    if "_add_unit_action" in st.session_state
    else None
)
for i in range(8):
    if i != _pending_add_slot:
        _init_unit(i)

# ---------------------------------------------------------------------------
# Pending actions — processed BEFORE any widget is instantiated
# so that session_state writes don't conflict with widget keys
# ---------------------------------------------------------------------------

if "_add_unit_action" in st.session_state:
    action = st.session_state.pop("_add_unit_action")
    new_i = st.session_state["num_units"]
    if new_i < 8:
        st.session_state["num_units"] += 1
        st.session_state["_num_units_input"] = st.session_state["num_units"]
        n_after = st.session_state["num_units"]
        # Copy all fields from the source unit
        for field in DEFAULT_UNIT:
            src_key = f"u{action['src']}_{field}"
            dst_key = f"u{new_i}_{field}"
            st.session_state[dst_key] = st.session_state.get(src_key, DEFAULT_UNIT[field])
        # Apply overrides from action
        for field, val in action.get("overrides", {}).items():
            st.session_state[f"u{new_i}_{field}"] = val

        # Base room name comes directly from the action override — it is
        # already stripped of any suffix by the button handler. Do NOT
        # re-derive it from session_state to avoid picking up stale pre-init
        # values (e.g. "Bedroom 3") that _init_unit may have placed there.
        base_name = action.get("overrides", {}).get("room_name", "")
        if not base_name:
            raw_name = st.session_state[f"u{new_i}_room_name"]
            base_name = re.sub(r'\s*\(AC \d+\)$', '', raw_name).strip()

        # Find existing units (excluding the new one) that belong to this room
        existing_same_room = [
            j for j in range(new_i)
            if re.sub(r'\s*\(AC \d+\)$', '', st.session_state.get(f"u{j}_room_name", "")).strip() == base_name
        ]

        # Total area comes from the existing units only (new unit not yet sized correctly)
        total_area = sum(
            float(st.session_state.get(f"u{j}_floor_area", DEFAULT_UNIT["floor_area"]))
            for j in existing_same_room
        )

        # All same-room units including the new one
        same_room = existing_same_room + [new_i]
        split_area = round(total_area / len(same_room))

        # Rename and resize every unit in this room
        for idx, j in enumerate(same_room):
            st.session_state[f"u{j}_room_name"] = f"{base_name} (AC {idx + 1})"
            st.session_state[f"u{j}_floor_area"] = split_area
            # Bump widget-key versions so widgets pick up the new values
            # (Streamlit 1.35 ignores session_state writes to already-rendered widget keys;
            # a fresh key has no cached frontend state and always reads from session_state)
            st.session_state[f"_fa_ver_{j}"] = st.session_state.get(f"_fa_ver_{j}", 0) + 1
        # Bump model version for the new slot only — existing units keep user-set models
        st.session_state[f"_model_ver_{new_i}"] = st.session_state.get(f"_model_ver_{new_i}", 0) + 1

if "_remove_unit_action" in st.session_state:
    action = st.session_state.pop("_remove_unit_action")
    remove_idx = action["remove_idx"]
    base_name  = action["base_name"]
    n_before   = st.session_state["num_units"]

    if n_before > 1:
        # Capture the full room area BEFORE removing/shifting — sum all same-room
        # units including the one being removed so redistribution uses the true total.
        _all_same = [
            j for j in range(n_before)
            if re.sub(r'\s*\(AC \d+\)$', '', st.session_state.get(f"u{j}_room_name", "")).strip() == base_name
        ]
        _room_total_area = sum(
            float(st.session_state.get(f"u{j}_floor_area", DEFAULT_UNIT["floor_area"]))
            for j in _all_same
        )

        # Shift every unit above remove_idx down by one slot
        _unit_fields = list(DEFAULT_UNIT.keys()) + ["override_temp"]
        for j in range(remove_idx, n_before - 1):
            for field in _unit_fields:
                src = f"u{j+1}_{field}"
                dst = f"u{j}_{field}"
                if src in st.session_state:
                    st.session_state[dst] = st.session_state[src]
            # Bump versioned widget keys so shifted data is picked up cleanly
            st.session_state[f"_fa_ver_{j}"] = st.session_state.get(f"_fa_ver_{j}", 0) + 1
            st.session_state[f"_model_ver_{j}"] = st.session_state.get(f"_model_ver_{j}", 0) + 1
        # Clear the vacated last slot and its versioned keys
        for field in _unit_fields:
            st.session_state.pop(f"u{n_before - 1}_{field}", None)
        st.session_state.pop(f"_fa_ver_{n_before - 1}", None)
        st.session_state.pop(f"_model_ver_{n_before - 1}", None)

        st.session_state["num_units"] -= 1
        st.session_state["_num_units_input"] = st.session_state["num_units"]
        n_after = st.session_state["num_units"]

        # Find remaining units for this room and redistribute area
        remaining = [
            j for j in range(n_after)
            if re.sub(r'\s*\(AC \d+\)$', '', st.session_state.get(f"u{j}_room_name", "")).strip() == base_name
        ]
        if remaining:
            total_area = _room_total_area   # pre-captured before shift/removal
            if len(remaining) == 1:
                st.session_state[f"u{remaining[0]}_room_name"] = base_name
                st.session_state[f"u{remaining[0]}_floor_area"] = round(total_area)
                st.session_state[f"_fa_ver_{remaining[0]}"] = st.session_state.get(f"_fa_ver_{remaining[0]}", 0) + 1
            else:
                split_area = round(total_area / len(remaining))
                for idx, j in enumerate(remaining):
                    st.session_state[f"u{j}_room_name"] = f"{base_name} (AC {idx + 1})"
                    st.session_state[f"u{j}_floor_area"] = split_area
                    st.session_state[f"_fa_ver_{j}"] = st.session_state.get(f"_fa_ver_{j}", 0) + 1

# ---------------------------------------------------------------------------
# Sidebar — global settings only
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## 🌍 Live Weather")
    city_input = st.text_input("City", value="Chennai")
    if st.button("🌡️ Fetch Live Temperature", width='stretch'):
        with st.spinner("Fetching..."):
            result = get_live_temp(city_input)
        if result["error"]:
            st.error(result["error"])
        else:
            st.session_state["outdoor_temp"] = result["temp_c"]
            st.session_state["_live_weather"] = result
            # Auto-detect state and look up electricity tariff
            state = extract_state_from_display_name(result.get("display_name", ""))
            rate, discom = tariff_for_state(state)
            st.session_state["electricity_rate"] = rate
            st.session_state["discom_name"] = discom
            st.session_state["_detected_state"] = state or "Unknown"
            st.rerun()

    if "_live_weather" in st.session_state:
        w = st.session_state["_live_weather"]
        st.success(
            f"**{w['city']}** — {w['temp_c']} °C  \n"
            f"🕐 {w['fetched_at'].replace('T', ' ').replace('Z', ' UTC')}"
        )
        if "_detected_state" in st.session_state:
            st.info(
                f"📍 **{st.session_state['_detected_state']}**  \n"
                f"💡 {st.session_state['discom_name']}  \n"
                f"₹ {st.session_state['electricity_rate']:.2f} / kWh"
            )

    st.divider()
    st.markdown("## 🌤️ Global Conditions")
    st.slider("Outdoor Temperature (°C)", -10.0, 50.0, step=0.5, key="outdoor_temp")
    st.slider("Simulation Duration (hours)", 1, 24, key="duration_h")

    # Time of day — defaults to current IST
    IST = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(IST)
    default_hour = now_ist.hour + now_ist.minute / 60
    if "time_of_day_h" not in st.session_state:
        st.session_state["time_of_day_h"] = round(default_hour, 1)

    st.markdown("## 🕐 Time of Day (IST)")
    st.caption(f"Current IST: **{now_ist.strftime('%I:%M %p')}**")
    st.slider("Hour (for initial temp estimation)", 0.0, 23.9, step=0.5,
              key="time_of_day_h", format="%.1f")
    st.slider("Diurnal Temp Range (°C)", 4.0, 15.0, step=0.5,
              key="diurnal_range",
              help="Typical outdoor peak-to-trough swing. Chennai: ~8°C. Drier inland cities: up to 12–15°C.")

    st.markdown("## ⚡ Electricity Tariff")
    discom = st.session_state.get("discom_name", "National average estimate")
    state  = st.session_state.get("_detected_state", "")
    if state:
        st.caption(f"Auto-detected: **{state}** · {discom}")
    else:
        st.caption("Fetch live weather to auto-detect tariff, or set manually below.")
    st.slider("Rate (₹ / kWh)", 1.0, 15.0, step=0.25, key="electricity_rate",
              help="Residential domestic rate. Auto-set from state DISCOM tariff when you fetch live weather.")

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.markdown("# 🏠 Home AC Digital Twin")
st.caption("Simulate your home's AC units room by room — real weather, Indian wall types, state electricity tariffs.")

st.divider()

# ---------------------------------------------------------------------------
# Unit configuration section
# ---------------------------------------------------------------------------

st.markdown("## ⚙️ AC Unit Configuration")

cfg_col, _ = st.columns([2, 5])
with cfg_col:
    num_units = st.number_input(
        "Number of AC units", min_value=1, max_value=8,
        value=st.session_state["num_units"], step=1,
        key="_num_units_input",
    )
    st.session_state["num_units"] = int(num_units)

n = st.session_state["num_units"]

# Pre-sync all preset capacities/COPs before any card renders.
# Read from the versioned widget key first — Streamlit loads the user's current
# selectbox interaction into the versioned key at the start of the rerun,
# before any card renders, so this avoids a one-render lag.
for _j in range(n):
    _ver_j   = st.session_state.get(f"_model_ver_{_j}", 0)
    _vkey_j  = f"_model_{_j}_v{_ver_j}"
    _model_j = st.session_state.get(_vkey_j) or st.session_state.get(f"u{_j}_model", DEFAULT_UNIT["model"])
    if _model_j in SAMSUNG_MODELS and _model_j != "Custom":
        st.session_state[f"u{_j}_capacity_kw"] = SAMSUNG_MODELS[_model_j]["capacity_kw"]
        st.session_state[f"u{_j}_cop_rated"]   = SAMSUNG_MODELS[_model_j]["cop_rated"]
    # else: capacity_kw / cop_rated already set by model_search sync-back or user override

# Sync canonical room names into separate widget keys.
# We use _rn_{j} as the text_input key (decoupled from u{j}_room_name) so that
# the action handler's writes to u{j}_room_name are always reflected in the
# expander title even before the widget renders.  The on_change callback
# (defined below) writes the widget value back to u{j}_room_name.
for _j in range(n):
    st.session_state[f"_rn_{_j}"] = st.session_state.get(f"u{_j}_room_name", f"Unit {_j+1}")

# Render unit config cards — 2 per row
for row_start in range(0, n, 2):
    cols = st.columns(2, gap="medium")
    for col_idx, i in enumerate(range(row_start, min(row_start + 2, n))):
        with cols[col_idx]:
            room_label = st.session_state[f"u{i}_room_name"]
            with st.expander(f"🏠 Unit {i+1} — {room_label}", expanded=True):

                c1, c2 = st.columns(2)
                with c1:
                    def _on_rn_change(_i=i):
                        st.session_state[f"u{_i}_room_name"] = st.session_state[f"_rn_{_i}"]
                    st.text_input("Room Name", key=f"_rn_{i}", on_change=_on_rn_change)
                with c2:
                    _model_ver = st.session_state.get(f"_model_ver_{i}", 0)
                    _model_key = f"_model_{i}_v{_model_ver}"

                    # All models from local DB — shown in the dropdown
                    # Streamlit selectbox allows typing to filter when the dropdown is open
                    _all_results = search_models("", online=False)
                    _option_labels = [r["label"] for r in _all_results]
                    if _model_key not in st.session_state:
                        st.session_state[_model_key] = st.session_state.get(f"u{i}_model", DEFAULT_UNIT["model"])
                    _cur_label = st.session_state.get(_model_key, DEFAULT_UNIT["model"])
                    if _cur_label not in _option_labels:
                        _option_labels = [_cur_label] + _option_labels

                    st.selectbox(
                        "AC Model",
                        options=_option_labels,
                        key=_model_key,
                    )
                    # Sync committed widget value back to canonical key
                    st.session_state[f"u{i}_model"] = st.session_state[_model_key]
                    # Look up specs from local DB
                    _sel_label = st.session_state[_model_key]
                    _match = next((r for r in _all_results if r["label"] == _sel_label), None)
                    if _match:
                        st.session_state[f"u{i}_capacity_kw"] = _match["capacity_kw"]
                        st.session_state[f"u{i}_cop_rated"] = _match["cop_rated"]

                # Auto-fill capacity/COP from model selection
                selected_model = st.session_state[f"u{i}_model"]
                if selected_model in SAMSUNG_MODELS:
                    preset = SAMSUNG_MODELS[selected_model]
                    if selected_model != "Custom":
                        st.session_state[f"u{i}_capacity_kw"] = preset["capacity_kw"]
                        st.session_state[f"u{i}_cop_rated"] = preset["cop_rated"]
                elif _match is not None:
                    # capacity/COP already set above in the with c2 block
                    preset = {"capacity_kw": _match["capacity_kw"], "cop_rated": _match["cop_rated"]}
                else:
                    # Custom or unknown — leave capacity/COP as-is
                    preset = {
                        "capacity_kw": st.session_state.get(f"u{i}_capacity_kw", DEFAULT_UNIT["capacity_kw"]),
                        "cop_rated":   st.session_state.get(f"u{i}_cop_rated",   DEFAULT_UNIT["cop_rated"]),
                    }

                c3, c4 = st.columns(2)
                with c3:
                    _fa_ver = st.session_state.get(f"_fa_ver_{i}", 0)
                    _fa_key = f"_fa_{i}_v{_fa_ver}"
                    if _fa_key not in st.session_state:
                        st.session_state[_fa_key] = int(st.session_state.get(f"u{i}_floor_area", DEFAULT_UNIT["floor_area"]))
                    st.number_input(
                        "Floor Area (sq ft)", min_value=50, max_value=5000, step=1,
                        key=_fa_key,
                    )
                    st.session_state[f"u{i}_floor_area"] = int(st.session_state[_fa_key])
                with c4:
                    st.number_input(
                        "Ceiling Height (ft)", min_value=6.5, max_value=20.0, step=0.5,
                        format="%.1f", key=f"u{i}_height",
                    )

                st.selectbox(
                    "Wall Construction Type",
                    options=list(WALL_CONSTRUCTION_MAP.keys()),
                    key=f"u{i}_insulation",
                    help="Select the outer wall construction of this room.",
                )
                wall_key = st.session_state[f"u{i}_insulation"]
                r_val = WALL_CONSTRUCTION_MAP[wall_key]
                st.caption(
                    f"R = {r_val} m²K/W  ·  {WALL_DESCRIPTIONS[wall_key]}"
                )

                st.number_input(
                    "Setpoint (°C)", min_value=16.0, max_value=30.0, step=0.5,
                    format="%.1f", key=f"u{i}_setpoint",
                )

                # Auto-estimate initial room temp from wall type + time of day
                r_val_i = WALL_CONSTRUCTION_MAP[st.session_state[f"u{i}_insulation"]]
                auto_temp = estimate_initial_temp(
                    outdoor_temp_c=float(st.session_state["outdoor_temp"]),
                    hour=float(st.session_state.get("time_of_day_h", 12.0)),
                    r_value=r_val_i,
                    floor_area_m2=float(st.session_state[f"u{i}_floor_area"]) * FT2_TO_M2,
                    ceiling_height_m=float(st.session_state[f"u{i}_height"]) * FT_TO_M,
                    diurnal_range_c=float(st.session_state.get("diurnal_range", 8.0)),
                )

                override_key = f"u{i}_override_temp"
                if override_key not in st.session_state:
                    st.session_state[override_key] = False

                override = st.checkbox("Override initial temp", key=override_key)
                if override:
                    st.number_input(
                        "Initial Room Temp (°C)", min_value=5.0, max_value=45.0,
                        step=0.5, format="%.1f", key=f"u{i}_initial_temp",
                    )
                else:
                    st.session_state[f"u{i}_initial_temp"] = auto_temp
                    IST = timezone(timedelta(hours=5, minutes=30))
                    h = int(st.session_state.get("time_of_day_h", 12))
                    m = int((st.session_state.get("time_of_day_h", 12) % 1) * 60)
                    ampm = "AM" if h < 12 else "PM"
                    h12 = h % 12 or 12
                    st.info(f"🌡️ Estimated initial temp: **{auto_temp} °C**  \n"
                            f"Wall R={r_val_i} · {h12}:{m:02d} {ampm}")

                if selected_model not in SAMSUNG_MODELS or selected_model == "Custom":
                    st.number_input(
                        "Capacity (kW)", min_value=1.0, max_value=20.0,
                        step=0.1, format="%.2f", key=f"u{i}_capacity_kw",
                    )
                else:
                    st.metric("Capacity", f"{preset['capacity_kw']:.3f} kW")

                if selected_model not in SAMSUNG_MODELS or selected_model == "Custom":
                    st.number_input(
                        "COP (Efficiency)", min_value=1.5, max_value=7.0,
                        step=0.1, format="%.1f", key=f"u{i}_cop_rated",
                    )
                else:
                    st.caption(f"COP: {preset['cop_rated']}  ·  Min speed: 20%")

                # ── Tonnage recommendation ──────────────────────────────
                st.divider()
                volume_cu_ft = (
                    float(st.session_state[f"u{i}_floor_area"]) *
                    float(st.session_state[f"u{i}_height"])
                )
                wall_key_i  = st.session_state[f"u{i}_insulation"]
                current_kw  = float(st.session_state[f"u{i}_capacity_kw"])
                current_ton = round(current_kw / 3.517, 2)
                room_name_i = st.session_state[f"u{i}_room_name"]
                base_name_i = re.sub(r'\s*\(AC \d+\)$', '', room_name_i).strip()
                same_room_indices = [
                    j for j in range(n)
                    if re.sub(r'\s*\(AC \d+\)$', '', st.session_state.get(f"u{j}_room_name", "")).strip() == base_name_i
                ]
                same_room_count = len(same_room_indices)
                is_primary_unit = (i == min(same_room_indices))

                # Always work with the FULL room — sum actual areas across all same-room units
                full_volume = sum(
                    float(st.session_state.get(f"u{j}_floor_area", DEFAULT_UNIT["floor_area"])) *
                    float(st.session_state.get(f"u{j}_height", DEFAULT_UNIT["height"]))
                    for j in same_room_indices
                )
                full_rec_ton  = recommend_tonnage(full_volume, wall_key_i)
                # Sum ACTUAL tonnage of every unit in this room (not just primary × count)
                total_current = round(sum(
                    float(st.session_state.get(f"u{j}_capacity_kw", DEFAULT_UNIT["capacity_kw"])) / 3.517
                    for j in same_room_indices
                ), 2)
                shortfall     = round(full_rec_ton - total_current, 2)
                if shortfall > 0 and current_ton > 0:
                    # Not enough — how many more units of current_ton are needed?
                    extra_needed = math.ceil(shortfall / current_ton)
                elif total_current - full_rec_ton > current_ton:
                    # Oversized by more than a full unit's worth → suggest downsize
                    extra_needed = -1
                else:
                    # Within one unit's worth of overage — considered adequate
                    extra_needed = 0

                st.caption(
                    f"📐 Full room volume: **{full_volume:,.0f} cu ft**  ·  "
                    f"Required: **{full_rec_ton}T**  ·  "
                    f"Installed: **{total_current}T** across {same_room_count} unit(s)"
                )

                if is_primary_unit:
                    if extra_needed == 0 and same_room_count == 1:
                        st.success(f"✅ {current_ton}T is well-matched for this room.")
                    elif extra_needed == 0:
                        st.success(
                            f"✅ {same_room_count} units combined = **{total_current}T** — "
                            f"covers this room's **{full_rec_ton}T** requirement."
                        )
                    elif extra_needed > 0:
                        # Suggest upgrading existing units before adding more
                        ideal_per_unit = full_rec_ton / same_room_count
                        rec_upgrade_ton = next(
                            (t for t in sorted(TON_TO_MODEL) if t >= ideal_per_unit),
                            max(TON_TO_MODEL),
                        )
                        rec_upgrade_model = TON_TO_MODEL[rec_upgrade_ton].split("(")[0].strip()
                        st.warning(
                            f"⚠️ Room needs **{full_rec_ton}T** total. "
                            f"Installed **{total_current}T** ({same_room_count} unit(s)) is insufficient.  \n"
                            f"Option A — Upgrade each unit to **{rec_upgrade_ton}T** "
                            f"({rec_upgrade_model}): {same_room_count} × {rec_upgrade_ton}T = "
                            f"{round(same_room_count * rec_upgrade_ton, 2)}T.  \n"
                            f"Option B — Add **{extra_needed}** more unit(s) of {current_ton}T."
                        )
                        if st.session_state["num_units"] < 8:
                            if st.button(
                                f"➕ Add unit for {base_name_i}",
                                key=f"add_unit_btn_{i}",
                                use_container_width=True,
                            ):
                                st.session_state["_add_unit_action"] = {
                                    "src": i,
                                    "overrides": {"room_name": base_name_i},
                                }
                                st.rerun()
                        else:
                            st.caption("Maximum 8 units reached.")
                    else:  # extra_needed < 0 — oversized by > 1 unit worth → suggest downsize
                        need_per_unit  = full_rec_ton / same_room_count
                        rec_unit_ton   = next(
                            (t for t in sorted(TON_TO_MODEL) if t >= need_per_unit),
                            max(TON_TO_MODEL),
                        )
                        rec_model_name = TON_TO_MODEL[rec_unit_ton].split("(")[0].strip()
                        st.warning(
                            f"⚠️ Installed **{total_current}T** ({same_room_count} units) exceeds "
                            f"this room's **{full_rec_ton}T** requirement.  \n"
                            f"Each unit needs ~**{need_per_unit:.2f}T** — "
                            f"downsize each unit to **{rec_unit_ton}T** ({rec_model_name}): "
                            f"{same_room_count} × {rec_unit_ton}T = {round(same_room_count * rec_unit_ton, 2)}T.  \n"
                            f"Or remove a unit below."
                        )
                        remove_options = {
                            f"{st.session_state.get(f'u{j}_room_name', f'Room {j+1}')}": j
                            for j in same_room_indices
                        }
                        selected_label = st.selectbox(
                            "Remove a unit",
                            options=list(remove_options.keys()),
                            key=f"remove_select_{i}",
                        )
                        selected_j = remove_options[selected_label]
                        if st.button(
                            "➖ Remove selected unit",
                            key=f"remove_unit_btn_{i}",
                            use_container_width=True,
                        ):
                            st.session_state["_remove_unit_action"] = {
                                "remove_idx": selected_j,
                                "base_name": base_name_i,
                            }
                            st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Run simulations for all units
# ---------------------------------------------------------------------------

results = []   # list of (unit_label, df, model, sim, hvac, room)

for i in range(n):
    r = RoomParams(
        floor_area_m2=float(st.session_state[f"u{i}_floor_area"]) * FT2_TO_M2,
        ceiling_height_m=float(st.session_state[f"u{i}_height"]) * FT_TO_M,
        insulation_r=INSULATION_MAP[st.session_state[f"u{i}_insulation"]],
    )
    h = HVACParams(
        capacity_kw=float(st.session_state[f"u{i}_capacity_kw"]),
        cop_rated=float(st.session_state[f"u{i}_cop_rated"]),
        min_ratio=0.20,
        kp=0.5,
    )
    s = SimParams(
        duration_hours=float(st.session_state["duration_h"]),
        initial_temp_c=float(st.session_state[f"u{i}_initial_temp"]),
        outdoor_temp_c=float(st.session_state["outdoor_temp"]),
        setpoint_c=float(st.session_state[f"u{i}_setpoint"]),
    )
    mdl = HVACModel(r, h, s)
    history = mdl.simulate()
    df = pd.DataFrame([
        {
            "time_h":        s.time_min / 60,
            "room_temp":     round(s.room_temp_c, 3),
            "modulation_pct": round(s.modulation_ratio * 100, 1),
            "mode":          s.mode,
            "energy_kwh":    round(s.energy_kwh, 4),
            "q_hvac_kw":     round(s.q_hvac_kw, 3),
            "q_outdoor_kw":  round(s.q_outdoor_kw, 3),
            "effective_cop": round(s.effective_cop, 2),
        }
        for s in history
    ])
    label = st.session_state[f"u{i}_room_name"]
    results.append((label, df, mdl, s, h, r))

# ---------------------------------------------------------------------------
# Results — tabs
# ---------------------------------------------------------------------------

st.markdown("## 📊 Simulation Results")

tab_labels = [label for label, *_ in results] + ["🔍 Fleet Overview"]
tabs = st.tabs(tab_labels)

MODE_LINE = {"cooling": "#60a5fa", "windfree": "#34d399", "heating": "#fb923c", "off": "#64748b"}
MODE_FILL = {"cooling": "rgba(96,165,250,0.35)", "windfree": "rgba(52,211,153,0.35)",
             "heating": "rgba(251,146,60,0.35)", "off": "rgba(100,116,139,0.15)"}


def build_unit_chart(df, sim, label):
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        subplot_titles=(
            f"Room Temperature — {label}",
            "Inverter Compressor Load  (🔵 Cooling · 🟢 WindFree · ⬜ Off)",
            "Cumulative Energy (kWh)",
        ),
        row_heights=[0.48, 0.24, 0.28],
        vertical_spacing=0.09,
    )

    # Temperature
    fig.add_trace(go.Scatter(
        x=df["time_h"], y=df["room_temp"], name="Room Temp",
        line=dict(color="#f87171", width=2.5),
        hovertemplate="Time: %{x:.2f} h<br>Temp: %{y:.2f} °C<extra></extra>",
    ), row=1, col=1)
    fig.add_hline(y=sim.setpoint_c, line_dash="dash", line_color="#60a5fa",
                  annotation_text=f"Setpoint {sim.setpoint_c}°C",
                  annotation_position="top right", row=1, col=1)
    fig.add_hline(y=sim.outdoor_temp_c, line_dash="dot", line_color="#fb923c",
                  annotation_text=f"Outdoor {sim.outdoor_temp_c}°C",
                  annotation_position="bottom right", row=1, col=1)

    # WindFree shading on temp chart
    in_wf, wf_start = False, None
    for _, row in df.iterrows():
        if row["mode"] == "windfree" and not in_wf:
            in_wf, wf_start = True, row["time_h"]
        elif row["mode"] != "windfree" and in_wf:
            in_wf = False
            fig.add_vrect(x0=wf_start, x1=row["time_h"],
                          fillcolor="rgba(52,211,153,0.08)", line_width=0, row=1, col=1)
    if in_wf:
        fig.add_vrect(x0=wf_start, x1=df["time_h"].iloc[-1],
                      fillcolor="rgba(52,211,153,0.08)", line_width=0, row=1, col=1)

    # Modulation — colour by mode
    cur_mode, seg_start = df["mode"].iloc[0], 0
    for i, row in df.iterrows():
        if row["mode"] != cur_mode or i == len(df) - 1:
            seg = df.iloc[seg_start: i + 1]
            fig.add_trace(go.Scatter(
                x=seg["time_h"], y=seg["modulation_pct"],
                fill="tozeroy", mode="lines",
                line=dict(color=MODE_LINE.get(cur_mode, "#64748b"), width=1.5),
                fillcolor=MODE_FILL.get(cur_mode, "rgba(100,116,139,0.15)"),
                showlegend=False,
                hovertemplate="Time: %{x:.2f} h<br>Load: %{y:.0f}%<br>Mode: " + cur_mode + "<extra></extra>",
            ), row=2, col=1)
            seg_start, cur_mode = i, row["mode"]
    fig.update_yaxes(title_text="%", range=[0, 105], row=2, col=1)

    # Energy
    fig.add_trace(go.Scatter(
        x=df["time_h"], y=df["energy_kwh"], fill="tozeroy",
        line=dict(color="#a78bfa", width=2), fillcolor="rgba(167,139,250,0.25)",
        hovertemplate="Time: %{x:.2f} h<br>Energy: %{y:.4f} kWh<extra></extra>",
    ), row=3, col=1)

    fig.update_xaxes(title_text="Time (hours)", row=3, col=1)
    fig.update_yaxes(title_text="°C", row=1, col=1)
    fig.update_yaxes(title_text="kWh", row=3, col=1)
    fig.update_layout(
        height=620, template="plotly_dark", showlegend=False,
        margin=dict(t=50, b=20, l=60, r=40),
        paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
    )
    return fig


# ── Per-unit tabs ─────────────────────────────────────────────────────────

for tab_idx, (tab, (label, df, mdl, sim, hvac, room)) in enumerate(zip(tabs[:-1], results)):
    with tab:
        final = df.iloc[-1]
        temp_delta = final["room_temp"] - sim.setpoint_c
        at_sp = df[abs(df["room_temp"] - sim.setpoint_c) <= 0.3]
        t2sp = at_sp.iloc[0]["time_h"] * 60 if not at_sp.empty else None
        wf_pct = (df["mode"] == "windfree").mean() * 100
        avg_mod = df.loc[df["modulation_pct"] > 0, "modulation_pct"].mean()
        cooling_kwh = abs(df["q_hvac_kw"]).sum() * (sim.dt_minutes / 60)
        eff_cop = cooling_kwh / final["energy_kwh"] if final["energy_kwh"] > 0 else 0

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Final Temp", f"{final['room_temp']:.1f} °C",
                  f"{temp_delta:+.1f}°C", delta_color="inverse")
        m2.metric("Energy Used", f"{final['energy_kwh']:.3f} kWh")
        rate = st.session_state["electricity_rate"]
        m3.metric(f"Cost (₹{rate:.2f}/kWh)", f"₹{final['energy_kwh']*rate:.2f}")
        m4.metric("Avg Load", f"{avg_mod:.0f}%" if not pd.isna(avg_mod) else "0%")
        m5.metric("WindFree Time", f"{wf_pct:.0f}%")
        m6.metric("Time to Setpoint",
                  f"{t2sp:.0f} min" if t2sp is not None else "Not reached")

        st.plotly_chart(build_unit_chart(df, sim, label), width='stretch', key=f"unit_chart_{tab_idx}")

        with st.expander("🏗️ Room Physics Details"):
            area_sqft = st.session_state[f"u{list(t for t,*_ in results).index(label)}_floor_area"]
            height_ft = st.session_state[f"u{list(t for t,*_ in results).index(label)}_height"]
            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Room Size", f"{area_sqft} sq ft × {height_ft} ft")
            p2.metric("UA Coefficient", f"{mdl.ua_kw_per_k*1000:.1f} W/K")
            p3.metric("Thermal Mass", f"{mdl.thermal_mass_kj_per_k:.1f} kJ/K")
            p4.metric("Time Constant",
                      f"{mdl.thermal_mass_kj_per_k / mdl.ua_kw_per_k / 60:.0f} min")

# ── Fleet Overview tab ────────────────────────────────────────────────────

with tabs[-1]:
    st.markdown("### Fleet Summary")

    # Summary table
    rows = []
    for i, (label, df, mdl, sim, hvac, room) in enumerate(results):
        final = df.iloc[-1]
        at_sp = df[abs(df["room_temp"] - sim.setpoint_c) <= 0.3]
        t2sp = at_sp.iloc[0]["time_h"] * 60 if not at_sp.empty else None
        wf_pct = (df["mode"] == "windfree").mean() * 100
        cooling_kwh = abs(df["q_hvac_kw"]).sum() * (sim.dt_minutes / 60)
        eff_cop = cooling_kwh / final["energy_kwh"] if final["energy_kwh"] > 0 else 0
        rows.append({
            "Room":           label,
            "Model":          st.session_state[f"u{i}_model"].split("  ")[0],
            "Area (sq ft)":   round(room.floor_area_m2 / FT2_TO_M2),
            "Wall Type":      st.session_state[f"u{i}_insulation"].split(" + ")[0],
            "Setpoint (°C)":  sim.setpoint_c,
            "Final Temp (°C)": round(final["room_temp"], 1),
            "Energy (kWh)":   round(final["energy_kwh"], 3),
            "Cost (₹)":       round(final["energy_kwh"] * st.session_state["electricity_rate"], 2),
            "Eff. COP":       round(eff_cop, 2),
            "WindFree (%)":   round(wf_pct, 0),
            "To Setpoint (min)": round(t2sp, 0) if t2sp else None,
        })

    st.dataframe(
        pd.DataFrame(rows).set_index("Room"),
        width='stretch',
    )

    # Comparison bar charts
    summary_df = pd.DataFrame(rows)

    bc1, bc2 = st.columns(2)

    with bc1:
        fig_e = go.Figure(go.Bar(
            x=summary_df["Room"], y=summary_df["Energy (kWh)"],
            marker_color="#a78bfa",
            text=summary_df["Energy (kWh)"].apply(lambda v: f"{v:.3f}"),
            textposition="outside",
        ))
        fig_e.update_layout(
            title="Energy Consumed per Unit (kWh)",
            template="plotly_dark", height=320,
            paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
            margin=dict(t=50, b=40),
            yaxis_title="kWh",
        )
        st.plotly_chart(fig_e, width='stretch', key="fleet_energy_bar")

    with bc2:
        fig_c = go.Figure(go.Bar(
            x=summary_df["Room"], y=summary_df["Eff. COP"],
            marker_color="#34d399",
            text=summary_df["Eff. COP"].apply(lambda v: f"{v:.2f}"),
            textposition="outside",
        ))
        fig_c.update_layout(
            title="Effective COP per Unit",
            template="plotly_dark", height=320,
            paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
            margin=dict(t=50, b=40),
            yaxis_title="COP",
        )
        st.plotly_chart(fig_c, width='stretch', key="fleet_cop_bar")

    # Temperature overlay — all units on one chart
    st.markdown("### Temperature Curves — All Rooms")
    fig_all = go.Figure()
    colors = ["#f87171", "#60a5fa", "#34d399", "#fb923c",
              "#a78bfa", "#f472b6", "#facc15", "#38bdf8"]
    for idx, (label, df, mdl, sim, hvac, room) in enumerate(results):
        fig_all.add_trace(go.Scatter(
            x=df["time_h"], y=df["room_temp"],
            name=label, line=dict(color=colors[idx % len(colors)], width=2),
        ))
        fig_all.add_hline(
            y=sim.setpoint_c, line_dash="dot",
            line_color=colors[idx % len(colors)],
            line_width=0.8, opacity=0.5,
        )
    fig_all.update_layout(
        template="plotly_dark", height=350,
        xaxis_title="Time (hours)", yaxis_title="°C",
        paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
        legend=dict(orientation="h", y=1.12),
        margin=dict(t=30, b=40),
    )
    st.plotly_chart(fig_all, width='stretch', key="fleet_temp_overlay")

    total_energy = sum(df.iloc[-1]["energy_kwh"] for _, df, *_ in results)
    st.info(
        f"**Total fleet energy:** {total_energy:.3f} kWh  ·  "
        f"**Total cost:** ₹{total_energy * st.session_state['electricity_rate']:.2f}  ·  "
        f"**Outdoor temp:** {st.session_state['outdoor_temp']}°C  ·  "
        f"**Duration:** {st.session_state['duration_h']} h"
    )

# ---------------------------------------------------------------------------
# Monthly Energy Projection
# ---------------------------------------------------------------------------

st.divider()
st.markdown("## 📅 Monthly Energy Projection")

if "_live_weather" not in st.session_state:
    st.info(
        "ℹ️ Fetch live weather first (sidebar) to enable monthly projection — "
        "the city coordinates are needed to pull 10-year historical temperature data."
    )
else:
    _w = st.session_state["_live_weather"]
    _proj_lat = _w["lat"]
    _proj_lon = _w["lon"]
    _proj_city = _w["city"]
    st.caption(f"📍 {_proj_city} · lat={_proj_lat:.4f}, lon={_proj_lon:.4f}")

    # Compute next 3 months
    _today = date.today()
    _proj_months = []
    for _mo in range(1, 4):
        _m = (_today.month - 1 + _mo) % 12 + 1
        _y = _today.year + ((_today.month - 1 + _mo) // 12)
        _proj_months.append((_y, _m))
    _span_label = " · ".join(date(_y, _m, 1).strftime("%b %Y") for _y, _m in _proj_months)
    _total_days = sum(calendar.monthrange(_y, _m)[1] for _y, _m in _proj_months)

    # Config hash for staleness detection
    _cfg_parts = [st.session_state["num_units"]]
    for _ci in range(st.session_state["num_units"]):
        _cfg_parts += [
            st.session_state.get(f"u{_ci}_capacity_kw", 0),
            st.session_state.get(f"u{_ci}_setpoint", 0),
            st.session_state.get(f"u{_ci}_floor_area", 0),
        ]
    _config_hash = hash(tuple(_cfg_parts))

    if "_monthly_proj" in st.session_state:
        if st.session_state["_monthly_proj"].get("config_hash") != _config_hash:
            st.warning("⚠️ Configuration changed — recalculate to update projection.")

    # Always fetch historical means outside the button so caching works
    daily_means = get_historical_means(_proj_lat, _proj_lon)

    if st.button(f"📊 Calculate {_span_label} Projection"):
        st.session_state["_run_monthly_proj"] = True

    if st.session_state.get("_run_monthly_proj"):
        _n = st.session_state["num_units"]
        with st.spinner(f"Simulating {_total_days} days × {_n} units…"):
            _daily_records = []
            _fallback_temp = float(st.session_state["outdoor_temp"])

            for _y, _m in _proj_months:
                _days_in = calendar.monthrange(_y, _m)[1]
                for _day in range(1, _days_in + 1):
                    _mean_temp = daily_means.get((_m, _day), _fallback_temp)
                    _day_date = date(_y, _m, _day).isoformat()
                    _unit_energies = {}
                    for _ui in range(_n):
                        _r = RoomParams(
                            floor_area_m2=float(st.session_state[f"u{_ui}_floor_area"]) * FT2_TO_M2,
                            ceiling_height_m=float(st.session_state[f"u{_ui}_height"]) * FT_TO_M,
                            insulation_r=INSULATION_MAP[st.session_state[f"u{_ui}_insulation"]],
                        )
                        _init_temp = estimate_initial_temp(
                            outdoor_temp_c=_mean_temp,
                            hour=6.0,
                            r_value=INSULATION_MAP[st.session_state[f"u{_ui}_insulation"]],
                            floor_area_m2=float(st.session_state[f"u{_ui}_floor_area"]) * FT2_TO_M2,
                            ceiling_height_m=float(st.session_state[f"u{_ui}_height"]) * FT_TO_M,
                            diurnal_range_c=float(st.session_state.get("diurnal_range", 8.0)),
                        )
                        _h = HVACParams(
                            capacity_kw=float(st.session_state[f"u{_ui}_capacity_kw"]),
                            cop_rated=float(st.session_state[f"u{_ui}_cop_rated"]),
                            min_ratio=0.20, kp=0.5,
                        )
                        _s = SimParams(
                            duration_hours=24,
                            initial_temp_c=_init_temp,
                            outdoor_temp_c=_mean_temp,
                            setpoint_c=float(st.session_state[f"u{_ui}_setpoint"]),
                        )
                        _mdl = HVACModel(_r, _h, _s)
                        _history = _mdl.simulate()
                        _unit_energies[st.session_state[f"u{_ui}_room_name"]] = _history[-1].energy_kwh

                    _total_energy = sum(_unit_energies.values())
                    _rate = float(st.session_state["electricity_rate"])
                    _daily_records.append({
                        "date": _day_date,
                        "month": date(_y, _m, 1).strftime("%B %Y"),
                        "mean_temp": _mean_temp,
                        "unit_energies": _unit_energies,
                        "total_energy": _total_energy,
                        "cost": _total_energy * _rate,
                    })

            st.session_state["_monthly_proj"] = {
                "span_label": _span_label,
                "months": _proj_months,
                "daily": _daily_records,
                "config_hash": _config_hash,
            }
            st.session_state["_run_monthly_proj"] = False

    if "_monthly_proj" in st.session_state and st.session_state["_monthly_proj"].get("config_hash") == _config_hash:
        _proj = st.session_state["_monthly_proj"]
        _daily = _proj["daily"]
        _rate = float(st.session_state["electricity_rate"])
        _unit_colors = ["#f87171", "#60a5fa", "#34d399", "#fb923c",
                        "#a78bfa", "#f472b6", "#facc15", "#38bdf8"]

        # ── Per-month summary metrics ──────────────────────────────────────
        _month_groups = {}
        for _d in _daily:
            _month_groups.setdefault(_d["month"], []).append(_d)

        _mcols = st.columns(len(_month_groups))
        for _mi, (_mname, _mdays) in enumerate(_month_groups.items()):
            _mkwh  = sum(d["total_energy"] for d in _mdays)
            _mcost = sum(d["cost"]         for d in _mdays)
            _mtemp = sum(d["mean_temp"]    for d in _mdays) / len(_mdays)
            with _mcols[_mi]:
                st.markdown(f"**{_mname}**")
                st.metric("Energy", f"{_mkwh:.1f} kWh")
                st.metric("Cost",   f"₹{_mcost:,.0f}")
                st.metric("Avg Temp", f"{_mtemp:.1f} °C")

        st.divider()

        # ── 3-month totals ─────────────────────────────────────────────────
        _total_kwh  = sum(d["total_energy"] for d in _daily)
        _total_cost = sum(d["cost"]         for d in _daily)
        _avg_temp   = sum(d["mean_temp"]    for d in _daily) / len(_daily)
        _t1, _t2, _t3 = st.columns(3)
        _t1.metric("3-Month Total Energy", f"{_total_kwh:.1f} kWh")
        _t2.metric("3-Month Estimated Cost", f"₹{_total_cost:,.0f}")
        _t3.metric("Overall Avg Temp", f"{_avg_temp:.1f} °C")

        # ── Charts ─────────────────────────────────────────────────────────
        _dates    = [d["date"]         for d in _daily]
        _energies = [d["total_energy"] for d in _daily]
        _temps    = [d["mean_temp"]    for d in _daily]
        _unit_names = list(_daily[0]["unit_energies"].keys())
        _unit_3m_totals = {
            name: sum(d["unit_energies"][name] for d in _daily)
            for name in _unit_names
        }

        _left_col, _right_col = st.columns([3, 2])

        with _left_col:
            _fig_daily = make_subplots(specs=[[{"secondary_y": True}]])
            _fig_daily.add_trace(
                go.Bar(
                    x=_dates, y=_energies,
                    name="Daily Energy (kWh)",
                    marker=dict(
                        color=_temps, colorscale="RdYlBu_r", showscale=True,
                        colorbar=dict(title="°C", thickness=12, len=0.7),
                    ),
                    hovertemplate="%{x}<br>Energy: %{y:.2f} kWh<extra></extra>",
                ),
                secondary_y=False,
            )
            _fig_daily.add_trace(
                go.Scatter(
                    x=_dates, y=_temps,
                    name="Mean Temp (°C)",
                    line=dict(color="orange", width=2, dash="dash"),
                    hovertemplate="%{x}<br>Temp: %{y:.1f} °C<extra></extra>",
                ),
                secondary_y=True,
            )
            _fig_daily.update_layout(
                title=f"Daily Energy — {_proj['span_label']}",
                template="plotly_dark", height=340,
                paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
                margin=dict(t=50, b=40, l=60, r=60),
                legend=dict(orientation="h", y=1.12),
            )
            _fig_daily.update_yaxes(title_text="kWh", secondary_y=False)
            _fig_daily.update_yaxes(title_text="°C",  secondary_y=True)
            st.plotly_chart(_fig_daily, width='stretch', key="proj_daily_chart")

        with _right_col:
            _fig_units = go.Figure(go.Bar(
                x=list(_unit_3m_totals.values()),
                y=list(_unit_3m_totals.keys()),
                orientation="h",
                marker_color=[_unit_colors[_i % len(_unit_colors)] for _i in range(len(_unit_names))],
                text=[f"{v:.1f} kWh" for v in _unit_3m_totals.values()],
                textposition="outside",
                hovertemplate="%{y}<br>%{x:.2f} kWh<extra></extra>",
            ))
            _fig_units.update_layout(
                title="3-Month Total per Unit",
                template="plotly_dark", height=340,
                paper_bgcolor="#0f172a", plot_bgcolor="#0f172a",
                margin=dict(t=50, b=40, l=10, r=80),
                xaxis_title="kWh",
            )
            st.plotly_chart(_fig_units, width='stretch', key="proj_unit_chart")

        st.caption(
            f"Based on 10-year daily mean temperatures from Open-Meteo · "
            f"₹{_rate:.2f}/kWh · {_total_days} days simulated"
        )

        # ── Per-room recommendations ────────────────────────────────────────
        st.divider()
        st.markdown("## 💡 Energy Optimisation Recommendations")
        st.caption(
            "Per-room analysis across the 3-month projection. "
            "Savings estimates use physics-based deltas: ~6 % per +1 °C setpoint, "
            "~3–4 % per R-value improvement, ~15 % for pre-cooling strategy."
        )

        _n_units = st.session_state["num_units"]
        for _ri in range(_n_units):
            _rname    = st.session_state.get(f"u{_ri}_room_name", f"Unit {_ri+1}")
            _setpt    = float(st.session_state.get(f"u{_ri}_setpoint",   24.0))
            _cap_kw   = float(st.session_state.get(f"u{_ri}_capacity_kw", 3.517))
            _area_ft  = float(st.session_state.get(f"u{_ri}_floor_area",  160.0))
            _ins_key  = st.session_state.get(f"u{_ri}_insulation", list(WALL_LOAD_FACTORS.keys())[0])
            _r_val    = INSULATION_MAP.get(_ins_key, 0.15)
            _model    = st.session_state.get(f"u{_ri}_model", DEFAULT_UNIT["model"])

            # Gather this room's daily energy across all 3 months
            _room_days_by_month = {}   # month_label -> list of (date, energy, mean_temp)
            for _d in _daily:
                if _rname not in _d["unit_energies"]:
                    continue
                _room_days_by_month.setdefault(_d["month"], []).append(
                    (_d["date"], _d["unit_energies"][_rname], _d["mean_temp"])
                )

            if not _room_days_by_month:
                continue

            # Aggregate per month
            _monthly_stats = {}  # month -> {kwh, cost, avg_temp, max_temp, days}
            for _mname, _mlist in _room_days_by_month.items():
                _m_kwh  = sum(e for _, e, _ in _mlist)
                _m_tmps = [t for _, _, t in _mlist]
                _monthly_stats[_mname] = {
                    "kwh":      _m_kwh,
                    "cost":     _m_kwh * _rate,
                    "avg_temp": sum(_m_tmps) / len(_m_tmps),
                    "max_temp": max(_m_tmps),
                    "min_temp": min(_m_tmps),
                    "days":     len(_mlist),
                }

            _room_total_kwh  = sum(v["kwh"]  for v in _monthly_stats.values())
            _room_total_cost = sum(v["cost"] for v in _monthly_stats.values())

            with st.expander(f"🏠 {_rname}", expanded=True):
                # --- Headline numbers
                _h1, _h2 = st.columns(2)
                _h1.metric("3-Month Energy", f"{_room_total_kwh:.1f} kWh")
                _h2.metric("3-Month Cost",   f"₹{_room_total_cost:,.0f}")

                st.markdown("---")
                recs = []   # list of (priority, icon, title, detail)

                # ── Recommendation 1: Setpoint optimisation ──────────────────
                _saving_1c = _room_total_kwh * 0.06
                _saving_2c = _room_total_kwh * 0.12
                recs.append((
                    1, "🌡️", "Setpoint optimisation",
                    f"Raising setpoint from **{_setpt:.0f} °C → {_setpt+1:.0f} °C** saves "
                    f"≈ **{_saving_1c:.1f} kWh (₹{_saving_1c*_rate:,.0f})** over 3 months. "
                    f"At **{_setpt+2:.0f} °C** savings grow to ≈ **{_saving_2c:.1f} kWh "
                    f"(₹{_saving_2c*_rate:,.0f})**. "
                    "Each degree warmer reduces compressor runtime and keeps the inverter "
                    "in its efficient partial-load range longer.",
                ))

                # ── Recommendation 2: Pre-cooling on peak-temp days ───────────
                _hottest_month = max(_monthly_stats, key=lambda m: _monthly_stats[m]["avg_temp"])
                _hot_avg = _monthly_stats[_hottest_month]["avg_temp"]
                _hot_max = _monthly_stats[_hottest_month]["max_temp"]
                if _hot_avg > 30:
                    _precool_save = _room_total_kwh * 0.15
                    recs.append((
                        2, "⏰", f"Pre-cooling strategy for {_hottest_month}",
                        f"Average outdoor temp is **{_hot_avg:.1f} °C** (peak **{_hot_max:.1f} °C**) "
                        f"in {_hottest_month}. Starting the AC **30–45 min before occupancy** "
                        "and pre-cooling to setpoint while the building mass is still cooler "
                        f"can save ≈ **{_precool_save:.1f} kWh (₹{_precool_save*_rate:,.0f})** "
                        "by reducing peak-load compressor cycling.",
                    ))

                # ── Recommendation 3: Natural ventilation months ─────────────
                _cool_months = [m for m, s in _monthly_stats.items() if s["avg_temp"] < _setpt + 4]
                for _cm in _cool_months:
                    _cm_kwh  = _monthly_stats[_cm]["kwh"]
                    _cm_avg  = _monthly_stats[_cm]["avg_temp"]
                    _cm_min  = _monthly_stats[_cm]["min_temp"]
                    recs.append((
                        3, "🌬️", f"Natural ventilation opportunity — {_cm}",
                        f"Mean outdoor temp in {_cm} is **{_cm_avg:.1f} °C** (lows to "
                        f"**{_cm_min:.1f} °C**) — close to your setpoint of {_setpt:.0f} °C. "
                        "Consider switching off AC during early-morning (5–9 AM) and evening "
                        "(7–10 PM) hours and using ceiling fans instead. Estimated saving: "
                        f"≈ **{_cm_kwh * 0.20:.1f} kWh (₹{_cm_kwh * 0.20 * _rate:,.0f})** for the month.",
                    ))

                # ── Recommendation 4: Insulation upgrade ─────────────────────
                if _r_val < 0.5:
                    _ins_save = _room_total_kwh * 0.08
                    _better_wall = "cavity brick wall (R ≈ 0.5)" if _r_val < 0.35 else "AAC block wall (R ≈ 0.6)"
                    recs.append((
                        4, "🧱", "Wall insulation improvement",
                        f"Current wall type **{_ins_key}** (R = {_r_val} m²K/W) has poor insulation. "
                        f"Upgrading to a **{_better_wall}** or applying **2-inch EPS foam** to exterior "
                        f"walls could reduce heat gain by ~20 %, saving ≈ **{_ins_save:.1f} kWh "
                        f"(₹{_ins_save*_rate:,.0f})** over 3 months.",
                    ))

                # ── Recommendation 5: WindFree / Fan mode at night ───────────
                if "WindFree" in _model or "windfree" in _model.lower():
                    recs.append((
                        5, "💤", "Overnight WindFree mode",
                        f"The **{_model.split('  ')[0]}** supports WindFree mode (≤ 20 % capacity, "
                        "silent airflow). Setting a **1–2 °C night setback** (e.g., 26 °C overnight "
                        "vs 24 °C during the day) keeps the unit in WindFree range the entire night, "
                        "cutting overnight energy use by ~30–40 % with no comfort loss while sleeping.",
                    ))

                # ── Recommendation 6: Tonnage adequacy reminder ───────────────
                _vol_ft3 = _area_ft * float(st.session_state.get(f"u{_ri}_height", 9.0))
                _rec_ton = recommend_tonnage(_vol_ft3, _ins_key)
                _cur_ton = round(_cap_kw / 3.517, 2)
                if _cur_ton > _rec_ton * 1.3:
                    recs.append((
                        2, "📐", "AC appears oversized",
                        f"Required tonnage for this zone: **{_rec_ton:.1f}T** · Installed: **{_cur_ton:.1f}T**. "
                        "An oversized inverter AC short-cycles — compresses room humidity poorly and never "
                        "reaches its efficient partial-load COP region. "
                        f"Consider replacing with a **{_rec_ton:.1f}T** unit for better efficiency.",
                    ))
                elif _cur_ton < _rec_ton * 0.85:
                    recs.append((
                        1, "📐", "AC may be undersized",
                        f"Required tonnage: **{_rec_ton:.1f}T** · Installed: **{_cur_ton:.1f}T**. "
                        "Running at near-100 % capacity constantly means the inverter never enters "
                        "its part-load efficiency sweet-spot. Consider adding a unit or upgrading tonnage.",
                    ))

                # ── Recommendation 7: Outdated model / replacement ROI ────────
                _cur_cop = float(st.session_state.get(f"u{_ri}_cop_rated", 4.0))
                # Find nearest tonnage bucket in MARKET_BENCHMARKS
                _ton_buckets = sorted(MARKET_BENCHMARKS.keys())
                _nearest_ton = min(_ton_buckets, key=lambda t: abs(t - _cur_ton))
                _bmark = MARKET_BENCHMARKS.get(_nearest_ton)
                if _bmark and _cur_cop < _bmark["cop_best"] * _OUTDATED_COP_THRESHOLD:
                    _bmark_cop    = _bmark["cop_best"]
                    _bmark_model  = _bmark["model"]
                    _bmark_price  = _bmark["price_inr"]

                    # Energy savings: new unit delivers same cooling load with less electricity
                    # Electrical saving ratio = 1 − (cur_cop / bmark_cop)
                    _saving_ratio  = 1.0 - (_cur_cop / _bmark_cop)
                    _save_3m_kwh   = _room_total_kwh * _saving_ratio
                    _save_3m_inr   = _save_3m_kwh * _rate

                    # Extrapolate to full year: summer months (Apr-Jun) are heaviest;
                    # assume they represent ~55 % of annual AC load → annual ≈ 3m / 0.55
                    _save_yr_kwh   = _save_3m_kwh / 0.55
                    _save_yr_inr   = _save_yr_kwh * _rate

                    # Payback period (simple, no discounting)
                    _payback_yrs   = _bmark_price / _save_yr_inr if _save_yr_inr > 0 else float("inf")
                    _payback_str   = (
                        f"{_payback_yrs:.1f} years"
                        if _payback_yrs < 15
                        else "15+ years (marginal ROI)"
                    )

                    # 5-year net savings (after recouping cost)
                    _net_5yr       = _save_yr_inr * 5 - _bmark_price

                    recs.append((
                        1, "🔄", "Outdated model — replacement recommended",
                        f"Current COP **{_cur_cop:.1f}** is "
                        f"**{(1 - _cur_cop/_bmark_cop)*100:.0f}% below** the best available "
                        f"{_nearest_ton:.1f}T model on the Indian market.\n\n"
                        f"**Recommended replacement:** {_bmark_model}\n\n"
                        f"| Metric | Value |\n"
                        f"|---|---|\n"
                        f"| Current COP | {_cur_cop:.1f} |\n"
                        f"| New model COP | {_bmark_cop:.1f} |\n"
                        f"| 3-month energy saving | {_save_3m_kwh:.1f} kWh  ·  ₹{_save_3m_inr:,.0f} |\n"
                        f"| Estimated annual saving | {_save_yr_kwh:.1f} kWh  ·  **₹{_save_yr_inr:,.0f}** |\n"
                        f"| Replacement cost (incl. install) | ₹{_bmark_price:,} |\n"
                        f"| **Simple payback period** | **{_payback_str}** |\n"
                        f"| Net savings over 5 years | "
                        f"{'**₹{:,.0f} profit**'.format(_net_5yr) if _net_5yr > 0 else '₹{:,.0f} shortfall'.format(abs(_net_5yr))} |\n\n"
                        f"5-star BEE-rated inverter ACs also qualify for utility rebates and "
                        f"attract lower EMI rates under some green-loan schemes.",
                    ))

                # Render sorted by priority
                recs.sort(key=lambda x: x[0])
                for _pri, _icon, _title, _detail in recs:
                    st.markdown(f"**{_icon} {_title}**")
                    st.markdown(_detail)
                    st.markdown("")

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.divider()
st.caption(
    "Home AC Digital Twin · Physics: Newton's law of cooling · "
    "Inverter compressor: proportional modulation + part-load COP boost · "
    "Indian wall types · State DISCOM tariffs · Built with Streamlit + Plotly"
)
