"""cache's config keys: a key renamed since an earlier release is refused, naming the new one."""

from __future__ import annotations

import pytest
from moxi.config import ConfigError

from moxi_cache import settings
from conftest import write_cache_config


@pytest.mark.parametrize("text, old, new", [
    ("[claude]\nttl_seconds = 900\n", "claude.ttl_seconds", "claude.ttl_s"),
    ("[watch]\ntick = 20\n", "watch.tick", "watch.tick_s"),
    ("[segment]\nwarn_before = 300\n", "segment.warn_before", "segment.warn_before_s"),
])
def test_a_renamed_key_is_refused_naming_the_new_one(own_roots, text, old, new):
    write_cache_config(own_roots, text)
    with pytest.raises(ConfigError) as caught:
        settings.current()
    assert caught.value.key == old
    assert caught.value.fix == f"renamed: set {new} instead"
