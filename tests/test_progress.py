import io

from dbe.progress import ProgressBar, Spinner, human_bytes


def test_progress_bar_renders_and_finishes():
    out = io.StringIO()
    bar = ProgressBar("uploading", stream=out, force=True)
    bar.update(0, 100)
    bar.update(50, 100)
    text = out.getvalue()
    assert "uploading [" in text and " 50%" in text and "50 B/100 B" in text
    bar.finish("uploaded 100 B")
    assert out.getvalue().endswith("uploaded 100 B\n")
    bar.update(100, 100)  # no output after finish
    assert out.getvalue().endswith("uploaded 100 B\n")


def test_progress_bar_is_quiet_when_not_a_tty():
    out = io.StringIO()
    bar = ProgressBar("running prompts", unit="count", stream=out)
    bar.update(3, 10)
    assert out.getvalue() == ""
    bar.finish("run done: 10/10 prompts")
    assert out.getvalue() == "run done: 10/10 prompts\n"


def test_spinner_start_stop():
    out = io.StringIO()
    spinner = Spinner("loading", stream=out, force=True)
    spinner.start()
    spinner.stop("loaded")
    assert out.getvalue().endswith("loaded\n")
    quiet = Spinner("loading", stream=io.StringIO())
    quiet.stop()  # never started: no-op


def test_human_bytes():
    assert human_bytes(512) == "512 B"
    assert human_bytes(32_800_000) == "32.8 MB"
    assert human_bytes(1_500_000_000) == "1.5 GB"
