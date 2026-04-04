"""
Tests for tariffs.py — Indian electricity tariff lookup.

Covers:
  - Exact state name matches
  - Case-insensitive matching
  - Partial / substring matching (Nominatim variations)
  - Empty and unknown inputs fall back to _default
  - extract_state_from_display_name() parses Nominatim display strings
  - All returned tuples have a positive rate and a non-empty DISCOM string
"""

import pytest

from tariffs import STATE_TARIFFS, extract_state_from_display_name, tariff_for_state


# ---------------------------------------------------------------------------
# tariff_for_state — basic lookups
# ---------------------------------------------------------------------------

class TestTariffForState:
    def test_exact_match_returns_correct_rate(self):
        rate, discom = tariff_for_state("Tamil Nadu")
        assert rate == pytest.approx(5.50)
        assert "TANGEDCO" in discom

    def test_exact_match_karnataka(self):
        rate, discom = tariff_for_state("Karnataka")
        assert rate == pytest.approx(6.00)
        assert "BESCOM" in discom

    def test_exact_match_delhi(self):
        rate, discom = tariff_for_state("Delhi")
        assert rate == pytest.approx(6.50)

    # Case-insensitive
    def test_lowercase_input(self):
        rate_exact, _ = tariff_for_state("Tamil Nadu")
        rate_lower, _ = tariff_for_state("tamil nadu")
        assert rate_lower == pytest.approx(rate_exact)

    def test_uppercase_input(self):
        rate_exact, _ = tariff_for_state("Maharashtra")
        rate_upper, _ = tariff_for_state("MAHARASHTRA")
        assert rate_upper == pytest.approx(rate_exact)

    def test_mixed_case_input(self):
        rate_exact, _ = tariff_for_state("Kerala")
        rate_mixed, _ = tariff_for_state("kErAlA")
        assert rate_mixed == pytest.approx(rate_exact)

    # Partial matching
    def test_partial_match_state_name_in_input(self):
        # "Tamil Nadu" substring present in longer string
        rate, discom = tariff_for_state("Tamil Nadu, India")
        assert rate == pytest.approx(5.50)

    def test_input_substring_of_state_name(self):
        # "Gujarat" matches because input "gujarat" in "gujarat"
        rate, discom = tariff_for_state("Gujarat")
        assert rate == pytest.approx(5.50)

    # Fallback
    def test_empty_string_returns_default(self):
        rate, discom = tariff_for_state("")
        default_rate, default_discom = STATE_TARIFFS["_default"]
        assert rate == pytest.approx(default_rate)
        assert discom == default_discom

    def test_none_equivalent_unknown_state(self):
        rate, discom = tariff_for_state("Atlantis")
        default_rate, default_discom = STATE_TARIFFS["_default"]
        assert rate == pytest.approx(default_rate)
        assert discom == default_discom

    def test_whitespace_only_returns_default(self):
        # strip() makes it empty → default
        rate, discom = tariff_for_state("   ")
        default_rate, _ = STATE_TARIFFS["_default"]
        assert rate == pytest.approx(default_rate)

    # Sanity: all entries have positive rates
    def test_all_tariff_rates_are_positive(self):
        for state, (rate, discom) in STATE_TARIFFS.items():
            assert rate > 0, f"{state} has non-positive rate"
            assert discom, f"{state} has empty DISCOM name"


# ---------------------------------------------------------------------------
# extract_state_from_display_name
# ---------------------------------------------------------------------------

class TestExtractStateFromDisplayName:
    def test_chennai_display_name(self):
        display = "Chennai, Chennai District, Tamil Nadu, India"
        state = extract_state_from_display_name(display)
        assert state == "Tamil Nadu"

    def test_bangalore_display_name(self):
        display = "Bengaluru, Bangalore Urban, Karnataka, India"
        state = extract_state_from_display_name(display)
        assert state == "Karnataka"

    def test_mumbai_display_name(self):
        display = "Mumbai, Mumbai City, Maharashtra, India"
        state = extract_state_from_display_name(display)
        assert state == "Maharashtra"

    def test_delhi_display_name(self):
        display = "New Delhi, Delhi, India"
        state = extract_state_from_display_name(display)
        assert state == "Delhi"

    def test_unknown_location_returns_empty_string(self):
        display = "Springfield, Shelbyville, Fictonia, India"
        state = extract_state_from_display_name(display)
        assert state == ""

    def test_skips_last_part_india(self):
        # "India" as last element should not be matched as a state
        display = "Some City, Some District, India"
        state = extract_state_from_display_name(display)
        assert state == ""

    def test_returns_first_valid_state_from_right(self):
        # Scans right-to-left (skipping India), should pick the state before city
        display = "Hyderabad, Hyderabad District, Telangana, India"
        state = extract_state_from_display_name(display)
        assert state == "Telangana"

    def test_single_part_city_india(self):
        display = "Kerala, India"
        # Only part before India is "Kerala" which IS a state
        state = extract_state_from_display_name(display)
        assert state == "Kerala"
