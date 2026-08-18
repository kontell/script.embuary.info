#!/usr/bin/python
# coding: utf-8

########################

import xbmc

from resources.lib.helper import *
from resources.lib.nextaired import *

########################


def publish_menu_buttons():
    """Mirror the button settings onto window properties, for skins.

    A skin cannot gate a button on an add-on setting: <visible> has no way to
    read one, and the whole expression fails to parse if you try. So each button
    is published on Window(Home) as `embuary.menu.<name>` and a skin gates on

        String.IsEqual(Window(Home).Property(embuary.menu.nextaired),true)

    which is the same arrangement plugin.video.kofin uses for its own two root
    entries, and what skin.contuary already expects.

    Set to "true" when on and cleared when off, rather than written as "false".
    A skin only ever tests for "true", and clearing leaves nothing stale behind
    for the next thing that reads the property.
    """
    for name in MENU_BUTTONS:
        key = MENU_PROPERTY % name

        if menu_enabled(name):
            winprop(key, "true")
        else:
            winprop(key, clear=True)


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
        """Drop memoised settings, then republish what a skin can see.

        This process outlives every launch of the script and the plugin, so it
        is the one place where a value read minutes ago is not necessarily the
        value the user is now looking at in the settings dialog. Republishing
        here is what lets a skin button appear or disappear as the toggle is
        flipped, rather than at the next Kodi start.
        """
        refresh()
        publish_menu_buttons()


if __name__ == "__main__":
    """Fetch next airing items on Kodi startup"""
    refresh()
    publish_menu_buttons()

    if condition("Library.HasContent(TVShows)") and cache_enabled():
        log("Refreshing next airing database", force=True)
        NextAired()
        log("Finished next airing database refreshing", force=True)

    Service()
