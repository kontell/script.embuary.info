#!/usr/bin/python
# coding: utf-8

########################

import xbmc

from resources.lib.helper import *
from resources.lib.nextaired import *

########################


class Service(xbmc.Monitor):
    def __init__(self):
        while not self.abortRequested():
            self.waitForAbort(100)

    """ Local library is cached for 24h. This service updates the cache if the library has been changed.
        Since multiple .OnUpdate() callbacks can happen at the same time the refreshing is done by Kodi's AlarmClock function.
    """

    def onNotification(self, sender, method, data):
        if (
            method
            in [
                "VideoLibrary.OnUpdate",
                "VideoLibrary.OnScanFinished",
                "VideoLibrary.OnCleanFinished",
            ]
            and cache_enabled()
        ):
            execute(
                "AlarmClock(EmbuaryInfoRefreshLibraryCache,RunScript(script.embuary.info,call=refresh_library_cache),00:05,silent)"
            )

    def onSettingsChanged(self):
        """Drop memoised settings.

        This process outlives every launch of the script and the plugin, so it
        is the one place where a value read minutes ago is not necessarily the
        value the user is now looking at in the settings dialog.
        """
        refresh()


if __name__ == "__main__":
    """Fetch next airing items on Kodi startup"""
    refresh()

    if condition("Library.HasContent(TVShows)") and cache_enabled():
        log("Refreshing next airing database", force=True)
        NextAired()
        log("Finished next airing database refreshing", force=True)

    Service()
