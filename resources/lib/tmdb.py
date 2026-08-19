#!/usr/bin/python
# coding: utf-8

########################

import xbmc
import xbmcgui
import datetime
from urllib.parse import urlencode


from resources.lib.helper import *
from resources.lib.omdb import *
from resources.lib.localdb import *

########################

API_URL = "https://api.themoviedb.org/3/"
IMAGE_BASE = "https://image.tmdb.org/t/p/"

""" TMDb pre-renders every image at a handful of widths and upstream asked for
    `original` at all eighteen call sites -- cast thumbnails, studio logos and
    episode stills included.

    That is pure waste, and the add-on's own logs prove it: Kodi caches these
    at 480x720 for posters and 1920x1080 for fanart, so a 2000x3000 original
    poster is downloaded in full, decoded in full, and then thrown away down to
    a sixth of its area. The cost is paid three times -- bandwidth, a
    full-resolution decode on a CPU that is usually a low-end ARM part, and the
    peak RSS of holding the decoded bitmap.

    The sizes below are chosen against what the control is actually drawn at on
    a 1080p screen. Posters clear Kodi's 480x720 cache ceiling with room to
    spare, so they cannot look softer than they did.

    Backdrops need care, and an earlier version of this got it wrong. Kodi
    caches fanart at 1920x1080, and TMDb's backdrop widths go w300, w780,
    w1280, original -- there is nothing between w1280 and original. So w1280
    for everything would have put the page's own background *below* display
    resolution: a real, visible regression, not a free saving.

    The split is therefore by role rather than by art type. The backdrop a page
    shows as its own background stays `original`, which is one image and
    matches upstream exactly. Backdrops belonging to *other* titles in a list --
    similar films, collection members -- take w1280, and there are twenty-odd
    of those. That keeps the saving where the count is without touching the
    image anyone actually looks at.

    `grid` covers the browsable poster/backdrop lists, which are the worst case
    for memory because TMDb returns a hundred or more of them. Those list items
    also carry the original URL in a property, so opening one full screen still
    shows full quality -- see FullScreenImage in main.py.
"""
IMAGE_SIZES = {
    "poster": "w780",
    "backdrop": "w1280",
    "backdrop_hero": "original",
    "profile": "h632",
    "thumb": "w185",
    "still": "w300",
    "logo": "w300",
    "grid": "w500",
    "original": "original",
}


def image_url(path, kind="poster"):
    """Absolute URL for a TMDb image path, at the size `kind` calls for.

    Returns '' for a missing path, which is what every call site wants -- TMDb
    uses null rather than omitting the key.
    """
    if not path:
        return ""

    return IMAGE_BASE + IMAGE_SIZES.get(kind, "original") + path


########################


def tmdb_query(
    action,
    call=None,
    get=None,
    get2=None,
    get3=None,
    get4=None,
    params=None,
    use_language=True,
    language=None,
    show_error=False,
):
    urlargs = {}
    urlargs["api_key"] = tmdb_api_key()

    if use_language:
        urlargs["language"] = language if language is not None else language_code()

    if params:
        urlargs.update(params)

    url = urljoin(API_URL, action, call, get, get2, get3, get4)
    url = "{0}?{1}".format(url, urlencode(urlargs))

    try:
        request = None

        for i in range(1, 4):  # loop if heavy server load
            try:
                request = SESSION.get(url, timeout=HTTP_TIMEOUT)

                if str(request.status_code).startswith("5"):
                    raise Exception(str(request.status_code))
                else:
                    break

            except Exception:
                xbmc.sleep(500)

        if not request or request.status_code == 404:
            error = ADDON.getLocalizedString(32019)
            raise Exception(error)

        elif request.status_code == 401:
            error = ADDON.getLocalizedString(32022)
            raise Exception(error)

        elif not request.ok:
            raise Exception("Code " + str(request.status_code))

        result = request.json()

        if show_error:
            """Report "nothing found" when the response is empty.

            Upstream's second clause read `not len(result["results"]) == 0`
            -- true when results *were* returned -- so any successful search
            raised the not-found error and popped an OK dialog at the user,
            while a genuinely empty result set passed silently. The negation
            is dropped here.
            """
            if len(result) == 0 or (
                "results" in result and len(result["results"]) == 0
            ):
                error = ADDON.getLocalizedString(32019)
                raise Exception(error)

        return result

    except Exception as error:
        log("%s --> %s" % (error, url), ERROR)
        if show_error:
            tmdb_error(error)


def tmdb_search(call, query, year=None, include_adult="false"):
    if call == "person":
        params = {"query": query, "include_adult": include_adult}

    elif call == "movie":
        params = {"query": query, "year": year, "include_adult": include_adult}

    elif call == "tv":
        params = {"query": query, "first_air_date_year": year}

    else:
        return ""

    result = tmdb_query(action="search", call=call, params=params)

    try:
        result = result.get("results")

        if not result:
            raise Exception

        return result

    except Exception:
        tmdb_error(ADDON.getLocalizedString(32019))


def tmdb_find(call, external_id, error_check=True):
    if external_id.startswith("tt"):
        external_source = "imdb_id"
    else:
        external_source = "tvdb_id"

    """ This is the first thing that happens when the dialog is opened from a
        library item, and it was a TMDb round trip every single time: the
        title's own payload is cached by tmdb_id, but nothing cached the lookup
        that produces the tmdb_id. Measured at ~66 ms per open on a desktop
        connection, before anything is drawn.

        An IMDb or TVDb id maps to a TMDb id permanently, so this is about as
        cacheable as data gets. Keyed by the id alone rather than by call, so
        one entry answers both the movie and the tv question.
    """
    cache_key = "find" + str(external_id)
    result = get_cache(cache_key)

    if not result:
        result = tmdb_query(
            action="find",
            call=str(external_id),
            params={"external_source": external_source},
            use_language=False,
            show_error=True,
        )

        write_cache(cache_key, result)
    try:
        if call == "movie":
            return result.get("movie_results")
        else:
            return result.get("tv_results")

    except AttributeError:
        return


def tmdb_select_dialog(list, call):
    indexlist = []
    selectionlist = []

    if call == "person":
        default_img = "DefaultActor.png"
        img = "profile_path"
        label = "name"
        label2 = ""

    elif call == "movie":
        default_img = "DefaultVideo.png"
        img = "poster_path"
        label = "title"
        label2 = 'tmdb_get_year(item.get("release_date", ""))'

    elif call == "tv":
        default_img = "DefaultVideo.png"
        img = "poster_path"
        label = "name"
        label2 = "first_air_date"
        label2 = 'tmdb_get_year(item.get("first_air_date", ""))'

    else:
        return

    index = 0
    for item in list:
        icon = image_url(item[img], "thumb")
        list_item = xbmcgui.ListItem(item[label])
        list_item.setArt({"icon": default_img, "thumb": icon})

        try:
            list_item.setLabel2(str(eval(label2)))
        except Exception:
            pass

        selectionlist.append(list_item)
        indexlist.append(index)
        index += 1

    busydialog(close=True)

    selected = DIALOG.select(
        xbmc.getLocalizedString(424), selectionlist, useDetails=True
    )

    if selected == -1:
        return -1

    busydialog()

    return indexlist[selected]


def tmdb_select_dialog_small(list):
    indexlist = []
    selectionlist = []

    index = 0
    for item in list:
        list_item = xbmcgui.ListItem(item)
        selectionlist.append(list_item)
        indexlist.append(index)
        index += 1

    busydialog(close=True)

    selected = DIALOG.select(
        xbmc.getLocalizedString(424), selectionlist, useDetails=False
    )

    if selected == -1:
        return -1

    busydialog()

    return indexlist[selected]


def tmdb_calc_age(birthday, deathday=None):
    if deathday is not None:
        ref_day = deathday.split("-")

    elif birthday:
        date = datetime.date.today()
        ref_day = [date.year, date.month, date.day]

    else:
        return ""

    born = birthday.split("-")
    age = int(ref_day[0]) - int(born[0])

    if len(born) > 1:
        diff_months = int(ref_day[1]) - int(born[1])
        diff_days = int(ref_day[2]) - int(born[2])

        if diff_months < 0 or (diff_months == 0 and diff_days < 0):
            age -= 1

    return age


def is_below_rating(item):
    """Whether TheMovieDB rates this item below the threshold the user set.

    Lives here rather than in person.py because every list asks it: person
    credits, similar titles and collection members all carry the same two
    fields.

    Something nobody has voted on is kept. TMDb reports an unrated item as
    vote_average 0.0, which is indistinguishable from a genuinely terrible
    score unless vote_count is read too -- and hiding everything unreleased the
    moment the slider leaves zero is not what "hide items rated below" means.
    The same reasoning as an unknown date in is_posthumous: no evidence is not
    evidence.
    """
    minimum = filter_rating()

    if minimum <= 0:
        return False

    if not (item.get("vote_count") or 0):
        return False

    rating = item.get("vote_average")

    """ A missing or unreadable score is not a low one. `or 0` here would turn
        None into 0.0 and hide the item, which is the same mistake as treating
        an unrated title as terrible.
    """
    if rating is None:
        return False

    try:
        return float(rating) < minimum

    except (TypeError, ValueError):
        return False


def tmdb_error(message=ADDON.getLocalizedString(32019)):
    busydialog(close=True)
    DIALOG.ok(ADDON.getLocalizedString(32000), str(message))


def tmdb_studios(list_item, item, key):
    if key == "production":
        key_name = "production_companies"
        prop_name = "studio"
    elif key == "network":
        key_name = "networks"
        prop_name = "network"
    else:
        return

    i = 0
    for studio in item[key_name]:
        icon = image_url(studio["logo_path"], "logo")
        if icon:
            list_item.setProperty(prop_name + "." + str(i), studio["name"])
            list_item.setProperty(prop_name + ".icon." + str(i), icon)
            i += 1


def tmdb_check_localdb(local_items, title, originaltitle, year, imdbnumber=False):
    """Link a TMDb item to the local library row for it, if there is one.

    `local_items` is normally a LocalIndex. A plain list is still accepted and
    wrapped, because the widget and nextaired paths hand over the raw lists
    from get_local_media(); wrapping a list that is then used once is no worse
    than the scan it replaces.
    """
    local = {
        "dbid": -1,
        "playcount": 0,
        "watchedepisodes": "",
        "episodes": "",
        "unwatchedepisodes": "",
        "file": "",
    }

    if not local_items:
        return local

    if not isinstance(local_items, LocalIndex):
        local_items = LocalIndex(local_items)

    item = local_items.find(title, originaltitle, tmdb_get_year(year), imdbnumber)

    if item is None:
        return local

    dbid = item["dbid"]
    playcount = item["playcount"]
    episodes = item.get("episodes", "")
    watchedepisodes = item.get("watchedepisodes", "")
    file = item.get("file", "")

    local["dbid"] = dbid
    local["file"] = file
    local["playcount"] = playcount
    local["episodes"] = episodes
    local["watchedepisodes"] = watchedepisodes
    local["unwatchedepisodes"] = episodes - watchedepisodes if episodes else ""

    return local


def tmdb_handle_person(item):
    if item.get("gender") == 2:
        gender = "male"
    elif item.get("gender") == 1:
        gender = "female"
    else:
        gender = ""

    icon = image_url(item["profile_path"], "profile")
    list_item = xbmcgui.ListItem(label=item["name"])
    list_item.setProperty("birthyear", date_year(item.get("birthday", "")))
    list_item.setProperty("birthday", date_format(item.get("birthday", "")))
    list_item.setProperty("deathyear", date_year(item.get("deathday", "")))
    list_item.setProperty("deathday", date_format(item.get("deathday", "")))
    list_item.setProperty(
        "age", str(tmdb_calc_age(item.get("birthday", ""), item.get("deathday")))
    )
    list_item.setProperty("biography", tmdb_fallback_info(item, "biography"))
    list_item.setProperty(
        "place_of_birth",
        item.get("place_of_birth").strip() if item.get("place_of_birth") else "",
    )
    list_item.setProperty("known_for_department", item.get("known_for_department", ""))
    list_item.setProperty("gender", gender)
    list_item.setProperty("id", str(item.get("id", "")))
    list_item.setProperty("call", "person")
    list_item.setArt({"icon": "DefaultActor.png", "thumb": icon, "poster": icon})

    return list_item


def tmdb_handle_movie(item, local_items=None, full_info=False, mediatype="movie"):
    icon = image_url(item["poster_path"], "poster")
    backdrop = image_url(
        item["backdrop_path"], "backdrop_hero" if full_info else "backdrop"
    )

    label = item["title"] or item["original_title"]
    originaltitle = item.get("original_title", "")
    imdbnumber = item.get("imdb_id", "")
    collection = item.get("belongs_to_collection", "")
    duration = item.get("runtime") * 60 if item.get("runtime", 0) > 0 else ""

    premiered = item.get("release_date")
    if premiered in ["2999-01-01", "1900-01-01"]:
        premiered = ""

    local_info = tmdb_check_localdb(
        local_items, label, originaltitle, premiered, imdbnumber
    )
    dbid = local_info["dbid"]
    is_local = True if dbid > 0 else False

    list_item = xbmcgui.ListItem(label=label)
    list_item.setInfo(
        "video",
        {
            "title": label,
            "originaltitle": originaltitle,
            "dbid": dbid,
            "playcount": local_info["playcount"],
            "imdbnumber": imdbnumber,
            "rating": item.get("vote_average", ""),
            "votes": item.get("vote_count", ""),
            "premiered": premiered,
            "mpaa": tmdb_get_cert(item),
            "tagline": item.get("tagline", ""),
            "duration": duration,
            "status": item.get("status", ""),
            "plot": tmdb_fallback_info(item, "overview"),
            "director": tmdb_join_items_by(
                item.get("crew", ""), key_is="job", value_is="Director"
            ),
            "writer": tmdb_join_items_by(
                item.get("crew", ""), key_is="department", value_is="Writing"
            ),
            "country": tmdb_join_items(item.get("production_countries", "")),
            "genre": tmdb_join_items(item.get("genres", "")),
            "studio": tmdb_join_items(item.get("production_companies", "")),
            "mediatype": mediatype,
        },
    )
    list_item.setArt(
        {"icon": "DefaultVideo.png", "thumb": icon, "poster": icon, "fanart": backdrop}
    )
    list_item.setProperty("role", item.get("character", ""))
    list_item.setProperty("budget", format_currency(item.get("budget")))
    list_item.setProperty("revenue", format_currency(item.get("revenue")))
    list_item.setProperty("homepage", item.get("homepage", ""))
    list_item.setProperty("file", local_info.get("file", ""))
    list_item.setProperty("id", str(item.get("id", "")))
    list_item.setProperty("call", "movie")

    if full_info:
        tmdb_studios(list_item, item, "production")
        omdb_properties(list_item, imdbnumber)

        region_release = tmdb_get_region_release(item)
        if premiered != region_release:
            list_item.setProperty("region_release", date_format(region_release))

        if collection:
            list_item.setProperty("collection", collection["name"])
            list_item.setProperty("collection_id", str(collection["id"]))
            list_item.setProperty(
                "collection_poster",
                (image_url(collection["poster_path"], "poster")),
            )
            list_item.setProperty(
                "collection_fanart",
                (image_url(collection["backdrop_path"], "backdrop_hero")),
            )

    return list_item, is_local


def tmdb_handle_tvshow(item, local_items=None, full_info=False, mediatype="tvshow"):
    icon = image_url(item["poster_path"], "poster")
    backdrop = image_url(
        item["backdrop_path"], "backdrop_hero" if full_info else "backdrop"
    )

    label = item["name"] or item["original_name"]
    originaltitle = item.get("original_name", "")
    imdbnumber = item["external_ids"]["imdb_id"] if item.get("external_ids") else ""
    next_episode = item.get("next_episode_to_air", "")
    last_episode = item.get("last_episode_to_air", "")
    tvdb_id = item["external_ids"]["tvdb_id"] if item.get("external_ids") else ""

    premiered = item.get("first_air_date")
    if premiered in ["2999-01-01", "1900-01-01"]:
        premiered = ""

    local_info = tmdb_check_localdb(
        local_items, label, originaltitle, premiered, tvdb_id
    )
    dbid = local_info["dbid"]
    is_local = True if dbid > 0 else False

    list_item = xbmcgui.ListItem(label=label)
    list_item.setInfo(
        "video",
        {
            "title": label,
            "originaltitle": originaltitle,
            "dbid": dbid,
            "playcount": local_info["playcount"],
            "status": item.get("status", ""),
            "rating": item.get("vote_average", ""),
            "votes": item.get("vote_count", ""),
            "imdbnumber": imdbnumber,
            "premiered": premiered,
            "mpaa": tmdb_get_cert(item),
            "season": str(item.get("number_of_seasons", "")),
            "episode": str(item.get("number_of_episodes", "")),
            "plot": tmdb_fallback_info(item, "overview"),
            "director": tmdb_join_items(item.get("created_by", "")),
            "genre": tmdb_join_items(item.get("genres", "")),
            "studio": tmdb_join_items(item.get("networks", "")),
            "mediatype": mediatype,
        },
    )
    list_item.setArt(
        {"icon": "DefaultVideo.png", "thumb": icon, "poster": icon, "fanart": backdrop}
    )
    list_item.setProperty("TotalEpisodes", str(local_info["episodes"]))
    list_item.setProperty("WatchedEpisodes", str(local_info["watchedepisodes"]))
    list_item.setProperty("UnWatchedEpisodes", str(local_info["unwatchedepisodes"]))
    list_item.setProperty("homepage", item.get("homepage", ""))
    list_item.setProperty("role", item.get("character", ""))
    list_item.setProperty("tvdb_id", str(tvdb_id))
    list_item.setProperty("id", str(item.get("id", "")))
    list_item.setProperty("call", "tv")

    if full_info:
        tmdb_studios(list_item, item, "production")
        tmdb_studios(list_item, item, "network")
        omdb_properties(list_item, imdbnumber)

        if last_episode:
            list_item.setProperty("lastepisode", last_episode.get("name"))
            list_item.setProperty("lastepisode_plot", last_episode.get("overview"))
            list_item.setProperty(
                "lastepisode_number", str(last_episode.get("episode_number"))
            )
            list_item.setProperty(
                "lastepisode_season", str(last_episode.get("season_number"))
            )
            list_item.setProperty(
                "lastepisode_date", date_format(last_episode.get("air_date"))
            )
            list_item.setProperty(
                "lastepisode_thumb",
                (image_url(last_episode["still_path"], "still")),
            )

        if next_episode:
            list_item.setProperty("nextepisode", next_episode.get("name"))
            list_item.setProperty("nextepisode_plot", next_episode.get("overview"))
            list_item.setProperty(
                "nextepisode_number", str(next_episode.get("episode_number"))
            )
            list_item.setProperty(
                "nextepisode_season", str(next_episode.get("season_number"))
            )
            list_item.setProperty(
                "nextepisode_date", date_format(next_episode.get("air_date"))
            )
            list_item.setProperty(
                "nextepisode_thumb",
                (image_url(next_episode["still_path"], "still")),
            )

    return list_item, is_local


def tmdb_handle_season(item, tvshow_details, full_info=False):
    backdrop = image_url(tvshow_details["backdrop_path"], "backdrop_hero")
    icon = image_url(item["poster_path"], "poster")
    if not icon and tvshow_details["poster_path"]:
        icon = image_url(tvshow_details["poster_path"], "poster")

    imdbnumber = (
        tvshow_details["external_ids"]["imdb_id"]
        if tvshow_details.get("external_ids")
        else ""
    )
    season_nr = str(item.get("season_number", ""))
    tvshow_label = tvshow_details["name"] or tvshow_details["original_name"]

    episodes_count = len(item.get("episodes") or [])

    list_item = xbmcgui.ListItem(label=tvshow_label)
    list_item.setInfo(
        "video",
        {
            "title": item["name"],
            "tvshowtitle": tvshow_label,
            "premiered": item.get("air_date", ""),
            "episode": episodes_count,
            "season": season_nr,
            "plot": item.get("overview", ""),
            "genre": tmdb_join_items(tvshow_details.get("genres", "")),
            "rating": tvshow_details.get("vote_average", ""),
            "votes": tvshow_details.get("vote_count", ""),
            "mpaa": tmdb_get_cert(tvshow_details),
            "mediatype": "season",
        },
    )
    list_item.setArt(
        {"icon": "DefaultVideo.png", "thumb": icon, "poster": icon, "fanart": backdrop}
    )
    list_item.setProperty("TotalEpisodes", str(episodes_count))
    list_item.setProperty("id", str(tvshow_details["id"]))
    list_item.setProperty("call", "tv")
    list_item.setProperty("call_season", season_nr)

    if full_info:
        tmdb_studios(list_item, tvshow_details, "production")
        tmdb_studios(list_item, tvshow_details, "network")
        omdb_properties(list_item, imdbnumber)

    return list_item


def tmdb_fallback_info(item, key):
    if FALLBACK_LANGUAGE == language_code():
        try:
            key_value = item.get(key, "").replace("&amp;", "&").strip()
        except Exception:
            key_value = ""

    else:
        key_value = tmdb_get_translation(item, key, language_code())

    # Default language is empty in the translations dict? Fall back to EN
    if not key_value:
        key_value = tmdb_get_translation(item, key, FALLBACK_LANGUAGE)

    return key_value


def tmdb_get_translation(item, key, language):
    key_value_iso_639_1 = ""
    try:
        language_iso_639_1 = language[:2]
        language_iso_3166_1 = language[3:] if len(language) > 3 else None

        for translation in item["translations"]["translations"]:
            if (
                translation.get("iso_639_1") == language_iso_639_1
                and translation["data"][key]
            ):
                key_value = translation["data"][key]
                if key_value:
                    key_value = key_value.replace("&amp;", "&").strip()
                    if (
                        not language_iso_3166_1
                        or language_iso_3166_1 == translation.get("iso_3166_1")
                    ):
                        return key_value
                    else:
                        key_value_iso_639_1 = key_value
    except Exception:
        pass

    return key_value_iso_639_1


def tmdb_handle_images(item):
    """One entry in the browsable poster/backdrop grid.

    These are the worst case for memory in the whole add-on: TMDb happily
    returns well over a hundred images for a popular title, and upstream gave
    every one of them an `original` URL, so simply opening the images tab
    uploaded a hundred full-resolution textures.

    The grid gets a modest thumbnail and the original is carried alongside in
    `fullsize`, which FullScreenImage reads when one is actually opened. Full
    quality is then paid for one image at a time instead of all of them at once.
    """
    icon = image_url(item["file_path"], "grid")
    list_item = xbmcgui.ListItem(
        label=str(item["width"]) + "x" + str(item["height"]) + "px"
    )
    list_item.setArt({"icon": "DefaultPicture.png", "thumb": icon})
    list_item.setProperty("call", "image")
    list_item.setProperty("fullsize", image_url(item["file_path"], "original"))

    return list_item


def tmdb_handle_credits(item):
    icon = image_url(item["profile_path"], "thumb")
    list_item = xbmcgui.ListItem(label=item["name"])
    list_item.setLabel2(item["label2"])
    list_item.setArt({"icon": "DefaultActor.png", "thumb": icon, "poster": icon})
    list_item.setProperty("id", str(item.get("id", "")))
    list_item.setProperty("call", "person")

    return list_item


def tmdb_handle_yt_videos(item):
    icon = "https://img.youtube.com/vi/%s/0.jpg" % str(item["key"])
    list_item = xbmcgui.ListItem(label=item["name"])
    list_item.setLabel2(item.get("type", ""))
    list_item.setArt({"icon": "DefaultVideo.png", "thumb": icon, "landscape": icon})
    list_item.setProperty("ytid", str(item["key"]))
    list_item.setProperty("call", "youtube")

    return list_item


def tmdb_join_items_by(item, key_is, value_is, key="name"):
    values = []
    for value in item:
        if value[key_is] == value_is:
            values.append(value[key])

    return get_joined_items(values)


def tmdb_join_items(item, key="name"):
    values = []
    for value in item:
        values.append(value[key])

    return get_joined_items(values)


def tmdb_get_year(item):
    try:
        year = str(item)[:-6]
        return year
    except Exception:
        return ""


def tmdb_get_region_release(item):
    try:
        for release in item["release_dates"]["results"]:
            if release["iso_3166_1"] == country_code():
                date = release["release_dates"][0]["release_date"]
                return date[:-14]

    except Exception:
        return ""


def tmdb_get_cert(item):
    prefix = "FSK " if country_code() == "DE" else ""
    mpaa = ""
    mpaa_fallback = ""

    if item.get("content_ratings"):
        for cert in item["content_ratings"]["results"]:
            if cert["iso_3166_1"] == country_code():
                mpaa = cert["rating"]
                break
            elif cert["iso_3166_1"] == "US":
                mpaa_fallback = cert["rating"]

    elif item.get("release_dates"):
        for cert in item["release_dates"]["results"]:
            if cert["iso_3166_1"] == country_code():
                mpaa = cert["release_dates"][0]["certification"]
                break
            elif cert["iso_3166_1"] == "US":
                mpaa_fallback = cert["release_dates"][0]["certification"]

    if mpaa:
        return prefix + mpaa

    return mpaa_fallback


def omdb_properties(list_item, imdbnumber):
    if omdb_api_key() and imdbnumber:
        omdb = omdb_api(imdbnumber)
        if omdb:
            list_item.setProperty("rating.metacritic", omdb.get("metacritic", ""))
            list_item.setProperty(
                "rating.rotten", omdb.get("tomatometerallcritics", "")
            )
            list_item.setProperty(
                "rating.rotten_avg", omdb.get("tomatometerallcritics_avg", "")
            )
            list_item.setProperty(
                "votes.rotten", omdb.get("tomatometerallcritics_votes", "")
            )
            list_item.setProperty(
                "rating.rotten_user", omdb.get("tomatometerallaudience", "")
            )
            list_item.setProperty(
                "rating.rotten_user_avg", omdb.get("tomatometerallaudience_avg", "")
            )
            list_item.setProperty(
                "votes.rotten_user", omdb.get("tomatometerallaudience_votes", "")
            )
            list_item.setProperty("rating.imdb", omdb.get("imdbRating", ""))
            list_item.setProperty("votes.imdb", omdb.get("imdbVotes", ""))
            list_item.setProperty("awards", omdb.get("awards", ""))
            list_item.setProperty("release", omdb.get("DVD", ""))
