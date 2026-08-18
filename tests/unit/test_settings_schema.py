"""The settings schema, checked against the code and the strings beside it.

Every failure mode here is silent in Kodi. A label id with no string renders as
a blank row, which reads as a broken schema. A `menu_*` setting the code does
not know about is simply never published. An empty `<default>` without
`<allowempty>` makes Kodi drop the whole setting from its group, so it does not
appear in the dialog at all -- and it says so only once, at INFO, in a log
nobody is reading.

All three were made by hand while writing this schema. Hence the tests.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from resources.lib.settings import MENU_BUTTONS

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "resources/settings.xml"
STRINGS = ROOT / "resources/language/resource.language.en_GB/strings.po"


def schema():
    return ET.parse(SCHEMA).getroot()


def settings():
    return list(schema().iter("setting"))


def defined_strings():
    return set(re.findall(r'msgctxt "#(\d+)"', STRINGS.read_text()))


def test_every_addon_string_the_schema_references_exists():
    """Ids below 30000 are Kodi's own and are not ours to define."""
    referenced = set()

    for element in schema().iter():
        for attribute in ("label", "help"):
            value = element.get(attribute)
            if value and value.isdigit() and int(value) >= 30000:
                referenced.add(value)

    for heading in schema().iter("heading"):
        if heading.text and heading.text.isdigit() and int(heading.text) >= 30000:
            referenced.add(heading.text)

    assert referenced
    assert not referenced - defined_strings()


def test_no_string_is_defined_twice():
    ids = re.findall(r'msgctxt "#(\d+)"', STRINGS.read_text())

    assert len(ids) == len(set(ids))


def test_setting_ids_are_unique():
    ids = [s.get("id") for s in settings()]

    assert len(ids) == len(set(ids))


def test_the_menu_buttons_the_code_publishes_are_the_ones_in_the_schema():
    """Drift here is invisible: a setting the code does not know about is never
    mirrored to a window property, and a name the schema does not have reads as
    False and silently hides the button.
    """
    in_schema = {
        s.get("id")[len("menu_") :]
        for s in settings()
        if s.get("id").startswith("menu_")
    }

    assert in_schema == set(MENU_BUTTONS)


@pytest.mark.parametrize(
    "setting",
    [s for s in settings() if s.get("type") == "string"],
    ids=lambda s: s.get("id"),
)
def test_a_string_setting_that_can_be_empty_says_so(setting):
    """Kodi rejects an empty <default> unless <allowempty> is present, and then
    drops the setting from its group rather than warning usefully. Verified on
    21.3: omdb_api_key simply did not appear in the dialog.
    """
    default = setting.find("default")
    is_empty = default is None or not (default.text or "").strip()

    if not is_empty:
        return

    allow = setting.find("./constraints/allowempty")

    assert allow is not None and allow.text == "true"


def test_the_settings_that_shipped_before_this_schema_kept_their_ids():
    """Kodi keys stored values by id, so a rename silently resets the user to
    default with nothing in the log. These eleven are what the old flat schema
    had, and they are the whole migration story.
    """
    inherited = {
        "language_code",
        "country_code",
        "tmdb_api_key",
        "omdb_api_key",
        "trakt_api_key",
        "filter_shows",
        "filter_movies",
        "similar_movies_filter",
        "filter_upcoming",
        "filter_daydelta",
        "cache_enabled",
    }

    assert inherited <= {s.get("id") for s in settings()}
