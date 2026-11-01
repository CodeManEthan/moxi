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


@pytest.mark.parametrize("name", ["moxi-handoff", "moxi-comms-send", "moxi-2fa", "moxi-a1-b2",
                                  "moxi-" + "a" * 59])
def test_a_skill_name_is_moxi_then_hyphen_joined_words(name):
    assert names.is_skill_name(name)
    assert names.check_skill_name(name) == name


@pytest.mark.parametrize("name", ["handoff", "moxi-", "moxi--x", "Moxi-x", "moxi-x-", "moxi_x",
                                  "moxi-X", "moxi-x y", "moxi-x\n", "moxi-café", "moxi",
                                  "moxi-" + "a" * 60, None, 3])
def test_any_other_skill_name_is_refused_naming_the_rule(name):
    assert not names.is_skill_name(name)
    with pytest.raises(ValueError, match=r"not a Moxi skill name: a skill Moxi ships is "
                                         r"named moxi-<name>, .* 64 characters at most"):
        names.check_skill_name(name)


def test_the_skill_name_cap_is_64_characters():
    assert names.SKILL_PREFIX == "moxi-" and names.SKILL_NAME_MAX == 64
    assert len("moxi-" + "a" * 59) == 64 and len("moxi-" + "a" * 60) == 65


@pytest.mark.parametrize("name, counted", [("moxi-x", True), ("handoff", True), ("x", True),
                                           (".x", False), (".git", False),
                                           ("__pycache__", False), ("_private", True)])
def test_a_skill_folder_is_any_but_dot_named_and_pycache(name, counted):
    assert names.is_skill_folder(name) is counted
