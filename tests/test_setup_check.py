import main as M

GOOD = {
    "FIREBASE_SERVICE_ACCOUNT": '{"type": "service_account"}',
    "GOOGLE_OAUTH_CLIENT_ID": "a",
    "GOOGLE_OAUTH_CLIENT_SECRET": "b",
    "GOOGLE_DRIVE_REFRESH_TOKEN": "c",
}


def test_all_secrets_present_is_fine():
    assert M.check_setup(dict(GOOD)) == []


def test_empty_secret_gives_plain_instruction():
    env = dict(GOOD, FIREBASE_SERVICE_ACCOUNT="")
    problems = M.check_setup(env)
    assert len(problems) == 1 and "FIREBASE_SERVICE_ACCOUNT is empty" in problems[0] and "Part G" in problems[0]


def test_missing_secret_is_reported_in_run_mode():
    env = dict(GOOD)
    del env["GOOGLE_DRIVE_REFRESH_TOKEN"]
    assert any("GOOGLE_DRIVE_REFRESH_TOKEN" in p for p in M.check_setup(env, mode="run"))


def test_check_mode_only_requires_firebase_service_account():
    env = {"FIREBASE_SERVICE_ACCOUNT": GOOD["FIREBASE_SERVICE_ACCOUNT"]}
    assert M.check_setup(env, mode="check") == []


def test_check_mode_still_requires_firebase_service_account():
    problems = M.check_setup({}, mode="check")
    assert len(problems) == 1 and "FIREBASE_SERVICE_ACCOUNT is empty" in problems[0]


def test_invalid_firebase_json_is_reported_in_check_mode():
    problems = M.check_setup({"FIREBASE_SERVICE_ACCOUNT": "not json"}, mode="check")
    assert any("not valid JSON" in p for p in problems)


def test_invalid_firebase_json_is_reported_in_run_mode():
    problems = M.check_setup(dict(GOOD, FIREBASE_SERVICE_ACCOUNT="not json"), mode="run")
    assert any("not valid JSON" in p for p in problems)


def test_main_stops_before_touching_anything_when_setup_is_missing(capsys):
    code = M.main(["check"], env={"FIREBASE_SERVICE_ACCOUNT": ""})
    out = capsys.readouterr().out
    assert code == 2 and "SETUP PROBLEM" in out
