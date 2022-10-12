# -*- coding: utf-8 -*-

# FLEDGE_BEGIN
# See: http://fledge-iot.readthedocs.io/
# FLEDGE_END

import pytest

from python.fledge.plugins.south.playback import playback



def test_plugin_contract():
    # Evaluates if the plugin has all the required methods
    assert callable(getattr(playback, 'plugin_info'))
    assert callable(getattr(playback, 'plugin_init'))
    assert callable(getattr(playback, 'plugin_start'))
    assert callable(getattr(playback, 'plugin_shutdown'))
    assert callable(getattr(playback, 'plugin_reconfigure'))
