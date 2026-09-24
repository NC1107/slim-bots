from slimbots.converters import Duration, TimeOfDay


def test_duration_parses_a_single_unit():
    assert Duration.parse("10m") == 600
    assert Duration.parse("2h") == 7200
    assert Duration.parse("1d") == 86400
    assert Duration.parse("30s") == 30


def test_duration_parses_a_compound_spec():
    assert Duration.parse("2h30m") == 9000
    assert Duration.parse("1d12h") == 129600


def test_duration_rejects_garbage():
    assert Duration.parse("") is None
    assert Duration.parse("soon") is None
    assert Duration.parse("10") is None
    assert Duration.parse("10x") is None
    assert Duration.parse("0s") is None
    assert Duration.parse("-5m") is None


def test_duration_is_a_plain_int_of_seconds():
    assert isinstance(Duration.parse("1m"), int)
    assert Duration.parse("1m") + 1 == 61


def test_time_of_day_parses_valid_clock_times():
    t = TimeOfDay.parse("09:05")
    assert (t.hour, t.minute) == (9, 5)
    t = TimeOfDay.parse("23:59")
    assert (t.hour, t.minute) == (23, 59)


def test_time_of_day_rejects_out_of_range_and_malformed():
    assert TimeOfDay.parse("24:00") is None
    assert TimeOfDay.parse("12:60") is None
    assert TimeOfDay.parse("noon") is None
    assert TimeOfDay.parse("12") is None
    assert TimeOfDay.parse("") is None
