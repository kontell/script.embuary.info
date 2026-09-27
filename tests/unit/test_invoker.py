"""The setting must survive manifest updates and preserve valid XML."""

import xml.etree.ElementTree as ET

from resources.lib.invoker import apply_reuse_invoker


def test_invoker_setting_rewrites_only_the_manifest_value(tmp_path):
    path = tmp_path / "addon.xml"
    path.write_text(
        '<addon><extension point="xbmc.addon.metadata">'
        '<reuselanguageinvoker>true</reuselanguageinvoker>'
        '<platform>all</platform></extension></addon>',
        encoding="utf-8",
    )

    assert apply_reuse_invoker(path, False) is True
    assert ET.parse(path).findtext(
        './extension/reuselanguageinvoker'
    ) == "false"
    assert apply_reuse_invoker(path, False) is False
    assert apply_reuse_invoker(path, True) is True
    assert ET.parse(path).findtext(
        './extension/reuselanguageinvoker'
    ) == "true"


def test_invoker_setting_does_not_replace_manifest_without_tag(tmp_path):
    path = tmp_path / "addon.xml"
    path.write_text('<addon><extension point="xbmc.addon.metadata"/></addon>')

    assert apply_reuse_invoker(path, False) is None
    assert ET.parse(path).getroot().tag == "addon"
