"""Keep the installed manifest's invoker flag in sync with the setting.

Kodi reads this metadata when it loads the add-on, so a change takes effect
after a Kodi restart. Reconcile at service startup because an add-on update
restores the packaged manifest (which ships with reuse enabled).
"""

import os
import re
import stat
import tempfile


REUSE_TAG = re.compile(r"(<reuselanguageinvoker>)(true|false)(</reuselanguageinvoker>)")


def apply_reuse_invoker(path, enabled):
    """Return True if changed, False if current, None if it could not be set."""
    try:
        with open(path, "r", encoding="utf-8") as source:
            content = source.read()
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return None

    wanted = "true" if enabled else "false"
    updated, count = REUSE_TAG.subn(r"\g<1>%s\g<3>" % wanted, content, count=1)
    if count != 1:
        return None
    if updated == content:
        return False

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=os.path.dirname(path),
            prefix=".addon.xml.", delete=False
        ) as output:
            temporary = output.name
            output.write(updated)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        return None
    return True
