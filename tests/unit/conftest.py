"""Make `resources.lib.main` importable without a running Kodi.

Three things get in the way, all of them at import time rather than call time,
which is why this is a conftest and not a fixture:

  * `helper.py` builds ADDON at module scope, and every settings accessor in
    `resources.lib.settings` goes through `xbmcaddon.Addon()`. Kodistubs'
    Addon returns '' for everything, which is the wrong type for the boolean
    and integer settings.
  * `simplecache`, `arrow` and `routing` are Kodi add-on modules resolved from
    the user's add-on directory at runtime. They are not on PyPI in the form
    Kodi ships, so they are faked here rather than installed.
  * The add-on imports itself as `resources.lib.x`, which only resolves with
    the repo root on sys.path.

`set_setting` is the way a test changes one: values are memoised for the length
of a launch, so writing into `_SETTINGS` alone would not be seen.
"""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fake_simplecache():
    module = types.ModuleType("simplecache")

    class SimpleCache:
        def __init__(self, *args, **kwargs):
            self.enable_mem_cache = True
            self.data_is_json = False
            self._store = {}

        def get(self, key, *args, **kwargs):
            return self._store.get(key)

        def set(self, key, data, *args, **kwargs):
            self._store[key] = data

        def close(self):
            pass

    module.SimpleCache = SimpleCache
    return module


def _fake_arrow():
    module = types.ModuleType("arrow")

    class _Arrow:
        year = 1970

        def to(self, *args, **kwargs):
            return self

        def date(self):
            import datetime

            return datetime.date(1970, 1, 1)

        def strftime(self, fmt):
            return ""

    module.get = lambda *args, **kwargs: _Arrow()
    module.utcnow = lambda: _Arrow()
    return module


def _fake_routing():
    module = types.ModuleType("routing")

    class Plugin:
        def __init__(self, *args, **kwargs):
            pass

        def route(self, *args, **kwargs):
            return lambda fn: fn

        def run(self, *args, **kwargs):
            pass

    module.Plugin = Plugin
    return module


sys.modules.setdefault("simplecache", _fake_simplecache())
sys.modules.setdefault("arrow", _fake_arrow())
sys.modules.setdefault("routing", _fake_routing())


""" Stand-in settings store. Only the types have to be right -- every test that
    cares about a value sets it explicitly, through set_setting().
"""
_SETTINGS = {
    "tmdb_api_key": "test-key",
    "omdb_api_key": "",
    "country_code": "GB",
    "language_code": "en-GB",
    "filter_daydelta": "0",
}


class _FakeAddon:
    def getAddonInfo(self, key):
        return {
            "id": "script.embuary.info",
            "version": "2.1.0",
            "path": str(ROOT),
        }.get(key, "")

    def getSetting(self, key):
        return _SETTINGS.get(key, "")

    def getSettingString(self, key):
        return _SETTINGS.get(key, "")

    def getSettingBool(self, key):
        return _SETTINGS.get(key) == "true"

    def getSettingInt(self, key):
        try:
            return int(_SETTINGS.get(key, "0"))
        except ValueError:
            return 0

    def getSettingNumber(self, key):
        try:
            return float(_SETTINGS.get(key, "0"))
        except ValueError:
            return 0.0

    def getLocalizedString(self, code):
        return "string-%s" % code


import xbmcaddon  # noqa: E402  (must follow the sys.modules setup above)

xbmcaddon.Addon = _FakeAddon

import pytest  # noqa: E402

from resources.lib import settings as _settings  # noqa: E402


def set_setting(key, value):
    """Set one setting and drop the memo, the way Kodi's own change would.

    Writing straight into _SETTINGS is not enough: accessors memoise for the
    length of a launch, so a test that skipped the refresh would silently keep
    reading whatever an earlier test left behind.
    """
    _SETTINGS[key] = value
    _settings.refresh()


@pytest.fixture(autouse=True)
def _isolate_settings():
    """No test inherits another's settings, in either direction."""
    original = dict(_SETTINGS)
    _settings.refresh()
    yield
    _SETTINGS.clear()
    _SETTINGS.update(original)
    _settings.refresh()
