"""
Tests for weather.py — live weather and historical data fetching.

All HTTP calls are mocked; we test:
  - fetch_current_temp(): happy path, geocoding failure, city not found,
    yr.no network error, malformed response format
  - fetch_historical_daily_means(): correct averaging, None-temp skipping,
    (month, day) key structure, HTTP error propagation
"""

import pytest
import requests

from weather import fetch_current_temp, fetch_historical_daily_means


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_nominatim_response(lat="13.0827", lon="80.2707",
                              display_name="Chennai, Tamil Nadu, India"):
    return [{"lat": lat, "lon": lon, "display_name": display_name}]


def _make_yr_response(temp_c=32.5, time_str="2024-06-01T12:00:00Z"):
    return {
        "properties": {
            "timeseries": [
                {
                    "time": time_str,
                    "data": {
                        "instant": {
                            "details": {"air_temperature": temp_c}
                        }
                    }
                }
            ]
        }
    }


# ---------------------------------------------------------------------------
# fetch_current_temp — happy path
# ---------------------------------------------------------------------------

class TestFetchCurrentTempHappyPath:
    def test_returns_temp_and_metadata(self, mocker):
        mocker.patch("weather.requests.get", side_effect=[
            mocker.Mock(status_code=200,
                        json=lambda: _make_nominatim_response(),
                        raise_for_status=lambda: None),
            mocker.Mock(status_code=200,
                        json=lambda: _make_yr_response(temp_c=31.0),
                        raise_for_status=lambda: None),
        ])
        result = fetch_current_temp("Chennai")
        assert result["error"] is None
        assert result["temp_c"] == pytest.approx(31.0)
        assert result["lat"] == pytest.approx(13.0827)
        assert result["lon"] == pytest.approx(80.2707)

    def test_city_is_first_part_of_display_name(self, mocker):
        mocker.patch("weather.requests.get", side_effect=[
            mocker.Mock(status_code=200,
                        json=lambda: _make_nominatim_response(
                            display_name="Mumbai, Maharashtra, India"),
                        raise_for_status=lambda: None),
            mocker.Mock(status_code=200,
                        json=lambda: _make_yr_response(),
                        raise_for_status=lambda: None),
        ])
        result = fetch_current_temp("Mumbai")
        assert result["city"] == "Mumbai"

    def test_display_name_preserved(self, mocker):
        full_name = "Chennai, Chennai District, Tamil Nadu, India"
        mocker.patch("weather.requests.get", side_effect=[
            mocker.Mock(status_code=200,
                        json=lambda: _make_nominatim_response(display_name=full_name),
                        raise_for_status=lambda: None),
            mocker.Mock(status_code=200,
                        json=lambda: _make_yr_response(),
                        raise_for_status=lambda: None),
        ])
        result = fetch_current_temp("Chennai")
        assert result["display_name"] == full_name

    def test_fetched_at_is_returned(self, mocker):
        mocker.patch("weather.requests.get", side_effect=[
            mocker.Mock(status_code=200,
                        json=lambda: _make_nominatim_response(),
                        raise_for_status=lambda: None),
            mocker.Mock(status_code=200,
                        json=lambda: _make_yr_response(time_str="2024-06-01T09:00:00Z"),
                        raise_for_status=lambda: None),
        ])
        result = fetch_current_temp("Chennai")
        assert result["fetched_at"] == "2024-06-01T09:00:00Z"


# ---------------------------------------------------------------------------
# fetch_current_temp — geocoding failures
# ---------------------------------------------------------------------------

class TestFetchCurrentTempGeocodingFailures:
    def test_geocoding_network_error_returns_error_dict(self, mocker):
        mocker.patch("weather.requests.get",
                     side_effect=requests.exceptions.ConnectionError("timeout"))
        result = fetch_current_temp("Chennai")
        assert result["temp_c"] is None
        assert result["error"] is not None
        assert "Geocoding failed" in result["error"]

    def test_city_not_found_returns_error(self, mocker):
        mocker.patch("weather.requests.get",
                     return_value=mocker.Mock(status_code=200,
                                              json=lambda: [],
                                              raise_for_status=lambda: None))
        result = fetch_current_temp("NonExistentCityXYZ")
        assert result["temp_c"] is None
        assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# fetch_current_temp — yr.no failures
# ---------------------------------------------------------------------------

class TestFetchCurrentTempYrFailures:
    def test_yr_network_error_returns_error_dict(self, mocker):
        mocker.patch("weather.requests.get", side_effect=[
            mocker.Mock(status_code=200,
                        json=lambda: _make_nominatim_response(),
                        raise_for_status=lambda: None),
            requests.exceptions.Timeout("timed out"),
        ])
        result = fetch_current_temp("Chennai")
        assert result["temp_c"] is None
        assert "Weather fetch failed" in result["error"]

    def test_malformed_yr_response_missing_timeseries(self, mocker):
        malformed = {"properties": {}}  # no timeseries key
        mocker.patch("weather.requests.get", side_effect=[
            mocker.Mock(status_code=200,
                        json=lambda: _make_nominatim_response(),
                        raise_for_status=lambda: None),
            mocker.Mock(status_code=200,
                        json=lambda: malformed,
                        raise_for_status=lambda: None),
        ])
        result = fetch_current_temp("Chennai")
        assert result["temp_c"] is None
        assert "Unexpected response format" in result["error"]

    def test_malformed_yr_response_empty_timeseries(self, mocker):
        malformed = {"properties": {"timeseries": []}}
        mocker.patch("weather.requests.get", side_effect=[
            mocker.Mock(status_code=200,
                        json=lambda: _make_nominatim_response(),
                        raise_for_status=lambda: None),
            mocker.Mock(status_code=200,
                        json=lambda: malformed,
                        raise_for_status=lambda: None),
        ])
        result = fetch_current_temp("Chennai")
        assert result["temp_c"] is None
        assert result["error"] is not None


# ---------------------------------------------------------------------------
# fetch_historical_daily_means
# ---------------------------------------------------------------------------

class TestFetchHistoricalDailyMeans:
    def _mock_archive_response(self, mocker, dates, temps):
        mock_resp = mocker.Mock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "daily": {
                "time": dates,
                "temperature_2m_mean": temps,
            }
        }
        mocker.patch("weather.requests.get", return_value=mock_resp)
        return mock_resp

    def test_returns_dict_with_month_day_keys(self, mocker):
        self._mock_archive_response(
            mocker,
            dates=["2023-06-15", "2022-06-15"],
            temps=[35.0, 33.0],
        )
        result = fetch_historical_daily_means(13.08, 80.27)
        assert (6, 15) in result

    def test_averages_same_day_across_years(self, mocker):
        self._mock_archive_response(
            mocker,
            dates=["2023-06-15", "2022-06-15", "2021-06-15"],
            temps=[36.0, 34.0, 35.0],
        )
        result = fetch_historical_daily_means(13.08, 80.27)
        assert result[(6, 15)] == pytest.approx(35.0)

    def test_skips_none_temperatures(self, mocker):
        self._mock_archive_response(
            mocker,
            dates=["2023-06-15", "2022-06-15", "2021-06-15"],
            temps=[36.0, None, 34.0],
        )
        result = fetch_historical_daily_means(13.08, 80.27)
        # Only the two non-None values averaged
        assert result[(6, 15)] == pytest.approx(35.0)

    def test_all_none_temperatures_excluded(self, mocker):
        self._mock_archive_response(
            mocker,
            dates=["2023-06-15"],
            temps=[None],
        )
        result = fetch_historical_daily_means(13.08, 80.27)
        assert (6, 15) not in result

    def test_multiple_different_days(self, mocker):
        self._mock_archive_response(
            mocker,
            dates=["2023-01-01", "2023-07-04", "2022-01-01"],
            temps=[20.0, 35.0, 18.0],
        )
        result = fetch_historical_daily_means(13.08, 80.27)
        assert (1, 1) in result
        assert (7, 4) in result
        assert result[(1, 1)] == pytest.approx(19.0)
        assert result[(7, 4)] == pytest.approx(35.0)

    def test_empty_response_returns_empty_dict(self, mocker):
        self._mock_archive_response(mocker, dates=[], temps=[])
        result = fetch_historical_daily_means(13.08, 80.27)
        assert result == {}

    def test_http_error_propagates(self, mocker):
        mock_resp = mocker.Mock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("404")
        mocker.patch("weather.requests.get", return_value=mock_resp)
        with pytest.raises(requests.HTTPError):
            fetch_historical_daily_means(0.0, 0.0)
