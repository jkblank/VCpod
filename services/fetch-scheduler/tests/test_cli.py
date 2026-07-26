from fetch_scheduler.cli import _print_tick_result
from fetch_scheduler.loop import TickResult


def test_print_tick_result_surfaces_source_errors(capsys):
    # Confirmed live: a target that's due every tick but always fails a
    # source auth check (e.g. expired cookies) printed nothing at all
    # before this — source_errors must be visible, not silently invisible
    # forever just because it's not an unexpected exception.
    result = TickResult(
        fetched={"alice": []},
        source_errors={"alice": ["apple_music: could not authenticate (expired cookies)"]},
    )

    _print_tick_result(result)

    captured = capsys.readouterr()
    assert "apple_music: could not authenticate (expired cookies)" in captured.out


def test_print_tick_result_nothing_due_message(capsys):
    _print_tick_result(TickResult())

    captured = capsys.readouterr()
    assert "nothing due" in captured.out


def test_print_tick_result_fetched_and_tick_error(capsys):
    result = TickResult(fetched={"alice": ["Chill"]}, errors=["bob"])

    _print_tick_result(result)

    captured = capsys.readouterr()
    assert "[alice] fetched: Chill" in captured.out
    assert "[bob] ERROR: tick failed, see log" in captured.out
