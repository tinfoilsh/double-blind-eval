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


def test_private_key_from_environment(monkeypatch, tmp_path):
    from dbe.canonical import generate_private_key, private_key_hex, public_key_hex
    from dbe.cli import _load_key

    key = generate_private_key()
    monkeypatch.setenv("DBE_PRIVATE_KEY", private_key_hex(key))
    loaded = _load_key("model-owner", None)
    assert public_key_hex(loaded.public_key()) == public_key_hex(key.public_key())
    monkeypatch.delenv("DBE_PRIVATE_KEY")
    monkeypatch.setenv("DBE_HOME", str(tmp_path))
    import dbe.client, dbe.cli

    monkeypatch.setattr(dbe.cli, "DBE_HOME", tmp_path)
    assert _load_key("model-owner", None) is None


def test_per_party_key_variables_and_precedence(monkeypatch, tmp_path):
    from dbe.canonical import generate_private_key, private_key_hex, public_key_hex
    from dbe.cli import _load_key

    bo, mo, generic = generate_private_key(), generate_private_key(), generate_private_key()
    monkeypatch.setenv("DBE_BENCHMARK_OWNER_KEY", private_key_hex(bo))
    monkeypatch.setenv("DBE_MODEL_OWNER_KEY", private_key_hex(mo))
    monkeypatch.setenv("DBE_PRIVATE_KEY", private_key_hex(generic))
    assert public_key_hex(_load_key("benchmark-owner", None).public_key()) == public_key_hex(bo.public_key())
    assert public_key_hex(_load_key("model-owner", None).public_key()) == public_key_hex(mo.public_key())
    monkeypatch.delenv("DBE_MODEL_OWNER_KEY")
    assert public_key_hex(_load_key("model-owner", None).public_key()) == public_key_hex(generic.public_key())
    explicit = tmp_path / "k.key"
    explicit.write_text(private_key_hex(bo))
    assert public_key_hex(_load_key("model-owner", str(explicit)).public_key()) == public_key_hex(bo.public_key())


def test_bad_key_variable_gives_a_clear_message(monkeypatch):
    import pytest

    from dbe.cli import _load_key

    monkeypatch.setenv("DBE_MODEL_OWNER_KEY", "<hex>")
    with pytest.raises(SystemExit) as exc:
        _load_key("model-owner", None)
    assert "DBE_MODEL_OWNER_KEY" in str(exc.value) and "64 hex" in str(exc.value) and "<hex>" in str(exc.value)
    from dbe.canonical import generate_private_key, private_key_hex

    monkeypatch.setenv("DBE_MODEL_OWNER_KEY", '"' + private_key_hex(generate_private_key()) + '"')
    assert _load_key("model-owner", None) is not None  # surrounding quotes are tolerated
