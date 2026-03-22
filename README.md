# 🏠 Home AC Digital Twin

A physics-based simulator and energy optimiser for residential air conditioning in India. Configure multiple AC units room by room, pull live weather data, simulate inverter-compressor behaviour, and get data-driven recommendations — all in a Streamlit dashboard.

---

## Demo

![Dashboard](https://img.shields.io/badge/Built%20with-Streamlit-red?logo=streamlit)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Features

| Category | Capability |
|---|---|
| **Multi-room config** | Up to 8 AC units, auto-split floor area when multiple units serve the same room |
| **Physics engine** | Newton's law of cooling + inverter proportional control + part-load COP boost |
| **Samsung WindFree** | Silent WindFree mode (≤ 20 % compressor) triggers within 0.5 °C of setpoint |
| **Live weather** | Real-time outdoor temperature via yr.no (MET Norway); geocoded by Nominatim |
| **Indian wall types** | 9 construction types from bare RCC (R = 0.15) to brick + rockwool (R = 3.0) |
| **Tonnage advisor** | Volume-based recommendation per room, adjusted by wall heat-load factor |
| **Model search** | Live search over 30+ Indian market AC models (Samsung, LG, Daikin, Voltas, …) |
| **State tariffs** | DISCOM-specific electricity rates for 25 Indian states |
| **3-month projection** | Day-by-day simulation using 10-year Open-Meteo historical means |
| **Recommendations** | 7 actionable tips per room — setpoint, pre-cooling, ventilation, insulation, replacement ROI |

---

## Architecture

```
home-ac-digital-twin/
├── app.py            # Streamlit dashboard — UI, state management, orchestration
├── hvac_model.py     # Physics engine — inverter AC simulation
├── weather.py        # Live weather (yr.no) + 10-year historical means (Open-Meteo)
├── tariffs.py        # Indian state electricity tariffs (DISCOM rates)
├── model_search.py   # Local AC model database + optional DuckDuckGo search
└── requirements.txt
```

---

## Physics Model

The core simulation (`hvac_model.py`) runs at a 1-minute timestep using:

### Newton's Law of Cooling
```
dT/dt = (Q_outdoor + Q_hvac) / C_thermal
```

| Symbol | Description |
|---|---|
| `Q_outdoor` | `UA × (T_outdoor − T_room)` — heat leaking through walls/ceiling |
| `Q_hvac` | `±modulation_ratio × capacity_kw` — cooling / heating by the AC |
| `C_thermal` | `air_volume × density × Cp × thermal_mass_multiplier` |
| `UA` | Overall heat-transfer coefficient `(kW/K) = surface_area / R_value / 1000` |

### Inverter Compressor (Proportional Control)
```
modulation_ratio = Kp × temperature_error     (clamped to [20 %, 100 %])
```

- **WindFree mode** — error ≤ 0.5 °C → runs at minimum 20 % speed silently
- **Off** — error < deadband (0.3 °C on cooling)
- **Cooling / Heating** — proportional output otherwise

### Part-Load COP Boost
```
COP(ratio) = COP_rated × (1 + 0.4 × (1 − ratio))
```
At 20 % load the inverter is 40 % more efficient than at full speed — matching real-world inverter behaviour.

---

## Getting Started

### 1. Clone & set up environment

```bash
git clone https://github.com/pran-venukumar/home-ac-digital-twin.git
cd home-ac-digital-twin
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Run

```bash
streamlit run app.py
```

Open `http://localhost:8501` in your browser.

> **Note:** The app uses only free, no-key APIs (Nominatim, yr.no, Open-Meteo). No API keys needed.

---

## Usage

### Step 1 — Configure rooms

Set the number of AC units (1–8). For each unit:

- **Room name** — type freely (e.g., "Living Room", "Master Bedroom")
- **AC model** — search by brand or model number; capacity and COP auto-fill
- **Floor area & height** — in square feet / feet
- **Wall construction** — choose from 9 Indian residential types
- **Setpoint** — desired room temperature (°C)

When you add a second AC unit to the same room the floor area is automatically split equally. The system tells you the combined tonnage and whether you need more or fewer units.

### Step 2 — Fetch live weather (sidebar)

Type your city (e.g., "Chennai") and click **Fetch Live Temperature**. The app:
- Geocodes via Nominatim → lat / lon
- Pulls current temperature from yr.no
- Auto-detects your state and loads the correct DISCOM tariff

### Step 3 — Run simulation

Click **▶ Run Simulation**. Each unit is simulated for the configured duration (1–24 hours) at the outdoor temperature shown in the sidebar.

Results show:
- **Per-unit charts** — room temperature, compressor modulation %, energy accumulation
- **Fleet overview** — summary table, energy bar chart, COP comparison, temperature overlay
- **Time to setpoint** — how long until each room reaches its target

### Step 4 — 3-month energy projection

Click **📊 Calculate Projection** to simulate the next three calendar months using 10-year historical daily mean temperatures from Open-Meteo. Results include:
- Monthly energy (kWh) and estimated electricity cost
- Daily energy chart coloured by outdoor temperature
- Per-unit contribution breakdown

### Step 5 — Optimisation recommendations

After the projection runs, a **💡 Energy Optimisation Recommendations** section appears for each room with up to 7 prioritised suggestions.

---

## Recommendations Engine

| # | Recommendation | Trigger |
|---|---|---|
| 🌡️ Setpoint | Quantified savings for +1 °C and +2 °C raise | Always |
| ⏰ Pre-cooling | Start AC 30–45 min before occupancy | Avg outdoor temp > 30 °C |
| 🌬️ Natural ventilation | Replace AC with ceiling fans during mild hours | Month avg within 4 °C of setpoint |
| 🧱 Insulation upgrade | Upgrade wall type, estimated 8 % energy saving | R-value < 0.5 m²·K/W |
| 💤 WindFree night setback | 1–2 °C warmer setpoint overnight | Samsung WindFree model |
| 📐 Tonnage adequacy | Oversized (>130 %) or undersized (<85 %) | Volume vs installed capacity |
| 🔄 Replacement ROI | Full payback & 5-year net savings table | Current COP > 15 % below 5-star BEE best |

### Replacement ROI calculation

```
Energy saving ratio  = 1 − (current_COP / benchmark_COP)
3-month saving (kWh) = projection_kWh × saving_ratio
Annual saving (kWh)  = 3-month_saving / 0.55   # summer months ≈ 55 % of annual load
Payback period       = replacement_cost_₹ / (annual_saving_kWh × tariff_₹)
5-year net savings   = annual_saving_₹ × 5 − replacement_cost_₹
```

The benchmark table (`MARKET_BENCHMARKS`) lists the best 5-star BEE-rated inverter AC available in India for each tonnage category as of 2024–25, including approximate MRP with installation.

---

## AC Model Database

`model_search.py` ships with 33 pre-loaded models across 9 brands:

| Brand | Models included |
|---|---|
| Samsung | WindFree (1.0–3.0T) |
| LG | Dual Inverter, Artcool |
| Daikin | FTKF, MTKM series |
| Voltas | Inverter series |
| Hitachi | Kashikoi, RAS series |
| Panasonic | CS/CU-SU series |
| Blue Star | IC, BI series |
| Carrier | Cicero, Emperia |
| Whirlpool | 1.5T Inverter |

Search is case-insensitive substring matching on brand, model code, or tonnage.

---

## Wall Construction Types

| Type | R-value (m²·K/W) | Typical use |
|---|---|---|
| 150 mm RCC / Bare Concrete | 0.15 | Structural slabs |
| 230 mm Brick (unplastered) | 0.30 | Old construction |
| 230 mm Brick (plastered) | 0.40 | Standard residential |
| Cavity Brick | 0.50 | Mid-range apartments |
| AAC Block | 0.60 | Modern construction |
| Hollow Concrete Block | 0.45 | Commercial / newer residential |
| 230 mm Brick + 25 mm EPS | 1.20 | Insulated residential |
| 230 mm Brick + 50 mm EPS | 1.80 | High-performance |
| Brick + Rockwool | 3.00 | Best available |

---

## Indian State Tariffs

25 states covered, e.g.:

| State | Rate (₹/kWh) | DISCOM |
|---|---|---|
| Tamil Nadu | 5.50 | TANGEDCO |
| Karnataka | 6.00 | BESCOM |
| Maharashtra | 8.00 | MSEDCL |
| Delhi | 7.00 | BSES / TPDDL |
| Goa | 3.50 | GPDCL |
| Rajasthan | 7.50 | JVVNL |

Rates are blended averages for 201–500 units/month residential consumption. Auto-detected from the Nominatim reverse-geocode result when live weather is fetched.

---

## Key Constants

| Constant | Value | Meaning |
|---|---|---|
| `FT2_TO_M2` | 0.0929 | sq ft → m² |
| `FT_TO_M` | 0.3048 | ft → m |
| `min_ratio` | 0.20 | Minimum inverter speed |
| `cop_part_load_boost` | 0.40 | COP uplift at minimum load |
| `windfree_threshold_c` | 0.5 | °C error for WindFree mode |
| `kp` | 0.5 | Proportional gain (°C⁻¹) |
| `deadband` | 0.3 | °C hysteresis |
| `dt_minutes` | 1.0 | Simulation timestep |
| `_OUTDATED_COP_THRESHOLD` | 0.85 | Flag if COP < 85 % of best available |
| Annual load assumption | 55 % | Summer 3-month share of annual AC use |

---

## APIs Used

| API | Purpose | Key required |
|---|---|---|
| [Nominatim](https://nominatim.org/) | City geocoding | No |
| [yr.no (MET Norway)](https://api.met.no/) | Live weather | No |
| [Open-Meteo Archive](https://open-meteo.com/) | 10-year historical temperatures | No |
| [DuckDuckGo Instant Answers](https://duckduckgo.com/api) | Optional online model search | No |

---

## Requirements

```
streamlit>=1.35.0
plotly>=5.20.0
pandas>=2.0.0
requests>=2.31.0
```

Python 3.10 or newer recommended.

---

## Project Background

Built as a team demonstration of the **digital twin** concept applied to a familiar physical system — residential AC. The goal: make physics-based simulation accessible enough that anyone can explore "what-if" scenarios (different wall types, setpoints, models) and see real energy and cost impacts without running hardware experiments.

The model deliberately uses only free APIs and standard Python libraries so it can be run by anyone on any machine.

---

## Contributing

Pull requests are welcome. Key areas for contribution:
- Additional AC brands / models in `model_search.py`
- Multi-zone heat transfer (shared walls between rooms)
- Solar irradiance component (east/west/south-facing walls)
- Occupancy schedules (AC on/off based on time of day)
- Export to PDF / Excel

---

## License

MIT © 2025 Pran Venukumar
