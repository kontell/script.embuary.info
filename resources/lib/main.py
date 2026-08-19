#!/usr/bin/python
# coding: utf-8

########################

import gc
import sys
import xbmc
import xbmcgui

from collections import OrderedDict

from resources.lib.helper import *
from resources.lib.tmdb import *
from resources.lib.person import *
from resources.lib.video import *
from resources.lib.season import *
from resources.lib.localdb import *

########################

""" How many visited pages stay instantiated at once.

    Every live dialog holds its whole page of artwork as GPU textures, so this
    is the knob that decides the add-on's ceiling rather than a tuning
    preference. Measured on an Android TV box (Kodi 22, Mali GPU), one movie
    page costs roughly 140 MB of GPU memory and 200 MB of process RSS.

    Upstream kept every page forever, in three places at once -- dialog_cache,
    window_stack, and the frames of a dialog_manager that recursed instead of
    looping. Seven pages measured 983 MB of GPU memory against a 46 MB idle
    baseline, and 2.1 GB RSS against 582 MB; the kernel OOM killer took Kodi at
    nine pages. Three keeps instant Back for the pages a user actually bounces
    between while leaving the ceiling under half a gigabyte.
"""
MAX_LIVE_DIALOGS = 3


class TheMovieDB(object):
    def __init__(self, call, params):
        self.monitor = xbmc.Monitor()
        """ Navigation history holds (call, tmdb_id, season) descriptors, not
            live dialogs. Depth is then free: an hour of browsing costs a list
            of tuples. Going back re-derives the page, which normally needs no
            network because the TMDb response is still in simplecache.
        """
        self.history = []
        """ Live dialogs, most-recently-used last via move_to_end, bounded by
            trim_cache to MAX_LIVE_DIALOGS.
        """
        self.dialog_cache = OrderedDict()
        self.call = call
        self.tmdb_id = params.get("tmdb_id")
        self.season = params.get("season")
        self.query = remove_quotes(params.get("query"))
        self.query_year = params.get("year")
        self.exact_search = True if params.get("exact") == "true" else False
        self.external_id = params.get("external_id")
        self.dbid = params.get("dbid")

        if self.call == "tv":
            self.dbtype = "tvshow"
        elif self.call == "movie":
            self.dbtype = "movie"

        winprop("script.embuary.info-language_code", language_code())
        winprop("script.embuary.info-country_code", country_code())

        busydialog()

        if self.dbid and self.dbtype:
            self.tmdb_id = self.find_id(method="dbid")
        elif self.external_id:
            self.tmdb_id = self.find_id(method="external_id")
        elif self.query:
            self.tmdb_id = self.find_id(method="query")

        if self.tmdb_id:
            self.call_params = {}

            """ Index the local library once per run rather than re-scanning it
                for every item on every page. call_params is reused for the
                whole session, so this cost is paid once no matter how far the
                user browses.
            """
            local_media = get_local_media()
            self.call_params["local_shows"] = LocalIndex(local_media["shows"])
            self.call_params["local_movies"] = LocalIndex(local_media["movies"])

            self.entry_point()

        busydialog(close=True)

    """ Search for tmdb_id based one a query string or external ID (IMDb or TVDb)
    """

    def find_id(self, method):
        if method == "dbid":
            method_details = "VideoLibrary.Get%sDetails" % self.dbtype
            param = "%sid" % self.dbtype
            key_details = "%sdetails" % self.dbtype

            dbinfo = json_call(
                method_details,
                properties=["uniqueid", "year", "title"],
                params={param: int(self.dbid)},
            )
            try:
                dbinfo = dbinfo["result"][key_details]
            except KeyError:
                return

            uniqueid = dbinfo.get("uniqueid", {})

            result = None
            for item in uniqueid:
                if uniqueid[item].startswith("tt"):
                    result = tmdb_find(self.call, uniqueid[item])
                    break

                elif (
                    self.dbtype == "tvshow"
                    and item.lower() == "tvdb"
                    and uniqueid[item]
                ):
                    result = tmdb_find(self.call, uniqueid[item])
                    break

            if not result:
                self.query = dbinfo.get("title")
                self.query_year = dbinfo.get("year", "")

                tmdb_id = self.find_id(method="query")
                return tmdb_id

        elif method == "external_id":
            result = tmdb_find(self.call, self.external_id)

            if not result and self.query:
                self.find_id(method="query")

        elif method == "query":
            if " / " in self.query:
                query_values = self.query.split(" / ")
                position = tmdb_select_dialog_small(query_values)
                if position < 0:
                    return ""
                else:
                    self.query = query_values[position]

            """ A name only has to be resolved once.

                Kodi stores no external id for a person -- its actor table is
                (actor_id, name, art_urls) and its uniqueid table holds nothing
                but movies, shows and episodes -- so every visit to an actor
                starts from a bare name and pays a TMDb search to turn it back
                into an id. Remembering the answer skips that round trip, and
                means the genuinely ambiguous names left over after
                unambiguous_person are asked about once rather than every time.

                Person only. A film search is qualified by year and two films
                really can share a title, so there the dialog is doing useful
                work and a cached answer would suppress it wrongly.
            """
            if self.call == "person":
                cached_id = get_cache(person_id_cache_key(self.query))

                if cached_id:
                    return cached_id

            result = tmdb_search(self.call, self.query, self.query_year)

            if self.exact_search:
                exact_results = []

                for item in result:
                    title = item.get("title") or item.get("name") or ""
                    original_title = (
                        item.get("original_title") or item.get("original_name") or ""
                    )

                    if (
                        title.lower() == self.query.lower()
                        or original_title.lower() == self.query.lower()
                    ):
                        if self.query_year:
                            premiered = (
                                item.get("first_air_date", "")
                                if self.call == "tv"
                                else item.get("release_date", "")
                            )
                            if self.query_year == premiered[:-6]:
                                exact_results.append(item)
                        else:
                            exact_results.append(item)

                if exact_results:
                    result = exact_results
                else:
                    return ""

        try:
            if len(result) > 1:
                """TMDb's person search is fuzzy and its database is full of
                near-duplicate stubs, so a plain cast name returns more than
                one result about a third of the time -- and the extras are
                almost always noise nobody would pick. Skip the dialog when
                the intended person is obvious; see unambiguous_person.

                Only for people. Two films really can share a title, and
                there the dialog is doing useful work.
                """
                chosen = None

                if self.call == "person" and skip_person_dialog():
                    chosen = unambiguous_person(result, self.query)

                if chosen is not None:
                    position = result.index(chosen)
                else:
                    position = tmdb_select_dialog(result, self.call)
                    if position < 0:
                        raise Exception
            else:
                position = 0

            tmdb_id = result[position]["id"]

        except Exception:
            return ""

        """ Remember it, however it was decided. Caching the user's own choice
            is the point rather than a side effect: it is what stops the two or
            three names a real library cannot resolve automatically from asking
            again on every visit. The cost is that a misclick sticks until the
            entry expires or caching is turned off.
        """
        if method == "query" and self.call == "person" and self.query:
            write_cache(person_id_cache_key(self.query), tmdb_id)

        return tmdb_id

    """ Collect all data by the tmdb_id and build the dialogs.
    """

    def entry_point(self):
        dialog = self.build_dialog()

        if dialog:
            self.dialog_manager(dialog)
        else:
            self.quit()

    @staticmethod
    def request_key(call, tmdb_id, season):
        """Cache key for one page.

        Upstream had two spellings of this and they disagreed: entry_point
        wrote `call + id` while dialog_manager looked up `call + id + season`,
        so every season page missed its own cache entry and was refetched.
        """
        return "%s%s%s" % (call, tmdb_id, season if season else "")

    def build_dialog(self):
        """Fetch the data for the current call/tmdb_id/season and build its dialog.

        Returns the dialog, or None when TMDb had nothing. Deliberately does
        not open it -- dialog_manager owns the navigation loop, so that
        building a page never nests inside showing one.
        """
        self.call_params["call"] = self.call
        self.call_params["tmdb_id"] = self.tmdb_id
        self.call_params["season"] = self.season

        busydialog()

        dialog = None
        if self.call == "person":
            dialog = self.fetch_person()
        elif self.call == "tv" and self.season:
            dialog = self.fetch_season()
        elif self.call == "movie" or self.call == "tv":
            dialog = self.fetch_video()

        busydialog(close=True)

        if dialog:
            self.cache_dialog(
                self.request_key(self.call, self.tmdb_id, self.season), dialog
            )

        return dialog

    def cache_dialog(self, key, dialog):
        self.dialog_cache[key] = dialog
        self.dialog_cache.move_to_end(key)
        self.trim_cache()

    def trim_cache(self):
        """Drop the least recently used dialogs past MAX_LIVE_DIALOGS.

        Nothing is closed here -- an evicted dialog is not on screen. What
        matters is that no Python reference survives, because the C++
        CGUIWindow and every texture its controls hold stay alive until the
        binding object is collected.

        The explicit collect is not decoration. Eviction happens while a modal
        loop is running, so without it the free waits for whenever CPython next
        runs a generational sweep -- which on an idle dialog can be a long time,
        and the whole point is to hand the memory back before the next page
        allocates its own.
        """
        evicted = False
        while len(self.dialog_cache) > MAX_LIVE_DIALOGS:
            self.dialog_cache.popitem(last=False)
            evicted = True

        if evicted:
            gc.collect()

    def fetch_person(self):
        data = TMDBPersons(self.call_params)
        if not data["person"]:
            return

        dialog = DialogPerson(
            "script-embuary-person.xml",
            ADDON_PATH,
            "default",
            "1080i",
            person=data["person"],
            movies=data["movies"],
            tvshows=data["tvshows"],
            combined=data["combined"],
            images=data["images"],
            tmdb_id=self.tmdb_id,
        )
        return dialog

    def fetch_video(self):
        data = TMDBVideos(self.call_params)
        if not data["details"]:
            return

        dialog = DialogVideo(
            "script-embuary-video.xml",
            ADDON_PATH,
            "default",
            "1080i",
            details=data["details"],
            cast=data["cast"],
            crew=data["crew"],
            similar=data["similar"],
            youtube=data["youtube"],
            backdrops=data["backdrops"],
            posters=data["posters"],
            collection=data["collection"],
            seasons=data["seasons"],
            tmdb_id=self.tmdb_id,
        )
        return dialog

    def fetch_season(self):
        data = TMDBSeasons(self.call_params)
        if not data["details"]:
            return

        dialog = DialogSeason(
            "script-embuary-video.xml",
            ADDON_PATH,
            "default",
            "1080i",
            details=data["details"],
            cast=data["cast"],
            gueststars=data["gueststars"],
            posters=data["posters"],
            tmdb_id=self.tmdb_id,
        )
        return dialog

    """ Dialog handler. Owns the navigation history and keeps the script alive
        for as long as a page is on screen.
    """

    def dialog_manager(self, dialog):
        """Show pages until the user leaves the add-on.

        This is a loop, and that is the point. Upstream recursed: each step
        called dialog_manager() or entry_point() from inside the frame of the
        page you were leaving, so every page ever visited stayed reachable from
        the Python stack -- pinned there even if the caches around it were
        trimmed. Depth was bounded only by CPython's recursion limit and, in
        practice, by the OS killing Kodi first.
        """
        while dialog is not None and not self.monitor.abortRequested():
            dialog.doModal()

            try:
                next_id = dialog["id"]
                next_call = dialog["call"]
                next_season = dialog["season"]
            except KeyError:
                # doModal returned without any dialog setting an action. Every
                # exit path in the three dialog classes sets all three keys
                # together, so this means something unforeseen -- leave.
                break

            if next_call == "youtube":
                self.wait_for_playback()
                """ Reopen the same page. The action has to be cleared first or
                    the next pass reads this same 'youtube' and spins forever;
                    upstream never hit that because its recursive call did not
                    return.
                """
                dialog.action.clear()
                continue

            if next_call == "back":
                dialog = self.previous_dialog()
                continue

            if next_call == "close" or not next_id or not next_call:
                break

            """ Remember the page being left as a descriptor, then move on. The
                dialog itself is released once it falls out of dialog_cache.
            """
            self.history.append((self.call, self.tmdb_id, self.season))
            dialog = self.dialog_for(next_call, next_id, next_season)

        self.quit()

    def dialog_for(self, call, tmdb_id, season):
        """The dialog for one page, from cache if it is still live."""
        self.call = call
        self.tmdb_id = tmdb_id
        self.season = season

        key = self.request_key(call, tmdb_id, season)
        cached = self.dialog_cache.get(key)

        if cached is not None:
            self.dialog_cache.move_to_end(key)
            return cached

        return self.build_dialog()

    def previous_dialog(self):
        """Step back through the history until a page can be shown."""
        while self.history:
            call, tmdb_id, season = self.history.pop()
            dialog = self.dialog_for(call, tmdb_id, season)

            if dialog is not None:
                return dialog

        return None

    def wait_for_playback(self):
        while (
            condition(
                "Player.HasMedia | Window.IsVisible(busydialog) | Window.IsVisible(busydialognocancel) | Window.IsVisible(okdialog)"
            )
            and not self.monitor.abortRequested()
        ):
            self.monitor.waitForAbort(1)

    def quit(self):
        """Release every page before handing back to Kodi.

        Upstream deleted the attributes, which was not the same thing: the
        dialogs were still reachable from the recursion, so what actually
        freed them was the interpreter being torn down. Clearing the
        containers here is what makes the release happen while the script
        is still the one holding the references.
        """
        self.history = []
        self.dialog_cache.clear()
        gc.collect()
        quit()


""" Person dialog
"""


class DialogPerson(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        self.first_load = True
        self.action = {}

        self.tmdb_id = kwargs["tmdb_id"]
        self.person = kwargs["person"]
        self.movies = kwargs["movies"]
        self.tvshows = kwargs["tvshows"]
        self.combined = kwargs["combined"]
        self.images = kwargs["images"]

    def __getitem__(self, key):
        return self.action[key]

    def __setitem__(self, key, value):
        self.action[key] = value

    def onInit(self):
        execute("ClearProperty(script.embuary.info-nextcall,home)")

        if self.first_load:
            self.add_items()

    def add_items(self):
        self.first_load = False

        index = 10051
        li = [self.person, self.movies, self.tvshows, self.images, self.combined]

        for items in li:
            try:
                clist = self.getControl(index)
                clist.addItems(items)
            except RuntimeError as error:
                log(
                    "Control with id %s cannot be filled. Error --> %s"
                    % (str(index), error),
                    DEBUG,
                )
                pass
            index += 1

    def onAction(self, action):
        if action.getId() in [92, 10]:
            self.action["id"] = ""
            self.action["season"] = ""
            self.action["call"] = "back" if action.getId() == 92 else "close"
            self.quit()

    def onClick(self, controlId):
        next_id = xbmc.getInfoLabel("Container(%s).ListItem.Property(id)" % controlId)
        next_call = xbmc.getInfoLabel(
            "Container(%s).ListItem.Property(call)" % controlId
        )

        if next_call in ["person", "movie", "tv"] and next_id:
            self.action["id"] = next_id
            self.action["call"] = next_call
            self.action["season"] = ""
            self.quit()

        elif next_call == "image":
            FullScreenImage(controlId)

    def quit(self):
        close_action = self.getProperty("onclose")
        onnext_action = self.getProperty("onnext")
        onback_action = self.getProperty("onback_%s" % self.getFocusId())

        if self.action.get("call") and self.action.get("id"):
            execute("SetProperty(tmdb_next_call,true,home)")
            if onnext_action:
                execute(onnext_action)

        if self.action.get("call") == "back" and onback_action:
            execute(onback_action)

        else:
            if close_action:
                execute(close_action)
            self.close()


""" Show & movie dialog
"""


class DialogVideo(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        self.first_load = True
        self.action = {}

        self.tmdb_id = kwargs["tmdb_id"]
        self.details = kwargs["details"]
        self.cast = kwargs["cast"]
        self.crew = kwargs["crew"]
        self.similar = kwargs["similar"]
        self.youtube = kwargs["youtube"]
        self.backdrops = kwargs["backdrops"]
        self.posters = kwargs["posters"]
        self.seasons = kwargs["seasons"]
        self.collection = kwargs["collection"]

    def __getitem__(self, key):
        return self.action[key]

    def __setitem__(self, key, value):
        self.action[key] = value

    def onInit(self):
        execute("ClearProperty(script.embuary.info-nextcall,home)")

        if self.first_load:
            self.add_items()

    def add_items(self):
        self.first_load = False

        index = 10051
        li = [
            self.details,
            self.cast,
            self.similar,
            self.youtube,
            self.backdrops,
            self.crew,
            self.collection,
            self.seasons,
            self.posters,
        ]

        for items in li:
            try:
                clist = self.getControl(index)
                clist.addItems(items)
            except RuntimeError as error:
                log(
                    "Control with id %s cannot be filled. Error --> %s"
                    % (str(index), error),
                    DEBUG,
                )
                pass
            index += 1

    def onAction(self, action):
        if action.getId() in [92, 10]:
            self.action["id"] = ""
            self.action["season"] = ""
            self.action["call"] = "back" if action.getId() == 92 else "close"
            self.quit()

    def onClick(self, controlId):
        next_id = xbmc.getInfoLabel("Container(%s).ListItem.Property(id)" % controlId)
        next_call = xbmc.getInfoLabel(
            "Container(%s).ListItem.Property(call)" % controlId
        )
        next_season = xbmc.getInfoLabel(
            "Container(%s).ListItem.Property(call_season)" % controlId
        )

        if next_call in ["person", "movie", "tv"] and next_id:
            if next_id != str(self.tmdb_id) or next_season:
                self.action["id"] = next_id
                self.action["call"] = next_call
                self.action["season"] = next_season
                self.quit()

        elif next_call == "image":
            FullScreenImage(controlId)

        elif next_call == "youtube":
            self.action["id"] = ""
            self.action["season"] = ""
            self.action["call"] = "youtube"
            xbmc.Player().play(
                "plugin://plugin.video.youtube/play/?video_id=%s"
                % xbmc.getInfoLabel("Container(%s).ListItem.Property(ytid)" % controlId)
            )
            self.quit()

    def quit(self):
        close_action = self.getProperty("onclose")
        onnext_action = self.getProperty("onnext")
        onback_action = self.getProperty("onback_%s" % self.getFocusId())

        if self.action.get("call") and self.action.get("id"):
            execute("SetProperty(script.embuary.info-nextcall,true,home)")
            if onnext_action:
                execute(onnext_action)

        if self.action.get("call") == "back" and onback_action:
            execute(onback_action)

        else:
            if close_action:
                execute(close_action)
            self.close()


""" Season dialog
"""


class DialogSeason(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        self.first_load = True
        self.action = {}

        self.tmdb_id = kwargs["tmdb_id"]
        self.details = kwargs["details"]
        self.cast = kwargs["cast"]
        self.gueststars = kwargs["gueststars"]
        self.posters = kwargs["posters"]

    def __getitem__(self, key):
        return self.action[key]

    def __setitem__(self, key, value):
        self.action[key] = value

    def onInit(self):
        execute("ClearProperty(script.embuary.info-nextcall,home)")

        if self.first_load:
            self.add_items()

    def add_items(self):
        self.first_load = False

        index = [10051, 10052, 10056, 10059]
        li = [self.details, self.cast, self.gueststars, self.posters]

        for items in li:
            try:
                clist = self.getControl(index[li.index(items)])
                clist.addItems(items)
            except RuntimeError as error:
                log(
                    "Control with id %s cannot be filled. Error --> %s"
                    % (str(index[li.index(items)]), error),
                    DEBUG,
                )
                pass

    def onAction(self, action):
        if action.getId() in [92, 10]:
            self.action["id"] = ""
            self.action["season"] = ""
            self.action["call"] = "back" if action.getId() == 92 else "close"
            self.quit()

    def onClick(self, controlId):
        next_id = xbmc.getInfoLabel("Container(%s).ListItem.Property(id)" % controlId)
        next_call = xbmc.getInfoLabel(
            "Container(%s).ListItem.Property(call)" % controlId
        )

        if next_call in ["person"] and next_id:
            self.action["id"] = next_id
            self.action["call"] = next_call
            self.action["season"] = ""
            self.quit()

        elif next_call == "image":
            FullScreenImage(controlId)

    def quit(self):
        close_action = self.getProperty("onclose")
        onnext_action = self.getProperty("onnext")
        onback_action = self.getProperty("onback_%s" % self.getFocusId())

        if self.action.get("call") and self.action.get("id"):
            execute("SetProperty(script.embuary.info-nextcall,true,home)")
            if onnext_action:
                execute(onnext_action)

        if self.action.get("call") == "back" and onback_action:
            execute(onback_action)

        else:
            if close_action:
                execute(close_action)
            self.close()


""" Slideshow dialog
"""


class FullScreenImage(object):
    def __init__(self, controlId):
        """Prefer the full-resolution URL the grid item carries.

        tmdb_handle_images gives the grid a small thumbnail so that opening
        the images tab does not upload a hundred full-size textures, and
        stashes the original in `fullsize` for exactly this moment. Falling
        back to Art(thumb) keeps other containers -- posters, backdrops
        attached to a title -- working unchanged.
        """
        slideshow = []
        for i in range(int(xbmc.getInfoLabel("Container(%s).NumItems" % controlId))):
            item = "Container(%s).ListItemAbsolute(%s)" % (controlId, i)
            image = xbmc.getInfoLabel("%s.Property(fullsize)" % item)

            if not image:
                image = xbmc.getInfoLabel("%s.Art(thumb)" % item)

            slideshow.append(image)

        dialog = self.ShowImage(
            "script-embuary-image.xml",
            ADDON_PATH,
            "default",
            "1080i",
            slideshow=slideshow,
            position=xbmc.getInfoLabel("Container(%s).CurrentItem" % controlId),
        )
        dialog.doModal()
        del dialog

    class ShowImage(xbmcgui.WindowXMLDialog):
        def __init__(self, *args, **kwargs):
            self.position = int(kwargs["position"]) - 1
            self.slideshow = list()
            for item in kwargs["slideshow"]:
                list_item = xbmcgui.ListItem(label="")
                list_item.setArt({"icon": item})
                self.slideshow.append(list_item)

        def onInit(self):
            self.cont = self.getControl(1)
            self.cont.addItems(self.slideshow)
            self.cont.selectItem(self.position)
            self.setFocusId(2)
