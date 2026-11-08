import io
import subprocess
from pathlib import Path

import httpx
import pytest

from omni_ai.cli import IO, main
from omni_ai.cli import checks as chk
from omni_ai.cli import models as mdl
from omni_ai.settings import OmniConfig, save_config


@pytest.fixture
def cfgdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OMNI_CONFIG_DIR", str(tmp_path / "cfg"))
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OMNI_GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "cfg"


def run(argv: list[str], answers: list[str] | None = None) -> tuple[int, str]:
    """出力と、問いかけ（prompt）の両方を 1 つの文字列にまとめて返す。"""
    out = io.StringIO()
    it = iter(answers or [])

    def ask(prompt: str) -> str:
        out.write(prompt)
        return next(it)

    rc = main(argv, IO(out=out, ask=ask))
    return rc, out.getvalue()


def test_config_path_and_validate(cfgdir: Path) -> None:
    rc, text = run(["config", "path"])
    assert rc == 0 and str(cfgdir / "config.toml") in text
    save_config({"client": {"chat": {"providers": ["ollama"]}}})
    rc, text = run(["config", "validate"])
    assert rc == 1 and "ollama has no default model" in text


def test_models_pull_is_confirmed_and_can_be_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mdl, "remote_size", lambda name, http=None: 9_530_000_000)
    pulled: list[str] = []
    monkeypatch.setattr(mdl, "pull", lambda name, **kw: pulled.append(name) or f"{name}: ok")
    rc, text = run(["models", "pull", "ollama:nimble"], answers=["n"])
    assert rc == 1 and "9.53 GB" in text and pulled == []
    rc, text = run(["models", "pull", "ollama:nimble"], answers=["y"])
    assert rc == 0 and pulled == ["ollama:nimble"]
    rc, _ = run(["models", "pull", "ollama:nimble", "--yes"])
    assert pulled == ["ollama:nimble", "ollama:nimble"]


def _fake_hf_cache(root: Path) -> None:
    """fastembed が作る形（共有 blobs と .refs）を再現する。"""
    shared = root / "blobs"
    for blob, size, owners in [
        ("aa/aa1", 100, ["models--A--x"]),
        ("bb/bb1", 50, ["models--A--x", "models--B--y"]),
    ]:
        p = shared / blob
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"0" * size)
        p.with_name(p.name + ".refs").write_text("\n".join(f"{o}/blobs/h" for o in owners), encoding="utf-8")
    for model, blobs in [("models--A--x", ["aa/aa1", "bb/bb1"]), ("models--B--y", ["bb/bb1"])]:
        snap = root / model / "snapshots" / "rev"
        snap.mkdir(parents=True)
        for i, b in enumerate(blobs):
            local = root / model / "blobs" / f"h{i}"
            local.parent.mkdir(parents=True, exist_ok=True)
            local.symlink_to(Path("../../blobs") / b)
            (snap / f"f{i}").symlink_to(Path("../../blobs") / f"h{i}")


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlinks")
def test_hf_remove_keeps_shared_blobs(tmp_path: Path) -> None:
    try:
        _fake_hf_cache(tmp_path)
    except OSError:
        pytest.skip("symlinks are not permitted here (Windows without developer mode)")
    listed = {m.repo: m.size_bytes for m in mdl.list_hf_cache(tmp_path)}
    assert listed == {"A/x": 150, "B/y": 50}
    freed = mdl.hf_remove(tmp_path / "models--A--x")
    assert freed == 100  # 自分だけが参照する blob だけを消す
    assert not (tmp_path / "models--A--x").exists()
    assert (tmp_path / "blobs" / "bb" / "bb1").exists()  # B が使っている blob は残る
    assert not (tmp_path / "blobs" / "aa" / "aa1").exists()
    assert {m.repo for m in mdl.list_hf_cache(tmp_path)} == {"B/y"}


def test_check_ollama_reports_missing_models_and_version() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.17.7"})
        return httpx.Response(200, json={"models": [{"name": "gemma4:31b-cloud"}]})

    cfg = OmniConfig.model_validate(
        {"ollama": {"base_url": "http://o.test"}, "client": {"judge": {"model": "tev1:0.8b"}}}
    )
    out = chk.check_ollama(cfg, client=httpx.Client(transport=httpx.MockTransport(handler)))
    status = {c.name: c.status for c in out}
    assert status["ollama"] == "ok" and status["ollama version"] == "fail"
    missing = next(c for c in out if c.name == "ollama model (judge)")
    assert missing.status == "fail" and "omni-ai models pull ollama:tev1:0.8b" in missing.hint


def test_check_ollama_unreachable_is_skip_when_unused() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    out = chk.check_ollama(None, client=httpx.Client(transport=httpx.MockTransport(boom)))
    assert out[0].status == "skip"


def test_check_docker_daemon_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chk.PlatformCompat, "resolve_docker", staticmethod(lambda: ("docker", {})))

    def runner(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, b"", b"Cannot connect to the Docker daemon")

    cfg = OmniConfig.model_validate({"client": {"sandbox": {}}})
    out = chk.check_docker(cfg, runner=runner)
    assert out[0].status == "fail" and "start Docker" in out[0].hint


def test_doctor_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        chk, "run_checks", lambda: [chk.Check("x", "ok", "fine"), chk.Check("y", "fail", "broken", "fix it")]
    )
    rc, text = run(["doctor"])
    assert rc == 1 and "✗ y" in text and "→ fix it" in text
