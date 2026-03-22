"""
Home AC Digital Twin — Inverter Compressor Physics Model

Models a variable-speed inverter AC (e.g. Samsung WindFree) using:
  - Proportional control: modulation ratio scales with temperature error
  - Part-load COP boost: inverter ACs are MORE efficient at lower loads
  - WindFree mode: ultra-low capacity when room is near setpoint

Physics:
  dT/dt = (Q_outdoor + Q_hvac) / C_thermal

  Q_outdoor  = UA × (T_outdoor − T_room)          [envelope heat flow]
  Q_hvac     = ±modulation_ratio × capacity_kw     [variable HVAC output]
  COP(ratio) = cop_rated × (1 + boost × (1 − ratio))  [higher at part-load]
"""

import math
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

@dataclass
class RoomParams:
    """Physical characteristics of the room."""
    floor_area_m2: float = 30.0
    ceiling_height_m: float = 2.8
    insulation_r: float = 3.0          # m²·K/W  (higher = better insulated)
    thermal_mass_multiplier: float = 3.0  # accounts for furniture/walls (1 = air only)


@dataclass
class HVACParams:
    """Inverter AC system characteristics (e.g. Samsung WindFree 1.5 ton)."""
    capacity_kw: float = 5.275         # rated cooling/heating capacity (1.5 ton)
    cop_rated: float = 4.0             # COP at full load
    cop_part_load_boost: float = 0.4   # extra COP fraction at minimum load
                                       # cop(min_ratio) = cop_rated × (1 + boost)
    min_ratio: float = 0.20            # minimum compressor modulation (20%)
    kp: float = 0.5                    # proportional gain: ratio per °C of error
    deadband: float = 0.3              # switch off below this negative error (°C)
    windfree_threshold_c: float = 0.5  # enter WindFree mode below this error (°C)


@dataclass
class SimParams:
    """Simulation control parameters."""
    dt_minutes: float = 1.0
    duration_hours: float = 4.0
    initial_temp_c: float = 28.0
    outdoor_temp_c: float = 35.0
    setpoint_c: float = 22.0


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class HVACState:
    """System snapshot at one timestep."""
    time_min: float = 0.0
    room_temp_c: float = 28.0
    modulation_ratio: float = 0.0      # 0.0 = off, 0.2–1.0 = inverter range
    mode: str = "off"                  # "off" | "cooling" | "windfree" | "heating"
    energy_kwh: float = 0.0
    q_hvac_kw: float = 0.0            # instantaneous HVAC heat flow
    q_outdoor_kw: float = 0.0         # instantaneous outdoor heat flow
    effective_cop: float = 0.0         # actual COP this timestep


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class HVACModel:
    """
    Inverter AC digital twin.

    Usage:
        model = HVACModel(room, hvac, sim)
        history = model.simulate()
    """

    def __init__(self, room: RoomParams, hvac: HVACParams, sim: SimParams):
        self.room = room
        self.hvac = hvac
        self.sim = sim
        self._ua = self._compute_ua()
        self._c_thermal = self._compute_thermal_mass()
        self._mode = "cooling" if sim.outdoor_temp_c >= sim.setpoint_c else "heating"

    # ------------------------------------------------------------------
    # Derived physical properties
    # ------------------------------------------------------------------

    def _compute_ua(self) -> float:
        """Overall heat transfer coefficient in kW/K."""
        side = math.sqrt(self.room.floor_area_m2)
        wall_area = 4 * side * self.room.ceiling_height_m
        ceiling_area = self.room.floor_area_m2
        return (wall_area + ceiling_area) / self.room.insulation_r / 1000  # kW/K

    def _compute_thermal_mass(self) -> float:
        """Room thermal mass in kJ/K."""
        volume = self.room.floor_area_m2 * self.room.ceiling_height_m
        return volume * 1.2 * 1.005 * self.room.thermal_mass_multiplier  # kJ/K

    # ------------------------------------------------------------------
    # Inverter control law
    # ------------------------------------------------------------------

    def _inverter_control(self, temp_error: float, current_ratio: float) -> tuple[float, str]:
        """
        Proportional inverter controller.

        temp_error = T_room − T_setpoint
          positive → room is warmer than setpoint → need cooling
          negative → room is cooler than setpoint

        Returns (modulation_ratio, mode_str)
        """
        h = self.hvac

        if self._mode == "cooling":
            if temp_error < -h.deadband:
                # Room is cold enough — shut off
                return 0.0, "off"
            elif temp_error <= h.windfree_threshold_c:
                # Near setpoint — WindFree: minimum compressor speed
                return h.min_ratio, "windfree"
            else:
                # Proportional: scale capacity with error
                ratio = h.kp * temp_error
                ratio = max(h.min_ratio, min(1.0, ratio))
                return ratio, "cooling"

        else:  # heating
            if temp_error > h.deadband:
                return 0.0, "off"
            elif temp_error >= -h.windfree_threshold_c:
                return h.min_ratio, "windfree"
            else:
                ratio = h.kp * (-temp_error)
                ratio = max(h.min_ratio, min(1.0, ratio))
                return ratio, "heating"

    # ------------------------------------------------------------------
    # COP at partial load
    # ------------------------------------------------------------------

    def _cop_at_ratio(self, ratio: float) -> float:
        """
        Inverter ACs are more efficient at lower loads.
        Linear interpolation: cop(1.0) = cop_rated, cop(min) = cop_rated × (1 + boost)
        """
        if ratio <= 0:
            return 0.0
        h = self.hvac
        boost_factor = 1.0 + h.cop_part_load_boost * (1.0 - ratio)
        return h.cop_rated * boost_factor

    # ------------------------------------------------------------------
    # Single timestep
    # ------------------------------------------------------------------

    def _step(self, state: HVACState) -> HVACState:
        dt_s = self.sim.dt_minutes * 60.0
        dt_h = self.sim.dt_minutes / 60.0

        temp_error = state.room_temp_c - self.sim.setpoint_c
        ratio, mode = self._inverter_control(temp_error, state.modulation_ratio)

        # Heat flows (kW)
        q_outdoor = self._ua * (self.sim.outdoor_temp_c - state.room_temp_c)

        if mode == "cooling":
            q_hvac = -ratio * self.hvac.capacity_kw
        elif mode == "heating":
            q_hvac = +ratio * self.hvac.capacity_kw
        elif mode == "windfree":
            # WindFree: gentle airflow, minimal capacity
            sign = -1 if self._mode == "cooling" else +1
            q_hvac = sign * ratio * self.hvac.capacity_kw
        else:
            q_hvac = 0.0

        q_total = q_outdoor + q_hvac

        # Temperature change
        delta_t = (q_total * dt_s) / self._c_thermal
        new_temp = state.room_temp_c + delta_t

        # Energy: electrical input = thermal output / COP
        cop = self._cop_at_ratio(ratio) if ratio > 0 else 0.0
        power_kw = (ratio * self.hvac.capacity_kw / cop) if cop > 0 else 0.0
        new_energy = state.energy_kwh + power_kw * dt_h

        return HVACState(
            time_min=state.time_min + self.sim.dt_minutes,
            room_temp_c=new_temp,
            modulation_ratio=ratio,
            mode=mode,
            energy_kwh=new_energy,
            q_hvac_kw=q_hvac,
            q_outdoor_kw=q_outdoor,
            effective_cop=cop,
        )

    # ------------------------------------------------------------------
    # Full simulation
    # ------------------------------------------------------------------

    def simulate(self) -> list[HVACState]:
        n_steps = int(self.sim.duration_hours * 60 / self.sim.dt_minutes)
        state = HVACState(room_temp_c=self.sim.initial_temp_c)
        history = [state]
        for _ in range(n_steps):
            state = self._step(state)
            history.append(state)
        return history

    # ------------------------------------------------------------------
    # Properties for display
    # ------------------------------------------------------------------

    @property
    def ua_kw_per_k(self) -> float:
        return self._ua

    @property
    def thermal_mass_kj_per_k(self) -> float:
        return self._c_thermal

    @property
    def hvac_mode(self) -> str:
        return self._mode
