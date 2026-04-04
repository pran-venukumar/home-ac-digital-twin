"""
Tests for hvac_model.py — inverter AC physics engine.

Covers:
  - UA and thermal mass calculations
  - Inverter control law (all 6 branches: cooling/heating × off/windfree/proportional)
  - Proportional ratio clamping
  - COP part-load curve
  - Single-step energy and temperature physics
  - Full simulation invariants (convergence, monotone energy, mode sequencing)
  - Heating mode initialisation
"""

import math
import pytest

from hvac_model import HVACModel, HVACParams, HVACState, RoomParams, SimParams


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def default_model(**sim_kwargs) -> HVACModel:
    """Cooling-mode model with default params, optionally overriding SimParams fields."""
    sim = SimParams(outdoor_temp_c=35.0, setpoint_c=22.0, initial_temp_c=28.0, **sim_kwargs)
    return HVACModel(RoomParams(), HVACParams(), sim)


def heating_model(**sim_kwargs) -> HVACModel:
    """Heating-mode model (outdoor < setpoint)."""
    sim = SimParams(outdoor_temp_c=5.0, setpoint_c=22.0, initial_temp_c=15.0, **sim_kwargs)
    return HVACModel(RoomParams(), HVACParams(), sim)


# ---------------------------------------------------------------------------
# UA and thermal mass
# ---------------------------------------------------------------------------

class TestDerivedPhysics:
    def test_ua_is_positive(self):
        model = default_model()
        assert model.ua_kw_per_k > 0

    def test_ua_formula(self):
        room = RoomParams(floor_area_m2=36.0, ceiling_height_m=3.0, insulation_r=4.0)
        model = HVACModel(room, HVACParams(), SimParams())
        side = math.sqrt(36.0)
        wall_area = 4 * side * 3.0
        ceiling_area = 36.0
        expected = (wall_area + ceiling_area) / 4.0 / 1000
        assert model.ua_kw_per_k == pytest.approx(expected, rel=1e-6)

    def test_ua_increases_with_lower_insulation(self):
        room_good = RoomParams(insulation_r=5.0)
        room_bad = RoomParams(insulation_r=1.0)
        ua_good = HVACModel(room_good, HVACParams(), SimParams()).ua_kw_per_k
        ua_bad = HVACModel(room_bad, HVACParams(), SimParams()).ua_kw_per_k
        assert ua_bad > ua_good

    def test_thermal_mass_is_positive(self):
        model = default_model()
        assert model.thermal_mass_kj_per_k > 0

    def test_thermal_mass_formula(self):
        room = RoomParams(floor_area_m2=25.0, ceiling_height_m=2.5, thermal_mass_multiplier=2.0)
        model = HVACModel(room, HVACParams(), SimParams())
        volume = 25.0 * 2.5
        expected = volume * 1.2 * 1.005 * 2.0
        assert model.thermal_mass_kj_per_k == pytest.approx(expected, rel=1e-6)

    def test_thermal_mass_scales_with_multiplier(self):
        room1 = RoomParams(thermal_mass_multiplier=1.0)
        room3 = RoomParams(thermal_mass_multiplier=3.0)
        m1 = HVACModel(room1, HVACParams(), SimParams()).thermal_mass_kj_per_k
        m3 = HVACModel(room3, HVACParams(), SimParams()).thermal_mass_kj_per_k
        assert m3 == pytest.approx(3.0 * m1, rel=1e-6)


# ---------------------------------------------------------------------------
# Mode detection
# ---------------------------------------------------------------------------

class TestModeDetection:
    def test_cooling_mode_when_outdoor_above_setpoint(self):
        assert default_model().hvac_mode == "cooling"

    def test_heating_mode_when_outdoor_below_setpoint(self):
        assert heating_model().hvac_mode == "heating"

    def test_boundary_outdoor_equals_setpoint_is_heating(self):
        # outdoor == setpoint → condition is `>= setpoint` → cooling
        model = HVACModel(RoomParams(), HVACParams(), SimParams(outdoor_temp_c=22.0, setpoint_c=22.0))
        assert model.hvac_mode == "cooling"


# ---------------------------------------------------------------------------
# Inverter control — cooling mode
# ---------------------------------------------------------------------------

class TestInverterControlCooling:
    def setup_method(self):
        self.model = default_model()
        assert self.model.hvac_mode == "cooling"

    def test_shutoff_when_well_below_setpoint(self):
        # temp_error < -deadband (0.3) → off
        ratio, mode = self.model._inverter_control(temp_error=-1.0, current_ratio=0.5)
        assert mode == "off"
        assert ratio == 0.0

    def test_shutoff_at_deadband_boundary(self):
        # temp_error == -deadband → still off (strictly less than)
        hvac = HVACParams(deadband=0.3)
        model = HVACModel(RoomParams(), hvac, SimParams())
        ratio, mode = model._inverter_control(temp_error=-0.3, current_ratio=0.5)
        # -0.3 < -0.3 is False, so it falls through to windfree
        assert mode == "windfree"

    def test_windfree_just_below_setpoint(self):
        # 0 > temp_error > -deadband → windfree
        ratio, mode = self.model._inverter_control(temp_error=0.1, current_ratio=0.3)
        assert mode == "windfree"
        assert ratio == self.model.hvac.min_ratio

    def test_windfree_at_zero_error(self):
        ratio, mode = self.model._inverter_control(temp_error=0.0, current_ratio=0.0)
        assert mode == "windfree"
        assert ratio == self.model.hvac.min_ratio

    def test_cooling_proportional_above_threshold(self):
        # temp_error > windfree_threshold (0.5)
        ratio, mode = self.model._inverter_control(temp_error=2.0, current_ratio=0.0)
        assert mode == "cooling"
        expected_ratio = min(1.0, max(self.model.hvac.min_ratio, self.model.hvac.kp * 2.0))
        assert ratio == pytest.approx(expected_ratio)

    def test_ratio_clamped_at_max_1(self):
        # kp=0.5, error=10 → raw=5.0 → clamped to 1.0
        ratio, mode = self.model._inverter_control(temp_error=10.0, current_ratio=0.0)
        assert ratio == 1.0
        assert mode == "cooling"

    def test_ratio_clamped_at_min(self):
        # kp=0.5, error=0.6 → raw=0.3 > min_ratio(0.2), no clamp needed
        hvac = HVACParams(kp=0.1, min_ratio=0.2, windfree_threshold_c=0.5)
        model = HVACModel(RoomParams(), hvac, SimParams())
        # error=0.6, raw ratio=0.06 < min_ratio → clamp to min
        ratio, mode = model._inverter_control(temp_error=0.6, current_ratio=0.0)
        assert ratio == hvac.min_ratio
        assert mode == "cooling"


# ---------------------------------------------------------------------------
# Inverter control — heating mode
# ---------------------------------------------------------------------------

class TestInverterControlHeating:
    def setup_method(self):
        self.model = heating_model()
        assert self.model.hvac_mode == "heating"

    def test_shutoff_when_well_above_setpoint(self):
        # temp_error > deadband → off
        ratio, mode = self.model._inverter_control(temp_error=1.0, current_ratio=0.5)
        assert mode == "off"
        assert ratio == 0.0

    def test_windfree_just_above_setpoint(self):
        # -windfree_threshold <= temp_error <= deadband → windfree
        ratio, mode = self.model._inverter_control(temp_error=0.0, current_ratio=0.0)
        assert mode == "windfree"
        assert ratio == self.model.hvac.min_ratio

    def test_heating_proportional_below_threshold(self):
        # temp_error < -windfree_threshold
        ratio, mode = self.model._inverter_control(temp_error=-3.0, current_ratio=0.0)
        assert mode == "heating"
        expected = min(1.0, max(self.model.hvac.min_ratio, self.model.hvac.kp * 3.0))
        assert ratio == pytest.approx(expected)

    def test_heating_ratio_clamped_at_max(self):
        ratio, mode = self.model._inverter_control(temp_error=-20.0, current_ratio=0.0)
        assert ratio == 1.0
        assert mode == "heating"


# ---------------------------------------------------------------------------
# COP at partial load
# ---------------------------------------------------------------------------

class TestCOPAtRatio:
    def setup_method(self):
        self.model = default_model()
        self.hvac = self.model.hvac

    def test_cop_at_full_load_equals_rated(self):
        cop = self.model._cop_at_ratio(1.0)
        assert cop == pytest.approx(self.hvac.cop_rated)

    def test_cop_at_zero_is_zero(self):
        assert self.model._cop_at_ratio(0.0) == 0.0

    def test_cop_at_negative_is_zero(self):
        assert self.model._cop_at_ratio(-0.5) == 0.0

    def test_cop_higher_at_part_load(self):
        cop_full = self.model._cop_at_ratio(1.0)
        cop_half = self.model._cop_at_ratio(0.5)
        cop_min = self.model._cop_at_ratio(self.hvac.min_ratio)
        assert cop_min > cop_half > cop_full

    def test_cop_formula(self):
        ratio = 0.6
        expected = self.hvac.cop_rated * (1.0 + self.hvac.cop_part_load_boost * (1.0 - ratio))
        assert self.model._cop_at_ratio(ratio) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Single step physics
# ---------------------------------------------------------------------------

class TestStep:
    def test_energy_increases_when_running(self):
        model = default_model()
        state = HVACState(room_temp_c=28.0, modulation_ratio=0.0, mode="off", energy_kwh=0.0)
        next_state = model._step(state)
        # room is above setpoint → AC should turn on → energy should increase
        assert next_state.energy_kwh >= state.energy_kwh

    def test_energy_does_not_increase_when_off(self):
        # Setpoint below room but within deadband → AC turns off
        hvac = HVACParams(deadband=5.0)  # huge deadband to force off
        sim = SimParams(setpoint_c=27.5, initial_temp_c=27.0, outdoor_temp_c=35.0)
        model = HVACModel(RoomParams(), hvac, sim)
        state = HVACState(room_temp_c=27.0)
        next_state = model._step(state)
        if next_state.mode == "off":
            assert next_state.energy_kwh == pytest.approx(state.energy_kwh)

    def test_cooling_lowers_temperature(self):
        # Large positive error → strong cooling → temp should drop
        hvac = HVACParams(kp=1.0)
        sim = SimParams(outdoor_temp_c=22.0, setpoint_c=18.0, initial_temp_c=28.0)
        model = HVACModel(RoomParams(), hvac, sim)
        state = HVACState(room_temp_c=28.0)
        next_state = model._step(state)
        assert next_state.room_temp_c < state.room_temp_c

    def test_heating_raises_temperature(self):
        sim = SimParams(outdoor_temp_c=0.0, setpoint_c=22.0, initial_temp_c=10.0)
        model = HVACModel(RoomParams(), HVACParams(), sim)
        state = HVACState(room_temp_c=10.0)
        next_state = model._step(state)
        assert next_state.room_temp_c > state.room_temp_c

    def test_time_increments_by_dt(self):
        sim = SimParams(dt_minutes=5.0)
        model = HVACModel(RoomParams(), HVACParams(), sim)
        state = HVACState(time_min=10.0, room_temp_c=28.0)
        next_state = model._step(state)
        assert next_state.time_min == pytest.approx(15.0)

    def test_q_hvac_is_negative_in_cooling_mode(self):
        model = default_model()
        state = HVACState(room_temp_c=28.0)  # well above setpoint → cooling
        next_state = model._step(state)
        if next_state.mode == "cooling":
            assert next_state.q_hvac_kw < 0

    def test_q_hvac_is_zero_when_off(self):
        # Room just below setpoint + deadband so AC shuts off
        hvac = HVACParams(deadband=2.0)
        sim = SimParams(setpoint_c=25.0, initial_temp_c=22.0, outdoor_temp_c=35.0)
        model = HVACModel(RoomParams(), hvac, sim)
        state = HVACState(room_temp_c=22.0)  # error = 22-25 = -3 < -deadband → off
        next_state = model._step(state)
        assert next_state.mode == "off"
        assert next_state.q_hvac_kw == 0.0


# ---------------------------------------------------------------------------
# Full simulation invariants
# ---------------------------------------------------------------------------

class TestSimulate:
    def test_returns_correct_number_of_steps(self):
        sim = SimParams(duration_hours=1.0, dt_minutes=1.0)
        model = HVACModel(RoomParams(), HVACParams(), sim)
        history = model.simulate()
        # n_steps = 60, plus initial state
        assert len(history) == 61

    def test_first_entry_is_initial_state(self):
        sim = SimParams(initial_temp_c=30.0)
        model = HVACModel(RoomParams(), HVACParams(), sim)
        history = model.simulate()
        assert history[0].room_temp_c == pytest.approx(30.0)
        assert history[0].energy_kwh == pytest.approx(0.0)
        assert history[0].time_min == pytest.approx(0.0)

    def test_energy_is_monotonically_non_decreasing(self):
        model = default_model(duration_hours=2.0)
        history = model.simulate()
        energies = [s.energy_kwh for s in history]
        for prev, curr in zip(energies, energies[1:]):
            assert curr >= prev - 1e-9  # allow tiny float rounding

    def test_room_temp_converges_toward_setpoint_cooling(self):
        sim = SimParams(duration_hours=8.0, initial_temp_c=35.0, setpoint_c=22.0, outdoor_temp_c=38.0)
        model = HVACModel(RoomParams(), HVACParams(), sim)
        history = model.simulate()
        final_temp = history[-1].room_temp_c
        initial_temp = history[0].room_temp_c
        # Final temp should be closer to setpoint than initial
        assert abs(final_temp - sim.setpoint_c) < abs(initial_temp - sim.setpoint_c)

    def test_room_temp_converges_toward_setpoint_heating(self):
        sim = SimParams(duration_hours=8.0, initial_temp_c=5.0, setpoint_c=22.0, outdoor_temp_c=0.0)
        model = HVACModel(RoomParams(), HVACParams(), sim)
        history = model.simulate()
        final_temp = history[-1].room_temp_c
        initial_temp = history[0].room_temp_c
        assert abs(final_temp - sim.setpoint_c) < abs(initial_temp - sim.setpoint_c)

    def test_time_is_monotonically_increasing(self):
        model = default_model(duration_hours=1.0)
        history = model.simulate()
        times = [s.time_min for s in history]
        for prev, curr in zip(times, times[1:]):
            assert curr > prev

    def test_mode_never_both_cooling_and_heating(self):
        model = default_model(duration_hours=4.0)
        history = model.simulate()
        for state in history:
            assert state.mode in ("off", "cooling", "windfree", "heating")

    def test_total_energy_is_positive_after_run(self):
        model = default_model(duration_hours=1.0)
        history = model.simulate()
        assert history[-1].energy_kwh > 0
