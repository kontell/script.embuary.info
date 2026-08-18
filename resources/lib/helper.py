#!/usr/bin/python
# coding: utf-8

########################

import xbmc
import xbmcgui
import xbmcplugin
import json
import time
import datetime
import os
import operator
import sys
import requests
import simplecache
import hashlib

from resources.lib.settings import *

########################

ADDON = addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_VERSION = ADDON.getAddonInfo("version")
ADDON_PATH = ADDON.getAddonInfo("path")

INFO = xbmc.LOGINFO
WARNING = xbmc.LOGWARNING
DEBUG = xbmc.LOGDEBUG
ERROR = xbmc.LOGERROR

DIALOG = xbmcgui.Dialog()

""" One HTTP session for the whole script run.

    Upstream called `requests.get`/`requests.head` directly, and each of those
    builds a throwaway Session: new TCP connection, new TLS handshake, torn
    down again immediately. A single movie page makes several TMDb calls, an
    OMDb call, and one HEAD per trailer -- twenty-odd handshakes to two or
    three hosts, all of which keep-alive would have collapsed into a couple of
    connections.

    That is worth far more on the devices this add-on runs on than on a
    desktop: a TLS handshake on a low-end ARM SoC is tens of milliseconds of
    real CPU, not a rounding error.

    max_retries stays 0 because the callers already implement their own retry
    with a delay between attempts, and stacking urllib3's retries underneath
    would multiply the worst-case wait rather than bound it.
"""
SESSION = requests.Session()
_ADAPTER = requests.adapters.HTTPAdapter(
    pool_connections=4, pool_maxsize=8, max_retries=0
)
SESSION.mount("https://", _ADAPTER)
SESSION.mount("http://", _ADAPTER)

""" Every outbound request gets a deadline. Upstream's trailer check had none
    at all, so a single unresponsive host could hang the dialog indefinitely --
    with the busy dialog up and no way out.
"""
HTTP_TIMEOUT = 5

FALLBACK_LANGUAGE = "en"

CACHE = simplecache.SimpleCache()
CACHE.enable_mem_cache = False
CACHE.data_is_json = True

# TIMEZONE = 'US/Alaska'
TIMEZONE = "local"

########################


def log(txt, loglevel=DEBUG, json=False, force=False):
    if force:
        loglevel = INFO

    if json:
        txt = json_prettyprint(txt)

    message = "[ %s ] %s" % (ADDON_ID, txt)
    xbmc.log(msg=message, level=loglevel)


def cache_prefix():
    """Namespace for this add-on's cache keys.

    Language and country are part of it because the cached payloads are
    language- and region-specific, so changing either has to miss rather than
    serve the previous locale's data. That is also why it is derived per call
    now: it used to be a module constant, which under interpreter reuse would
    keep naming the locale the first launch happened to see.
    """
    return "%s_%s_%s%s_" % (ADDON_ID, ADDON_VERSION, language_code(), country_code())


def get_cache(key):
    if cache_enabled():
        return CACHE.get(cache_prefix() + key)


def write_cache(key, data, cache_time=336):
    if data:
        CACHE.set(
            cache_prefix() + key, data, expiration=datetime.timedelta(hours=cache_time)
        )


def format_currency(integer):
    try:
        integer = int(integer)
        if integer < 1:
            raise Exception

        return "{:,.0f}".format(integer)

    except Exception:
        return ""


def sort_dict(items, key, reverse=False):
    """Dummy date to always add planned or rumored items at the end of the list
    if no release date is available yet.
    """
    for item in items:
        if not item.get(key):
            if not reverse:
                item[key] = "2999-01-01"
            else:
                item[key] = "1900-01-01"

    return sorted(items, key=operator.itemgetter(key), reverse=reverse)


def remove_quotes(label):
    if not label:
        return ""

    if label.startswith("'") and label.endswith("'") and len(label) > 2:
        label = label[1:-1]
        if label.startswith('"') and label.endswith('"') and len(label) > 2:
            label = label[1:-1]

    return label


def get_date(date_time):
    date_time_obj = datetime.datetime.strptime(date_time, "%Y-%m-%d %H:%M:%S")
    date_obj = date_time_obj.date()

    return date_obj


def execute(cmd):
    xbmc.executebuiltin(cmd)


def condition(condition):
    return xbmc.getCondVisibility(condition)


def busydialog(close=False):
    if not close and not condition("Window.IsVisible(busydialognocancel)"):
        execute("ActivateWindow(busydialognocancel)")
    elif close:
        execute("Dialog.Close(busydialognocancel)")


def textviewer(params):
    DIALOG.textviewer(
        remove_quotes(params.get("header", "")),
        remove_quotes(params.get("message", "")),
    )


def winprop(key, value=None, clear=False, window_id=10000):
    window = xbmcgui.Window(window_id)

    if clear:
        window.clearProperty(key.replace(".json", "").replace(".bool", ""))

    elif value is not None:

        if key.endswith(".json"):
            key = key.replace(".json", "")
            value = json.dumps(value)

        elif key.endswith(".bool"):
            key = key.replace(".bool", "")
            value = "true" if value else "false"

        window.setProperty(key, value)

    else:
        result = window.getProperty(key.replace(".json", "").replace(".bool", ""))

        if result:
            if key.endswith(".json"):
                result = json.loads(result)
            elif key.endswith(".bool"):
                result = result in ("true", "1")

        return result


""" Dates.

    These used to go through `arrow`, which cost 154 ms to import inside Kodi's
    interpreter and was paid on every single launch of the info dialog -- a
    launch that cannot amortise it, because <reuselanguageinvoker> does not
    apply to RunScript. What arrow was doing here is parsing two shapes and
    calling strftime, so it is now the standard library.

    arrow is still a dependency, but only of widgets.py, whose locale-aware
    "Thursday, 21 August 2026" has no concise stdlib equivalent. The plugin and
    the service can afford it; they are not what a user waits on.
"""

DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%Y",
)


def parse_date(value):
    """A datetime from the date shapes this add-on sees, or None.

    TMDb writes YYYY-MM-DD; Trakt writes an ISO timestamp ending in Z, which
    fromisoformat did not accept before Python 3.11 and which Kodi 20 would
    therefore have choked on.
    """
    if not value:
        return None

    text = str(value).strip()

    if text.endswith("Z"):
        text = text[:-1] + "+0000"

    for date_format_string in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text, date_format_string)
        except ValueError:
            continue

    return None


def date_year(value):
    """Year as a string, or '' if the value will not parse.

    Upstream assigned `year` inside the try and returned it after an `except:
    pass`, so any unparseable date raised UnboundLocalError out of a function
    whose whole purpose was to swallow bad input. It reached a release because
    the only caller passes TMDb birthday and deathday strings, which are
    usually well formed and are usually empty when they are not -- and empty
    returns on the line above.
    """
    if not value:
        return value

    parsed = parse_date(value)

    return str(parsed.year) if parsed else ""


def date_format(value, date="short", scheme=None):
    """Value rendered the way this Kodi renders dates.

    `scheme` overrides the region format with an explicit strftime string.
    xbmc.getRegion returns strftime formats already -- '%Y-%m-%d' for dateshort
    on the box this was written against -- so both paths feed strftime alike.
    """
    if not value:
        return value

    parsed = parse_date(value)

    if parsed is None:
        return value

    try:
        return parsed.strftime(scheme or xbmc.getRegion("date%s" % date))

    except Exception:
        return value


def date_delta(date):
    """Days from today until `date`; negative once it is past.

    An unparseable value is treated as the far-future sentinel the callers pass
    when they have no date at all. Upstream let arrow raise here, which would
    have taken down the whole page rather than misfiltering one item.
    """
    parsed = parse_date(date)
    day = parsed.date() if parsed else datetime.date(2900, 1, 1)

    return day - datetime.date.today()


def date_weekday(date=None):
    """(localised weekday name, weekday index) for a date, or today.

    Accepts a string, a date, a datetime, or an arrow -- widgets.py still holds
    arrow objects and passes them straight in.
    """
    if date is None:
        day = datetime.datetime.now().date()

    elif hasattr(date, "date"):
        day = date.date()

    elif isinstance(date, datetime.date):
        day = date

    else:
        parsed = parse_date(date)

        if parsed is None:
            return "", ""

        day = parsed.date()

    weekdays = (
        xbmc.getLocalizedString(11),
        xbmc.getLocalizedString(12),
        xbmc.getLocalizedString(13),
        xbmc.getLocalizedString(14),
        xbmc.getLocalizedString(15),
        xbmc.getLocalizedString(16),
        xbmc.getLocalizedString(17),
    )

    return weekdays[day.weekday()], day.weekday()


def utc_to_local(value):
    """A UTC timestamp as (local date, local time) strings."""
    parsed = parse_date(value)

    if parsed is None:
        return "", ""

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)

    local = parsed.astimezone()

    if xbmc.getRegion("time").startswith("%I"):
        return local.strftime("%Y-%m-%d"), local.strftime("%I:%M %p")

    return local.strftime("%Y-%m-%d"), local.strftime("%H:%M")


def get_bool(value, string="true"):
    try:
        if value.lower() == string:
            return True
        raise Exception

    except Exception:
        return False


def get_joined_items(item):
    if len(item) > 0:
        item = " / ".join(item)
    else:
        item = ""
    return item


def get_first_item(item):
    if len(item) > 0:
        item = item[0]
    else:
        item = ""

    return item


def json_call(
    method,
    properties=None,
    sort=None,
    query_filter=None,
    limit=None,
    params=None,
    item=None,
    options=None,
    limits=None,
):
    json_string = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}

    if properties is not None:
        json_string["params"]["properties"] = properties

    if limit is not None:
        json_string["params"]["limits"] = {"start": 0, "end": int(limit)}

    if sort is not None:
        json_string["params"]["sort"] = sort

    if query_filter is not None:
        json_string["params"]["filter"] = query_filter

    if options is not None:
        json_string["params"]["options"] = options

    if limits is not None:
        json_string["params"]["limits"] = limits

    if item is not None:
        json_string["params"]["item"] = item

    if params is not None:
        json_string["params"].update(params)

    json_string = json.dumps(json_string)

    result = xbmc.executeJSONRPC(json_string)

    """ Python 2 compatibility
    """
    try:
        result = unicode(result, "utf-8", errors="ignore")
    except NameError:
        pass

    return json.loads(result)


def set_plugincontent(content=None, category=None):
    if category:
        xbmcplugin.setPluginCategory(int(sys.argv[1]), category)
    if content:
        xbmcplugin.setContent(int(sys.argv[1]), content)


def json_prettyprint(string):
    return json.dumps(string, sort_keys=True, indent=4, separators=(",", ": "))


def urljoin(*args):
    """Joins given arguments into an url. Trailing but not leading slashes are
    stripped for each argument.
    """
    arglist = [arg for arg in args if arg is not None]
    return "/".join(map(lambda x: str(x).rstrip("/"), arglist))


def md5hash(value):
    value = str(value).encode()
    return hashlib.md5(value).hexdigest()
