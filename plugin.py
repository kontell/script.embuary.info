#!/usr/bin/python

########################

import sys

from resources.lib.widgets import *

########################

if __name__ == "__main__":
    """Everything a parked interpreter would otherwise carry over from the
    previous call.

    `plugin` is built at module scope, and script.module.routing's constructor
    reads sys.argv there: the path, the query, and the plugin handle. Its run()
    re-reads the path and the query on every call but **not** the handle, which
    is fine while each invocation gets a fresh interpreter and wrong the moment
    one is reused: every call after the first would write its listing into the
    first call's handle, which Kodi closed long ago. The listing never appears
    and the caller waits.

    Settings are memoised for the length of one launch for the same reason; see
    resources/lib/settings.py.
    """
    plugin.handle = (
        int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else -1
    )
    refresh()
    plugin.run()
