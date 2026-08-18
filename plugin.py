#!/usr/bin/python

########################

from resources.lib.widgets import *

########################

if __name__ == "__main__":
    """Settings are memoised for the length of one launch, so every launch has
    to say where it begins. Under <reuselanguageinvoker> this module is
    imported once and then re-entered here for every subsequent call, which
    is exactly the case a module-level read gets wrong.
    """
    refresh()
    plugin.run()
