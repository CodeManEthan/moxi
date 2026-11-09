import pytest

from moxi import names


def test_the_constants():
    assert (names.HARNESS, names.OWNER, names.NAMESPACE) == ("moxi", "CodeManEthan", "moxi")
    assert names.ORG == names.OWNER  # the earlier name, kept for its callers


def test_every_spelling_is_built_from_the_module_name():
    assert names.distribution("cache") == "moxi-cache"
    assert names.import_name("cache") == "moxi_cache"
    assert names.repo("cache") == "CodeManEthan/moxi"
    assert names.env_prefix("cache") == "MOXI_CACHE_"
    assert names.env_prefix("comms") == "MOXI_COMMS_"


@pytest.mark.parametrize("name", ["cache", "comms", "turns", "sessions", "moxi"])
def test_every_module_lives_in_the_one_repository(name):
    assert names.repo(name) == f"{names.OWNER}/{names.HARNESS}" == "CodeManEthan/moxi"


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


def test_the_core_keeps_seven_more_names():
    assert names.RESERVED == {"moxi", "set", "unset", "runtime", "installs", "test",
                              "anchor"}
    assert not names.RESERVED & names.VERBS
    for name in ("doctor", "installs", "set", "moxi", "test"):
        assert names.is_reserved(name)
    for name in ("comms", "sets", "tests", None, 3):
        assert not names.is_reserved(name)


def test_each_name_the_core_keeps_has_one_reason():
    for name in names.RESERVED:
        why = names.reserved_why(name)
        assert isinstance(why, str) and why and "\n" not in why, name
    assert len({names.reserved_why(name) for name in names.RESERVED}) == len(names.RESERVED)
    assert names.reserved_why("test") == ("its variables would start MOXI_TEST_, which "
                                          "moxi-dev's test tiers use")
    for name in ("comms", "doctor", "tests", None, 3):
        assert names.reserved_why(name) is None


def test_test_is_kept_for_moxi_devs_test_tier_prefix():
    # moxi-dev's test tiers read MOXI_TEST_*; a module named test would own that prefix.
    from moxi_dev import contract

    assert names.env_prefix("test") == contract.DEV_ENV_PREFIX == "MOXI_TEST_"


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
    assert names.is_skill_dir(name) is counted
