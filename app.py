"""
Home AC Digital Twin — Multi-Unit Streamlit Dashboard

Run with:
    streamlit run app.py
"""

import math
from datetime import datetime, timezone, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from hvac_model import HVACModel, HVACParams, RoomParams, SimParams
from weather import fetch_current_temp
from tariffs import tariff_for_state, extract_state_from_display_name


@st.cache_data(ttl=600)
def get_live_temp(city: str) -> dict:
    return fetch_current_temp(city)


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

DEFAULT_UNIT = {
    "room_name":    "Room",
    "model":        "Samsung WindFree 1.5T  (5.28 kW)",
    "capacity_kw":  5.275,
    "cop_rated":    4.0,
    "floor_area":   320,   # sq ft  (~30 m²)
    "height":       9.0,   # ft     (~2.7 m)
    "insulation":   "230mm Red Brick + Plaster (both sides)",
    "setpoint":     24.0,
    "initial_temp": 30.0,
    "min_ratio":    20,
    "kp":           0.5,
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
    for field, val in DEFAULT_UNIT.items():
        key = f"u{i}_{field}"
        if key not in st.session_state:
            st.session_state[key] = (name if field == "room_name" else val)

for i in range(8):   # pre-init up to 8 units
    _init_unit(i)

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

    # Scenario buttons MUST come before any slider that shares a key —
    # Streamlit forbids writing session_state for a key after its widget is instantiated.
    st.divider()
    st.markdown("## 🎬 Quick Scenarios")
    scenarios = {
        "🌊 Heat Wave":    {"outdoor_temp": 45.0},
        "❄️ Cold Night":   {"outdoor_temp": 5.0},
        "🏠 Chennai Day":  {"outdoor_temp": 34.0},
        "🏜️ Extreme Heat": {"outdoor_temp": 48.0},
    }
    cols = st.columns(2)
    for i, (label, vals) in enumerate(scenarios.items()):
        if cols[i % 2].button(label, width='stretch'):
            for k, v in vals.items():
                st.session_state[k] = v
            st.rerun()

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

# Render unit config cards — 2 per row
for row_start in range(0, n, 2):
    cols = st.columns(2, gap="medium")
    for col_idx, i in enumerate(range(row_start, min(row_start + 2, n))):
        with cols[col_idx]:
            room_label = st.session_state[f"u{i}_room_name"]
            with st.expander(f"🏠 Unit {i+1} — {room_label}", expanded=True):

                c1, c2 = st.columns(2)
                with c1:
                    st.text_input("Room Name", key=f"u{i}_room_name")
                with c2:
                    st.selectbox(
                        "AC Model",
                        options=list(SAMSUNG_MODELS.keys()),
                        key=f"u{i}_model",
                    )

                # Auto-fill capacity/COP from model selection
                selected_model = st.session_state[f"u{i}_model"]
                preset = SAMSUNG_MODELS[selected_model]
                if selected_model != "Custom":
                    st.session_state[f"u{i}_capacity_kw"] = preset["capacity_kw"]
                    st.session_state[f"u{i}_cop_rated"] = preset["cop_rated"]

                c3, c4 = st.columns(2)
                with c3:
                    st.number_input(
                        "Floor Area (sq ft)", min_value=50, max_value=5000, step=10,
                        key=f"u{i}_floor_area",
                    )
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

                if selected_model == "Custom":
                    st.number_input(
                        "Capacity (kW)", min_value=1.0, max_value=20.0,
                        step=0.1, format="%.2f", key=f"u{i}_capacity_kw",
                    )
                else:
                    st.metric("Capacity", f"{preset['capacity_kw']:.3f} kW")

                if selected_model == "Custom":
                    st.number_input(
                        "COP (Efficiency)", min_value=1.5, max_value=7.0,
                        step=0.1, format="%.1f", key=f"u{i}_cop_rated",
                    )
                else:
                    st.caption(f"COP: {preset['cop_rated']}  ·  Min speed: 20%")

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

for tab, (label, df, mdl, sim, hvac, room) in zip(tabs[:-1], results):
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

        st.plotly_chart(build_unit_chart(df, sim, label), width='stretch')

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
    for label, df, mdl, sim, hvac, room in results:
        final = df.iloc[-1]
        at_sp = df[abs(df["room_temp"] - sim.setpoint_c) <= 0.3]
        t2sp = at_sp.iloc[0]["time_h"] * 60 if not at_sp.empty else None
        wf_pct = (df["mode"] == "windfree").mean() * 100
        cooling_kwh = abs(df["q_hvac_kw"]).sum() * (sim.dt_minutes / 60)
        eff_cop = cooling_kwh / final["energy_kwh"] if final["energy_kwh"] > 0 else 0
        rows.append({
            "Room":           label,
            "Model":          st.session_state[f"u{results.index((label, df, mdl, sim, hvac, room))}_model"].split("  ")[0],
            "Area (sq ft)":   round(room.floor_area_m2 / FT2_TO_M2),
            "Wall Type":      st.session_state[f"u{results.index((label, df, mdl, sim, hvac, room))}_insulation"].split(" + ")[0],
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
        st.plotly_chart(fig_e, width='stretch')

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
        st.plotly_chart(fig_c, width='stretch')

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
    st.plotly_chart(fig_all, width='stretch')

    total_energy = sum(df.iloc[-1]["energy_kwh"] for _, df, *_ in results)
    st.info(
        f"**Total fleet energy:** {total_energy:.3f} kWh  ·  "
        f"**Total cost:** ₹{total_energy * st.session_state['electricity_rate']:.2f}  ·  "
        f"**Outdoor temp:** {st.session_state['outdoor_temp']}°C  ·  "
        f"**Duration:** {st.session_state['duration_h']} h"
    )

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.divider()
st.caption(
    "Home AC Digital Twin · Physics: Newton's law of cooling · "
    "Inverter compressor: proportional modulation + part-load COP boost · "
    "Indian wall types · State DISCOM tariffs · Built with Streamlit + Plotly"
)
