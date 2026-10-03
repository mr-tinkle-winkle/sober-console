from sober_console.logparse import parse_line, is_output_tag


def test_roblox_desktop_style_output_line():
    ln = parse_line("2024-05-01T12:34:56.789Z,12.345678,00001e8c,6 [FLog::Output] Hello from a script")
    assert ln.tag == "FLog::Output"
    assert ln.level == "output"
    assert ln.message == "Hello from a script"
    assert ln.time == "12:34:56.789"


def test_script_output_is_never_level_guessed():
    # A game printing the word "error" is still just a Message in the dev console.
    ln = parse_line("2024-05-01T12:34:56.789Z,1.0,00001e8c,6 [FLog::Output] no error here")
    assert ln.level == "output"


def test_engine_tags_get_levels_from_tag_or_text():
    assert parse_line("2024-05-01T12:00:00.000Z,1.0,0001,6 [FLog::Network] connected").level == "info"
    assert parse_line("2024-05-01T12:00:00.000Z,1.0,0001,6 [FLog::Error] boom").level == "error"
    assert parse_line("2024-05-01T12:00:00.000Z,1.0,0001,6 [FLog::Warning] hmm").level == "warning"
    assert parse_line("2024-05-01T12:00:00.000Z,1.0,0001,6 [FLog::Network] Fatal: lost").level == "error"


def test_sober_own_format():
    ln = parse_line("info: app: lifecycle: app_start delta=9.9e-05ms")
    assert ln.level == "info"
    assert ln.tag == "app: lifecycle"
    assert ln.message.startswith("app_start")


def test_sober_single_segment_and_untagged():
    assert parse_line("info: app: app tag = s048-1836").tag == "app"
    ln = parse_line("warn: Failed to connect to localhost port 5051")
    assert ln.level == "warning" and ln.tag == ""
    assert parse_line("error: something broke").level == "error"


def test_url_in_message_is_not_mistaken_for_a_tag():
    ln = parse_line("info: https://auth.roblox.com/v1/logout failed")
    assert ln.tag == ""


def test_info_line_that_says_error_is_shown_as_error():
    assert parse_line("info: HttpResponse error 401 on logout").level == "error"


def test_logcat_style():
    ln = parse_line("W/RobloxApp( 1234): low memory")
    assert (ln.level, ln.tag, ln.message) == ("warning", "W/RobloxApp", "low memory")
    assert parse_line("E/libEGL: bad config").level == "error"


def test_iso_prefix_then_other_shapes():
    ln = parse_line("2024-05-01 12:00:01.250 info: net: hello")
    assert ln.time == "12:00:01.250" and ln.tag == "net" and ln.level == "info"


def test_plain_line_falls_back_to_info_and_keywords():
    assert parse_line("some random text").level == "info"
    assert parse_line("Segfault in thread 3").level == "error"
    assert parse_line("Warning: texture too large").level == "warning"


def test_continuation_inherits_previous():
    first = parse_line("error: Script 'Workspace.Foo', Line 4 - boom")
    cont = parse_line("    at Foo.bar", first)
    assert cont.level == "error"
    assert parse_line("Stack Begin", first).level == "error"
    # without a previous line there is nothing to inherit
    assert parse_line("    at Foo.bar").level == "info"


def test_continuation_inherits_the_parents_timestamp():
    first = parse_line("2024-05-01T12:00:40.900Z,40.0,0001,6 [FLog::Error] boom")
    assert parse_line("    Script 'A', Line 88", first).time == "12:00:40.900"


def test_ansi_codes_are_stripped():
    ln = parse_line("\x1b[31merror: red text\x1b[0m")
    assert ln.level == "error" and "\x1b" not in ln.raw


def test_is_output_tag():
    assert is_output_tag("FLog::Output") and is_output_tag("Output") and is_output_tag("Foo::Output")
    assert not is_output_tag("FLog::Network") and not is_output_tag("")
