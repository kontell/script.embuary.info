#!/usr/bin/python
# coding: utf-8

########################

import xbmcaddon

########################

""" Every add-on setting, read at the moment it is asked for rather than at the
    moment this module is imported.

    Upstream read settings into module constants at import time: helper.py built
    COUNTRY_CODE and DEFAULT_LANGUAGE, tmdb.py API_KEY, person.py and video.py
    the filter flags. That is correct only for as long as every RunScript gets
    its own interpreter, because then "imported" and "launched" are the same
    instant.

    They stop being the same instant under <reuselanguageinvoker>, which parks
    the interpreter instead of tearing it down. The modules stay in sys.modules,
    their constants keep whatever the first launch happened to read, and a user
    who changes a setting sees nothing happen -- no error, nothing in the log,
    and the settings dialog still showing the value they picked. So nothing here
    caches a value.

    Values are memoised for the length of one launch and dropped by refresh(),
    which every entry point calls on its way in. Reading straight through to
    Kodi every time would be the simpler rule, but these are read per list item
    -- tmdb_fallback_info alone reads the language twice for each of the ~490
    items a movie page builds -- and a memo scoped to one launch costs nothing
    in freshness: a page ought to be built from one consistent set of settings
    anyway. The service, which outlives any launch, refreshes from
    onSettingsChanged instead.
"""

_addon = None
_values: dict = {}


def addon():
    """The shared Addon handle."""
    global _addon

    if _addon is None:
        _addon = xbmcaddon.Addon()

    return _addon


def refresh():
    """Forget everything read so far.

    Called at the start of each launch and whenever Kodi reports the settings
    changed. Without it, interpreter reuse would serve a parked module's idea
    of the settings for as long as the interpreter lived -- silently, since
    nothing about a stale read looks like a failure.
    """
    global _addon

    _values.clear()
    _addon = None


def _read(getter, key, fallback):
    """One setting, surviving a handle Kodi has invalidated underneath us.

    Kodi unloads an add-on before replacing any of its files, and for that
    window `xbmcaddon.Addon()` raises and an existing handle is dead. A routine
    repository update opens it as readily as a manual reinstall, and under
    interpreter reuse our handle outlives far more of the add-on's lifecycle
    than it used to. One rebuild costs a construction on the rare occasion it
    is needed and nothing at all otherwise.
    """
    global _addon

    if key in _values:
        return _values[key]

    for attempt in (1, 2):
        try:
            value = getattr(addon(), getter)(key)
            break

        except Exception:
            if attempt == 1:
                _addon = None
            else:
                """Not memoised: a failure here is a transient state of Kodi's,
                not an answer, and caching it would outlast the window that
                caused it.
                """
                return fallback

    _values[key] = value
    return value


def _string(key):
    return _read("getSettingString", key, "")


def _bool(key):
    return _read("getSettingBool", key, False)


def _int(key, fallback=0):
    return _read("getSettingInt", key, fallback)


########################
""" TheMovieDB
"""


def language_code():
    return _string("language_code")


def country_code():
    return _string("country_code")


########################
""" API keys
"""


def tmdb_api_key():
    return _string("tmdb_api_key")


def omdb_api_key():
    return _string("omdb_api_key")


def trakt_api_key():
    return _string("trakt_api_key")


########################
""" Filters
"""


def filter_shows():
    """Hide reality, talk and news shows in a person's TV credits."""
    return _bool("filter_shows")


def filter_movies():
    """Hide credits where the person appears as themselves.

    The id is the one the old schema used, so existing installs keep their
    value. It reads as movie-only and no longer is: the same rule now applies
    to TV credits, which were never character-filtered at all.
    """
    return _bool("filter_movies")


def filter_documentaries():
    """Hide documentaries outright, parts the person genuinely played included."""
    return _bool("filter_documentaries")


def similar_movies_filter():
    return _bool("similar_movies_filter")


def filter_upcoming():
    return _bool("filter_upcoming")


def filter_daydelta():
    return _int("filter_daydelta", 180)


########################
""" Advanced
"""


def cache_enabled():
    return _bool("cache_enabled")


########################
""" Menu entries.

    Each of these is one entry the add-on offers, and the Skin category has a
    toggle per entry. The add-on root honours them directly; a skin cannot,
    because a <visible> condition has no way to read an add-on setting -- so the
    service mirrors each onto a window property for skins to gate on. See
    service.py.

    MENU_ROOT are entries in the add-on's own root listing. MENU_BUTTONS are
    deep links that exist only as buttons a skin draws; there is no root entry
    to hide, which is why they are listed separately.
"""

MENU_ROOT = ("discover", "movies", "tvshows", "nextaired", "search")
MENU_BUTTONS = ("now_playing", "upcoming")
MENU_ENTRIES = MENU_ROOT + MENU_BUTTONS

MENU_PROPERTY = "embuary.menu.%s"


def menu_enabled(name):
    """Whether one menu entry is on offer."""
    return _bool("menu_%s" % name)
