"""Origin filters use the same metadata as TMDb's list and detail responses."""

import xml.etree.ElementTree as ET
from pathlib import Path

from conftest import set_setting

from resources.lib import tmdb


SCHEMA = Path(__file__).resolve().parents[2] / "resources/settings.xml"


def test_the_two_native_multiselects_use_the_agreed_catalogs():
    root = ET.parse(SCHEMA).getroot()
    settings = {item.get("id"): item for item in root.iter("setting")}

    languages = [
        option.text
        for option in settings["filter_hidden_languages"].iter("option")
    ]
    countries = [
        option.text
        for option in settings["filter_hidden_countries"].iter("option")
    ]
    existing_languages = {
        option.text.split("-")[0]
        for option in settings["language_code"].iter("option")
    }
    existing_countries = {
        option.text for option in settings["country_code"].iter("option")
    }

    assert len(languages) == 45
    assert set(languages) == existing_languages
    assert len(countries) == 26
    assert set(countries) == existing_countries
    for name in ("filter_hidden_languages", "filter_hidden_countries"):
        assert settings[name].get("type") == "list[string]"
        assert settings[name].findtext("./control/multiselect") == "true"
        assert settings[name].findtext("./constraints/delimiter") == ","


def test_empty_selections_leave_the_input_untouched(monkeypatch):
    items = [{"id": 1, "original_language": "hi"}]
    monkeypatch.setattr(tmdb, "_movie_countries", lambda items: 1 / 0)

    assert tmdb.exclude_origins(items, "movie") is items


def test_languages_and_tv_origin_countries_are_combined():
    set_setting("filter_hidden_languages", "hi,ta")
    set_setting("filter_hidden_countries", "IN")
    items = [
        {"id": 1, "original_language": "hi", "origin_country": ["US"]},
        {"id": 2, "original_language": "en", "origin_country": ["IN", "GB"]},
        {"id": 3, "original_language": "en", "origin_country": ["US"]},
        {"id": 4, "original_language": "en"},
    ]

    assert [item["id"] for item in tmdb.exclude_origins(items, "tv")] == [3, 4]


def test_movie_country_lookup_is_cached_and_unknown_movies_are_kept(monkeypatch):
    set_setting("filter_hidden_countries", "IN")
    store = {}
    calls = []

    def query(action, call, use_language):
        assert action == "movie" and use_language is False
        calls.append(call)
        if call == 3:
            return None
        country = "IN" if call == 1 else "US"
        return {"production_countries": [{"iso_3166_1": country}]}

    monkeypatch.setattr(tmdb, "tmdb_query", query)
    monkeypatch.setattr(tmdb, "get_cache", lambda key: store.get(key))
    monkeypatch.setattr(tmdb, "write_cache", lambda key, value: store.__setitem__(key, value))
    items = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 1}]

    assert [item["id"] for item in tmdb.exclude_origins(items, "movie")] == [2, 3]
    assert set(calls) == {1, 2, 3}
    assert store["movie_origin_1"] == {"countries": ["IN"]}
    assert store["movie_origin_2"] == {"countries": ["US"]}
    assert "movie_origin_3" not in store

    calls.clear()
    assert [item["id"] for item in tmdb.exclude_origins(items, "movie")] == [2, 3]
    assert calls == [3]


def test_inline_movie_details_do_not_need_another_request(monkeypatch):
    set_setting("filter_hidden_countries", "IN")
    monkeypatch.setattr(tmdb, "tmdb_query", lambda **kwargs: 1 / 0)
    items = [
        {"id": 1, "production_countries": [{"iso_3166_1": "IN"}]},
        {"id": 2, "production_countries": [{"iso_3166_1": "US"}]},
        {"id": 3, "production_countries": []},
    ]

    assert [item["id"] for item in tmdb.exclude_origins(items, "movie")] == [2, 3]


def test_country_lookup_stops_after_an_entire_failed_batch(monkeypatch):
    set_setting("filter_hidden_countries", "IN")
    calls = []
    monkeypatch.setattr(tmdb, "get_cache", lambda key: None)
    monkeypatch.setattr(tmdb, "tmdb_query", lambda **kwargs: calls.append(kwargs["call"]))
    items = [{"id": number} for number in range(20)]

    assert tmdb.exclude_origins(items, "movie") == items
    assert len(calls) == 8


def test_a_changed_selection_is_seen_on_the_next_launch():
    items = [{"id": 1, "original_language": "hi"}]

    set_setting("filter_hidden_languages", "hi")
    assert tmdb.exclude_origins(items, "movie") == []
    set_setting("filter_hidden_languages", "")
    assert tmdb.exclude_origins(items, "movie") == items
