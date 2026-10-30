import pytest

from moxi import names


def test_the_constants():
    assert (names.HARNESS, names.ORG, names.NAMESPACE) == ("moxi", "moxihq", "moxi")


def test_every_spelling_is_built_from_the_module_name():
    assert names.distribution("cache") == "moxi-cache"
    assert names.import_name("cache") == "moxi_cache"
    assert names.repo("cache") == "moxihq/cache"
    assert names.env_prefix("cache") == "MOXI_CACHE_"
    assert names.env_prefix("comms") == "MOXI_COMMS_"


@pytest.mark.parametrize("name", ["cache", "comms", "turns", "sessions", "s3", "2fa"])
def test_a_module_name_is_lower_case_letters_and_digits(name):
    assert names.is_module_name(name)
    assert names.check_module_name(name) == name


@pytest.mark.parametrize("name", ["", "Cache", "my-mod", "my_mod", "cache ", "café", "a.b",
                                  None, 3])
def test_anything_else_is_refused(name):
    assert not names.is_module_name(name)
    with pytest.raises(ValueError, match="not a Moxi module name"):
        names.check_module_name(name)
    if isinstance(name, str):
        for spell in (names.distribution, names.import_name, names.repo, names.env_prefix):
            with pytest.raises(ValueError):
                spell(name)


def test_the_umbrellas_verbs_are_reserved():
    assert names.VERBS == {"modules", "install", "shortcut", "config", "check", "help",
                           "version", "doctor", "uninstall", "run", "paths"}
    assert isinstance(names.VERBS, frozenset)


def test_the_core_keeps_five_more_names():
    assert names.RESERVED == {"moxi", "set", "unset", "runtime", "installs"}
    assert not names.RESERVED & names.VERBS
    for name in ("doctor", "installs", "set", "moxi"):
        assert names.is_reserved(name)
    for name in ("comms", "sets", None, 3):
        assert not names.is_reserved(name)
