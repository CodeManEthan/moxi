"""The gateway's sign-in, in process through loopback's guard on the gateway profile:
pairing by a one-use code, the pairing state, passkeys checked with `webauthn` against a
fake authenticator, the signed challenge and its spent set, the open routes, the sessions,
and the device file."""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import subprocess
import sys
import threading

import pytest

pytest.importorskip("webauthn")
pytest.importorskip("cryptography")

from gateway_kit import HOST, KEPT, Authenticator, Clock, Phone, b64, unb64  # noqa: E402
from moxi_ui.gateway import pairing  # noqa: E402
from moxi_ui.gateway.access import (COOKIE, IDLE, LIMIT, MAX_AGE, PER_DEVICE,  # noqa: E402
                                    signin_redirect)
from moxi_ui.gateway.address import Kept  # noqa: E402
from moxi_ui.gateway.devices import LIMIT as DEVICE_LIMIT  # noqa: E402
from moxi_ui.gateway.devices import Devices, devices_path  # noqa: E402


@pytest.fixture
def phone(tmp_path):
    return Phone(tmp_path)


# --- pairing ----------------------------------------------------------------------------


def test_a_pairing_with_a_passkey_writes_the_device_and_signs_in(phone):
    begun = phone.request("POST", "/api/pair/begin", {"code": phone.mint(), "name": "Pixel"})
    assert begun.status == 200
    options = begun.json()["options"]
    assert options["rp"]["id"] == HOST
    assert len(unb64(options["user"]["id"])) == 16
    assert options["authenticatorSelection"]["residentKey"] == "required"
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["attestation"] == "none" and options["timeout"] == 120_000
    authenticator = Authenticator()
    done = phone.request("POST", "/api/pair/finish", {
        "pairing": begun.json()["pairing"], "credential": authenticator.create(options)})
    assert done.status == 200
    (cookie,) = done.header("set-cookie")
    assert re.fullmatch(rf"{re.escape(COOKIE)}=[A-Za-z0-9_-]{{43}}; Secure; HttpOnly; "
                        rf"SameSite=Strict; Path=/; Max-Age={MAX_AGE}", cookie)
    assert "manifest-src 'self'" in done.header("content-security-policy")[0]
    (device,) = Devices.of(phone.state_dir).all()
    assert done.json() == {"key": done.json()["key"], "device": device.id}
    assert (device.name, device.host, device.provider, device.change) == (
        "Pixel", HOST, "cloudflare", "r1.1")
    assert device.credential == b64(authenticator.credential_id) and device.public_key
    assert stat.S_IMODE(os.stat(devices_path(phone.state_dir)).st_mode) == 0o600
    assert phone.audit.codes() == ["pair", "signin"]
    phone.keep(done)
    assert phone.request("GET", "/m/fixture/api/read").status == 200


def test_two_racing_redemptions_of_one_code_make_one_pairing_state(phone):
    code = phone.mint()
    barrier, answers = threading.Barrier(8), []

    def redeem():
        barrier.wait()
        answers.append(phone.access._begin_pairing(code, "Pixel")[0])

    threads = [threading.Thread(target=redeem) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(answers) == [200] + [403] * 7
    assert phone.access.pairings.live() == 1
    assert phone.audit.codes().count("pairing") == 7


def test_a_pairing_code_expires_after_120_seconds(phone):
    code = phone.mint()
    phone.clock.now += 121
    answer = phone.request("POST", "/api/pair/begin", {"code": code, "name": "Pixel"})
    assert answer.status == 403 and phone.audit.codes() == ["pairing"]
    assert phone.access.pairings.live() == 0


def test_a_pairing_code_never_reaches_a_log_line_or_an_audit_row(phone, caplog):
    caplog.set_level(logging.DEBUG)
    code = phone.mint()
    url = pairing.pair_url(HOST, 443, code)
    assert url.endswith(f"#c={code}") and "?n=" in url
    begun = phone.request("POST", "/api/pair/begin", {"code": code, "name": "Pixel"})
    phone.request("POST", "/api/pair/begin", {"code": code, "name": "Pixel"})  # refused
    phone.request("POST", "/api/pair/finish", {"pairing": begun.json()["pairing"],
                                               "credential": Authenticator().create(
                                                   begun.json()["options"], origin="https://x")})
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert code not in logged and code not in repr(phone.audit.rows)


def test_the_pairing_url_is_new_each_time_and_names_the_port_unless_443():
    first, second = pairing.pair_url(HOST, 443, "c"), pairing.pair_url(HOST, 443, "c")
    assert re.fullmatch(rf"https://{re.escape(HOST)}/pair\?n=[0-9a-f]{{16}}#c=c", first)
    assert first != second
    assert pairing.pair_url(HOST, 8443, "c").startswith(f"https://{HOST}:8443/pair?n=")


def test_minting_with_no_address_refuses_naming_gateway_on(tmp_path):
    with pytest.raises(pairing.NoAddress, match="moxi ui gateway enable"):
        pairing.mint(tmp_path, None)
    assert not (tmp_path / pairing.PAIR_DIR).exists()


@pytest.mark.parametrize("first", ["passkey", "none"])
def test_one_finish_takes_the_pairing_state_and_a_second_is_refused(phone, first):
    begun = phone.request("POST", "/api/pair/begin", {"code": phone.mint(), "name": "Pixel"})
    pairing_id, options = begun.json()["pairing"], begun.json()["options"]
    credential = Authenticator().create(options)
    one, two = (credential, None) if first == "passkey" else (None, credential)
    assert phone.request("POST", "/api/pair/finish",
                         {"pairing": pairing_id, "credential": one}).status == 200
    again = phone.request("POST", "/api/pair/finish", {"pairing": pairing_id, "credential": two})
    assert again.status == 403 and phone.audit.codes()[-1] == "pairing"
    assert len(Devices.of(phone.state_dir).all()) == 1


def test_racing_finishes_on_one_state_pair_one_device(phone):
    begun = phone.request("POST", "/api/pair/begin", {"code": phone.mint(), "name": "Pixel"})
    pairing_id = begun.json()["pairing"]
    barrier, answers = threading.Barrier(6), []

    def finish():
        barrier.wait()
        answers.append(phone.access._finish_pairing(pairing_id, None)[0])

    threads = [threading.Thread(target=finish) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(answers) == [200] + [403] * 5
    assert len(Devices.of(phone.state_dir).all()) == 1


def test_a_finish_naming_no_live_state_is_refused(phone):
    assert phone.request("POST", "/api/pair/finish",
                         {"pairing": "x" * 43, "credential": None}).status == 403
    begun = phone.request("POST", "/api/pair/begin", {"code": phone.mint(), "name": "Pixel"})
    phone.clock.now += 121
    assert phone.request("POST", "/api/pair/finish", {"pairing": begun.json()["pairing"],
                                                      "credential": None}).status == 403
    assert phone.audit.codes() == ["pairing", "pairing"]


def test_a_pairing_without_a_passkey_signs_in_and_is_marked(phone):
    done = phone.pair(None)
    assert done.status == 200
    (device,) = Devices.of(phone.state_dir).all()
    assert device.credential is None and device.public_key is None and not device.passkey
    assert phone.request("GET", "/m/fixture/api/read").status == 200


@pytest.mark.parametrize("bad", ["origin", "rp_id", "uv"])
def test_a_new_passkey_with_a_wrong_origin_rp_id_or_no_user_verification_is_refused(phone, bad):
    begun = phone.request("POST", "/api/pair/begin", {"code": phone.mint(), "name": "Pixel"})
    kwargs = {"origin": {"origin": "https://other.example.test"},
              "rp_id": {"rp_id": "other.example.test"}, "uv": {"uv": False}}[bad]
    credential = Authenticator().create(begun.json()["options"], **kwargs)
    answer = phone.request("POST", "/api/pair/finish",
                           {"pairing": begun.json()["pairing"], "credential": credential})
    assert answer.status == 403 and phone.audit.codes() == ["credential"]
    assert Devices.of(phone.state_dir).all() == [] and phone.access.pairings.live() == 0


def test_a_malformed_begin_or_finish_is_a_400_and_spends_nothing(phone):
    code = phone.mint()
    assert phone.request("POST", "/api/pair/begin", {"code": code}).status == 400
    assert phone.request("POST", "/api/pair/begin", {"code": code, "name": ""}).status == 400
    assert phone.request("POST", "/api/pair/begin",
                         {"code": code, "name": "x" * 65}).status == 400
    assert phone.request("POST", "/api/pair/begin",
                         {"code": code, "name": "Pixel"}).status == 200
    assert phone.request("POST", "/api/pair/finish",
                         {"pairing": "p", "credential": "text"}).status == 400


def test_past_twenty_devices_a_pairing_is_refused(phone):
    for number in range(DEVICE_LIMIT):
        assert phone.pair(None, name=f"phone {number}", keep=False).status == 200
    answer = phone.pair(None, name="one more", keep=False)
    assert answer.status == 409 and "revoke" in answer.json()["error"]


def test_a_pairing_named_as_a_stale_device_takes_its_place(phone):
    phone.pair(None, name="Pixel", keep=False)
    (old,) = Devices.of(phone.state_dir).all()
    phone.kept = Kept("r2.1", "gw2.example.test", 443, "cloudflare")
    phone.access.tick()
    assert Devices.of(phone.state_dir).get(old.id).stale
    phone.kept = KEPT
    phone.pair(None, name="Pixel", keep=False)
    (new,) = Devices.of(phone.state_dir).all()
    assert new.id != old.id and not new.stale
    assert ("re-pair", {"device": new.id, "replaces": old.id, "passkey": False}) in phone.audit.rows


# --- sign-in ----------------------------------------------------------------------------


def test_a_sign_in_begin_stores_nothing_and_a_finish_signs_in(phone):
    authenticator = Authenticator()
    phone.pair(authenticator, keep=False)
    begun = phone.request("POST", "/api/signin/begin", {})
    options = begun.json()["options"]
    assert begun.status == 200 and options["rpId"] == HOST
    assert options["allowCredentials"] == [] and options["userVerification"] == "required"
    assert phone.access.challenges.spent() == 0 and phone.access.pairings.live() == 0
    done = phone.request("POST", "/api/signin/finish",
                         {"credential": authenticator.get(options)})
    assert done.status == 200 and phone.access.challenges.spent() == 1
    phone.keep(done)
    assert phone.request("GET", "/m/fixture/api/read").status == 200
    assert Devices.of(phone.state_dir).all()[0].sign_count == 1


@pytest.mark.parametrize("bad", ["origin", "rp_id", "uv"])
def test_an_assertion_with_a_wrong_origin_rp_id_or_no_user_verification_is_refused(phone, bad):
    authenticator = Authenticator()
    phone.pair(authenticator, keep=False)
    kwargs = {"origin": {"origin": "https://other.example.test"},
              "rp_id": {"rp_id": "other.example.test"}, "uv": {"uv": False}}[bad]
    answer = phone.sign_in(authenticator, **kwargs)
    assert answer.status == 403 and phone.audit.codes()[-1] == "credential"
    assert phone.access.challenges.spent() == 0


def test_a_stale_device_signs_in_to_nothing(phone):
    authenticator = Authenticator()
    phone.pair(authenticator, keep=False)
    phone.access.stale(KEPT)
    assert phone.sign_in(authenticator).status == 403
    assert phone.audit.codes()[-1] == "stale"
    assert Devices.of(phone.state_dir).all()[0].stale


def test_a_sign_count_that_goes_backwards_is_refused_and_logged(phone, caplog):
    authenticator = Authenticator()
    phone.pair(authenticator, keep=False)
    assert phone.sign_in(authenticator, keep=False, count=5).status == 200
    caplog.set_level(logging.WARNING)
    assert phone.sign_in(authenticator, keep=False, count=3).status == 403
    assert phone.audit.codes()[-1] == "credential"
    assert any("sign count" in record.getMessage() for record in caplog.records)


def test_a_synced_passkey_with_no_count_signs_in_again(phone):
    authenticator = Authenticator(keep_count=False)
    phone.pair(authenticator, keep=False)
    assert phone.sign_in(authenticator, keep=False).status == 200
    assert phone.sign_in(authenticator, keep=False).status == 200


def test_an_unknown_or_revoked_passkey_is_refused(phone):
    stranger = Authenticator()
    assert phone.sign_in(stranger).status == 403 and phone.audit.codes()[-1] == "credential"
    authenticator = Authenticator()
    phone.pair(authenticator, keep=False)
    (device,) = Devices.of(phone.state_dir).all()
    assert phone.access.revoke(device.id)
    assert phone.sign_in(authenticator).status == 403 and phone.audit.codes()[-1] == "revoked"


# --- Rule (open routes) ---------------------------------------------------------------


def test_ten_thousand_begins_from_another_caller_leave_the_users_sign_in_working(phone):
    authenticator = Authenticator()
    phone.pair(authenticator, keep=False)
    assert set(phone.repeat(10_000, "POST", "/api/signin/begin", {})) == {200}
    assert phone.access.challenges.spent() == 0
    assert phone.request("POST", "/api/signin/finish",
                         {"credential": Authenticator().get({"rpId": HOST,
                                                             "challenge": b64(b"x" * 56)})}
                         ).status == 403
    assert phone.access.challenges.spent() == 0
    assert phone.sign_in(authenticator).status == 200
    assert phone.access.challenges.spent() == 1


def test_a_challenge_with_a_bad_tag_past_its_expiry_or_used_twice_is_refused(phone):
    authenticator = Authenticator()
    phone.pair(authenticator, keep=False)
    # A bad tag: one byte of the signed challenge changed.
    options = phone.request("POST", "/api/signin/begin", {}).json()["options"]
    forged = bytearray(unb64(options["challenge"]))
    forged[0] ^= 1
    assertion = authenticator.get(options, challenge=b64(bytes(forged)))
    assert phone.request("POST", "/api/signin/finish",
                         {"credential": assertion}).status == 403
    # Past its expiry.
    options = phone.request("POST", "/api/signin/begin", {}).json()["options"]
    phone.clock.now += 61
    assert phone.request("POST", "/api/signin/finish",
                         {"credential": authenticator.get(options)}).status == 403
    # Used twice: the second finish, verified as well, finds it spent.
    options = phone.request("POST", "/api/signin/begin", {}).json()["options"]
    assert phone.request("POST", "/api/signin/finish",
                         {"credential": authenticator.get(options)}).status == 200
    assert phone.request("POST", "/api/signin/finish",
                         {"credential": authenticator.get(options)}).status == 403
    assert phone.audit.codes()[-4:] == ["challenge", "challenge", "signin", "challenge"]
    assert phone.access.challenges.spent() == 1


def test_a_shell_restart_honours_no_earlier_challenge(tmp_path):
    first = Phone(tmp_path)
    authenticator = Authenticator()
    first.pair(authenticator, keep=False)
    options = first.request("POST", "/api/signin/begin", {}).json()["options"]
    second = Phone(tmp_path, clock=first.clock)
    assert second.request("POST", "/api/signin/finish",
                          {"credential": authenticator.get(options)}).status == 403
    assert second.audit.codes() == ["challenge"]
    assert second.sign_in(authenticator).status == 200


# --- the session ----------------------------------------------------------------------


def test_the_credential_classes(phone):
    # The open routes and the pages need nothing.
    assert phone.request("POST", "/api/signin/begin", {}).status == 200
    pair_page = phone.request("GET", "/pair?n=0123456789abcdef")
    assert pair_page.status == 200
    assert pair_page.header("clear-site-data") == ['"cache", "storage"']
    assert pair_page.header("cache-control") == ["no-store"]
    assert "worker-src 'self'" in pair_page.header("content-security-policy")[0]
    script = phone.request("GET", "/signin.js")
    assert script.status == 200 and script.header("clear-site-data") == []
    assert script.header("content-type") == ["text/javascript; charset=utf-8"]
    # Without a session, a route, an asset and a path outside the table are refused.
    for path in ("/m/fixture/api/read", "/m/fixture/app.js", "/nowhere"):
        assert phone.request("GET", path).status == 403
    assert phone.request("POST", "/api/signout", {}).status == 403
    phone.pair(None)
    # An asset needs the cookie; a route needs the cookie and the key.
    assert phone.request("GET", "/m/fixture/app.js", key=False).status == 200
    assert phone.request("GET", "/m/fixture/api/read", key=False).status == 403
    assert phone.request("GET", "/m/fixture/api/read").status == 200
    assert phone.request("GET", "/nowhere").status == 404
    # A navigation passes gate 4 with or without a cookie: the router decides. With no
    # navigation fallback in this table, gate 5 answers it, after the credential.
    assert phone.request("GET", "/m/ui/", cookie=False, navigate=True).status == 404


def test_no_token_class_reaches_anything(phone):
    phone.pair(None)
    answer = phone.request("GET", "/m/fixture/api/read", headers=(("x-moxi-token", "t" * 43),))
    assert answer.status == 403


def test_a_navigation_without_a_cookie_is_sent_to_a_new_signin_url():
    first, second = signin_redirect(), signin_redirect()
    location = dict((k.decode(), v.decode()) for k, v in first[1])["location"]
    assert first[0] == 303 and re.fullmatch(r"/signin\?n=[0-9a-f]{16}", location)
    assert location != dict((k.decode(), v.decode()) for k, v in second[1])["location"]


def test_a_missing_page_is_a_404(tmp_path):
    phone = Phone(tmp_path, pages={})
    assert phone.request("GET", "/signin?n=0123456789abcdef").status == 404


def test_signout_ends_the_session_and_clears_the_cookie(phone):
    phone.pair(None)
    answer = phone.request("POST", "/api/signout", {})
    assert answer.status == 200 and "Max-Age=0" in answer.header("set-cookie")[0]
    assert phone.audit.codes()[-1] == "signout"
    assert phone.request("GET", "/m/fixture/api/read").status == 403


def test_sessions_live_in_memory_only(phone):
    phone.pair(Authenticator())
    names = sorted(p.name for p in phone.state_dir.rglob("*") if p.is_file())
    assert names == ["devices.json", "devices.json.lock"]
    text = devices_path(phone.state_dir).read_text()
    assert phone.cookie not in text and phone.key not in text


def test_a_session_ends_after_twelve_hours_unused_or_seven_days(phone):
    phone.pair(None)
    phone.clock.now += IDLE - 60
    assert phone.request("GET", "/m/fixture/api/read").status == 200
    phone.clock.now += IDLE - 60
    assert phone.request("GET", "/m/fixture/api/read").status == 200
    phone.clock.now += IDLE + 1
    assert phone.request("GET", "/m/fixture/api/read").status == 403
    phone.pair(None, name="other")
    for _ in range(MAX_AGE // (IDLE - 60) + 1):
        phone.clock.now += IDLE - 60
        if phone.request("GET", "/m/fixture/api/read").status != 200:
            break
    assert phone.request("GET", "/m/fixture/api/read").status == 403
    assert phone.access.store.live() == 0


def test_four_sessions_per_device_and_sixteen_in_all_the_oldest_first(phone):
    authenticator = Authenticator()
    phone.pair(authenticator, keep=False)
    cookies = []
    for _ in range(PER_DEVICE + 1):
        phone.clock.now += 1
        phone.sign_in(authenticator)
        cookies.append((phone.cookie, phone.key))
    assert phone.access.store.live() == PER_DEVICE
    phone.cookie, phone.key = cookies[0]
    assert phone.request("GET", "/m/fixture/api/read").status == 403
    phone.cookie, phone.key = cookies[1]
    assert phone.request("GET", "/m/fixture/api/read").status == 200
    for number in range(LIMIT):
        phone.clock.now += 1
        phone.pair(None, name=f"phone {number}")
    assert phone.access.store.live() == LIMIT
    phone.cookie, phone.key = cookies[-1]
    assert phone.request("GET", "/m/fixture/api/read").status == 403


# --- devices --------------------------------------------------------------------------


def test_revoking_at_the_desk_ends_the_devices_sessions_at_once(phone):
    phone.pair(None)
    (device,) = Devices.of(phone.state_dir).all()
    assert phone.access.revoke(device.id)
    assert phone.request("GET", "/m/fixture/api/read").status == 403
    assert ("revoke", {"device": device.id}) in phone.audit.rows
    assert not phone.access.revoke(device.id)


def test_a_revoke_from_another_process_ends_the_sessions_at_the_next_tick(phone):
    phone.pair(None)
    (device,) = Devices.of(phone.state_dir).all()
    assert Devices.of(phone.state_dir).revoke(device.id)
    assert phone.request("GET", "/m/fixture/api/read").status == 200
    phone.access.tick()
    assert phone.request("GET", "/m/fixture/api/read").status == 403


def test_a_new_host_marks_the_devices_stale_and_ends_their_sessions(phone):
    phone.pair(None)
    (device,) = Devices.of(phone.state_dir).all()
    phone.kept = Kept("r2.1", "gw2.example.test", 443, "cloudflare")
    phone.access.tick()
    assert Devices.of(phone.state_dir).get(device.id).stale
    assert ("mark-stale", {"devices": [device.id]}) in phone.audit.rows
    assert phone.request("GET", "/m/fixture/api/read").status == 403
    # A new port keeps the host: nothing more is marked.
    phone.access.tick()
    assert phone.audit.codes().count("mark-stale") == 1


def test_the_device_files_marks_for_the_tunnels_change_listener(tmp_path):
    devices = Devices.of(tmp_path)
    a = devices.add(name="a", credential=None, public_key=None, sign_count=0, host=HOST,
                    provider="tailscale", change="1")
    b = devices.add(name="b", credential=None, public_key=None, sign_count=0,
                    host="gw2.example.test", provider="tailscale", change="2")
    assert devices.stale_unless("GW2.example.test", "tailscale") == [a.id]
    assert devices.stale_unless("gw2.example.test", "cloudflare") == [b.id]
    assert devices.stale_unless("gw2.example.test", "cloudflare") == []


def test_a_device_file_that_cant_be_read_is_never_written_over(tmp_path):
    path = devices_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not json")
    with pytest.raises(ValueError):
        Devices(path).add(name="a", credential=None, public_key=None, sign_count=0,
                          host=HOST, provider="cloudflare", change="1")
    assert path.read_text() == "not json" and Devices(path).all() == []


def test_nothing_imports_webauthn_until_a_passkey_is_checked():
    code = ("import sys, moxi_ui.serve, moxi_ui.page, moxi_ui.gateway.access, "
            "moxi_ui.gateway.desk; print('webauthn' in sys.modules)")
    found = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           check=True)
    assert found.stdout.strip() == "False"


def test_the_options_are_json(phone):
    begun = phone.request("POST", "/api/pair/begin", {"code": phone.mint(), "name": "Pixel"})
    json.dumps(begun.json())
