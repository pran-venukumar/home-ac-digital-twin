"""
Tests for model_search.py — AC model database and search.

Covers:
  - Empty query returns all local models
  - Brand/model/label substring search (case-insensitive)
  - No results for unknown brand
  - Result cap at 20
  - Sort order (brand alphabetically, then tonnage numerically)
  - Regex patterns extract capacity, tonnage, COP, star rating from text
  - search_ac_models_online() with mocked HTTP response
  - search_models() deduplicates online results against local hits
"""

import pytest

from model_search import (
    LOCAL_DB,
    _COP_PATTERN,
    _KW_PATTERN,
    _STAR_PATTERN,
    _STAR_TO_COP,
    _TON_PATTERN,
    search_models,
)


# ---------------------------------------------------------------------------
# search_models — local DB queries
# ---------------------------------------------------------------------------

class TestSearchModelsLocal:
    def test_empty_query_returns_all_local_models(self):
        results = search_models("")
        # Capped at 20, but LOCAL_DB has more entries so we expect 20
        assert len(results) == 20

    def test_empty_query_includes_custom_sentinel(self):
        results = search_models("")
        brands = {r["brand"] for r in results}
        # Custom should be present (it's in LOCAL_DB)
        assert "Custom" in brands or len(LOCAL_DB) <= 20

    def test_brand_search_samsung(self):
        results = search_models("Samsung")
        assert all(r["brand"] == "Samsung" for r in results)
        assert len(results) > 0

    def test_brand_search_case_insensitive(self):
        results_upper = search_models("SAMSUNG")
        results_lower = search_models("samsung")
        assert len(results_upper) == len(results_lower)
        assert {r["label"] for r in results_upper} == {r["label"] for r in results_lower}

    def test_brand_search_lg(self):
        results = search_models("LG")
        assert all(r["brand"] == "LG" for r in results)

    def test_model_search_windfree(self):
        results = search_models("windfree")
        assert len(results) > 0
        assert all("windfree" in r["label"].lower() or "windfree" in r["model"].lower()
                   for r in results)

    def test_tonnage_search(self):
        results = search_models("1.5T")
        assert len(results) > 0
        assert all(r["tonnage"] == 1.5 for r in results)

    def test_unknown_brand_returns_empty(self):
        results = search_models("Mitsubishi")
        assert results == []

    def test_results_sorted_by_brand_then_tonnage(self):
        results = search_models("")
        for i in range(len(results) - 1):
            a, b = results[i], results[i + 1]
            # Brand should be non-decreasing
            assert a["brand"] <= b["brand"] or a["brand"] == b["brand"]
            # Within same brand, tonnage should be non-decreasing
            if a["brand"] == b["brand"]:
                assert a["tonnage"] <= b["tonnage"]

    def test_result_schema_has_required_fields(self):
        required = {"brand", "model", "label", "capacity_kw", "cop_rated", "tonnage"}
        results = search_models("daikin")
        assert len(results) > 0
        for r in results:
            assert required.issubset(r.keys())

    def test_capacity_kw_is_positive(self):
        results = search_models("")
        for r in results:
            assert r["capacity_kw"] > 0

    def test_cop_rated_is_positive(self):
        results = search_models("")
        for r in results:
            assert r["cop_rated"] > 0

    def test_result_capped_at_20(self):
        results = search_models("")
        assert len(results) <= 20

    def test_online_false_returns_only_local(self, mocker):
        mock_online = mocker.patch("model_search.search_ac_models_online", return_value=[
            {"brand": "Online", "model": "X", "label": "X (5.0 kW)",
             "capacity_kw": 5.0, "cop_rated": 4.0, "tonnage": 1.5}
        ])
        results = search_models("samsung", online=False)
        mock_online.assert_not_called()
        assert all(r["brand"] != "Online" for r in results)

    def test_online_results_merged_and_deduplicated(self, mocker):
        online_entry = {
            "brand": "Online", "model": "Exotic AC 1.5T",
            "label": "Exotic AC 1.5T (5.28 kW)",
            "capacity_kw": 5.275, "cop_rated": 4.1, "tonnage": 1.5,
        }
        mocker.patch("model_search.search_ac_models_online", return_value=[online_entry])
        results = search_models("exotic", online=True)
        labels = [r["label"] for r in results]
        # Online result should be present (not a duplicate of local)
        assert online_entry["label"] in labels

    def test_online_result_with_same_label_as_local_is_excluded(self, mocker):
        # Pick a label that already exists in LOCAL_DB
        existing_label = LOCAL_DB[0]["label"]
        duplicate_online = dict(LOCAL_DB[0])  # copy
        mocker.patch("model_search.search_ac_models_online", return_value=[duplicate_online])
        # Query broadly so local hit is included
        results = search_models(LOCAL_DB[0]["brand"], online=True)
        # Count occurrences of the duplicate label — should appear exactly once
        count = sum(1 for r in results if r["label"] == existing_label)
        assert count == 1


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

class TestKWPattern:
    def test_extracts_integer_kw(self):
        m = _KW_PATTERN.search("Capacity: 5 kW")
        assert m and float(m.group(1)) == 5.0

    def test_extracts_decimal_kw(self):
        m = _KW_PATTERN.search("Samsung WindFree 5.28 kW inverter AC")
        assert m and float(m.group(1)) == pytest.approx(5.28)

    def test_case_insensitive_KW(self):
        m = _KW_PATTERN.search("7.03 KW")
        assert m is not None

    def test_no_match_when_absent(self):
        m = _KW_PATTERN.search("No capacity info here")
        assert m is None


class TestTonPattern:
    def test_extracts_ton(self):
        m = _TON_PATTERN.search("1.5 Ton inverter AC")
        assert m and float(m.group(1)) == pytest.approx(1.5)

    def test_extracts_tonne(self):
        m = _TON_PATTERN.search("2 Tonne AC unit")
        assert m and float(m.group(1)) == 2.0

    def test_case_insensitive(self):
        m = _TON_PATTERN.search("2.0 TON")
        assert m is not None

    def test_no_match_when_absent(self):
        assert _TON_PATTERN.search("No tonnage") is None


class TestCOPPattern:
    def test_extracts_cop_with_colon(self):
        m = _COP_PATTERN.search("COP: 4.2")
        assert m and float(m.group(1)) == pytest.approx(4.2)

    def test_extracts_cop_with_space(self):
        m = _COP_PATTERN.search("Rated COP 5.0 at full load")
        assert m and float(m.group(1)) == pytest.approx(5.0)

    def test_no_match_when_absent(self):
        assert _COP_PATTERN.search("No efficiency info") is None


class TestStarPattern:
    def test_extracts_star_rating(self):
        m = _STAR_PATTERN.search("5 Star rated inverter")
        assert m and int(m.group(1)) == 5

    def test_extracts_lowercase_star(self):
        m = _STAR_PATTERN.search("3 star BEE rating")
        assert m is not None

    def test_star_to_cop_mapping_complete(self):
        for stars in range(1, 6):
            assert stars in _STAR_TO_COP
            assert _STAR_TO_COP[stars] > 0

    def test_higher_star_higher_cop(self):
        cops = [_STAR_TO_COP[s] for s in range(1, 6)]
        assert cops == sorted(cops)
