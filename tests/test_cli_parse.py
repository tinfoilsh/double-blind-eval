from dbe.cli import build_parser, resolve_options


def parse(argv, env=None, monkeypatch=None):
    if monkeypatch is not None:
        for k in ("DBE_ENCLAVE", "DBE_REPO", "DBE_PARTY", "DBE_KEY", "DBE_DEV_URL"):
            monkeypatch.delenv(k, raising=False)
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
    return resolve_options(build_parser().parse_args(argv))


def test_shared_options_work_after_the_subcommand(monkeypatch):
    args = parse(["keygen", "--party", "benchmark-owner"], monkeypatch=monkeypatch)
    assert args.party == "benchmark-owner" and args.command == "keygen"
    args = parse(["model", "upload", "./adapter", "--party", "model-owner", "-e", "x.example"], monkeypatch=monkeypatch)
    assert args.party == "model-owner" and args.enclave == "x.example" and args.path == "./adapter"


def test_shared_options_work_before_the_subcommand_and_from_env(monkeypatch):
    args = parse(["--party", "mo", "-e", "x.example", "status"], monkeypatch=monkeypatch)
    assert args.party == "mo" and args.enclave == "x.example"
    args = parse(["status"], env={"DBE_PARTY": "benchmark-owner", "DBE_ENCLAVE": "y.example"}, monkeypatch=monkeypatch)
    assert args.party == "benchmark-owner" and args.enclave == "y.example"
    args = parse(["--party", "mo", "status", "--party", "bo"], monkeypatch=monkeypatch)
    assert args.party == "bo"
    assert parse(["verify"], monkeypatch=monkeypatch).repo == "tinfoilsh/double-blind-eval"
