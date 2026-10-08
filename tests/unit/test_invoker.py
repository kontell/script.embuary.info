"""The setting must survive manifest updates and preserve valid XML."""

import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import xbmcaddon

import service
from resources.lib.invoker import apply_reuse_invoker


def test_invoker_setting_rewrites_only_the_manifest_value(tmp_path):
    path = tmp_path / "addon.xml"
    path.write_text(
        '<addon><extension point="xbmc.addon.metadata">'
        "<reuselanguageinvoker>true</reuselanguageinvoker>"
        "<platform>all</platform></extension></addon>",
        encoding="utf-8",
    )

    assert apply_reuse_invoker(path, False) is True
    assert ET.parse(path).findtext("./extension/reuselanguageinvoker") == "false"
    assert apply_reuse_invoker(path, False) is False
    assert apply_reuse_invoker(path, True) is True
    assert ET.parse(path).findtext("./extension/reuselanguageinvoker") == "true"


def test_invoker_setting_does_not_replace_manifest_without_tag(tmp_path):
    path = tmp_path / "addon.xml"
    path.write_text('<addon><extension point="xbmc.addon.metadata"/></addon>')

    assert apply_reuse_invoker(path, False) is None
    assert ET.parse(path).getroot().tag == "addon"


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("failures", [0, 1, 2])
def test_service_sync_preserves_manifest_until_setting_is_read(
    tmp_path, monkeypatch, enabled, failures
):
    path = tmp_path / "addon.xml"
    original = (
        "<addon><reuselanguageinvoker>"
        + ("false" if enabled else "true")
        + "</reuselanguageinvoker></addon>"
    )
    path.write_text(original, encoding="utf-8")
    reads = []

    class FlakyAddon(xbmcaddon.Addon):
        def getAddonInfo(self, key):
            return str(tmp_path) if key == "path" else super().getAddonInfo(key)

        def getSettingBool(self, key):
            assert key == "reuse_language_invoker"
            reads.append(self)
            if len(reads) <= failures:
                raise RuntimeError("unloaded")
            return enabled

    monkeypatch.setattr(xbmcaddon, "Addon", FlakyAddon)
    monitor = SimpleNamespace(_notify_invoker_restart=Mock())

    service.Service._sync_reuse_invoker(monitor)
    assert len(reads) == min(failures + 1, 2)
    if failures:
        assert reads[0] is not reads[1]
    if failures == 2:
        assert path.read_text(encoding="utf-8") == original
        monitor._notify_invoker_restart.assert_not_called()
        service.Service._sync_reuse_invoker(monitor)
        assert len(reads) == 3

    assert ET.parse(path).findtext("reuselanguageinvoker") == str(enabled).lower()
    monitor._notify_invoker_restart.assert_called_once_with()
