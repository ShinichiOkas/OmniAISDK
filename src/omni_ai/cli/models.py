"""モデルの取得・一覧・削除（omni-ai models）。

- hf: fastembed / faster-whisper が使う Hugging Face のキャッシュ（OS 標準のキャッシュ配下 models/）
- ollama: Ollama のモデル（/api/pull・/api/tags・/api/delete）
- docker: Sandbox のイメージ
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..compat import PlatformCompat
from ..providers.fastembed._lazy import default_cache_dir

__all__ = ["CATALOG", "CachedModel", "hf_remove", "list_hf_cache", "remote_size"]

# 名前 → (種類, 実体)。pull / size で使う既知のモデル
CATALOG: dict[str, tuple[str, str]] = {
    "fastembed-embedding": (
        "fastembed-embedding",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    ),
    "fastembed-rerank": ("fastembed-rerank", "BAAI/bge-reranker-v2-m3-int8"),
    "whisper-medium": ("whisper", "medium"),
    "whisper-small": ("whisper", "small"),
    "whisper-base": ("whisper", "base"),
}


@dataclass
class CachedModel:
    repo: str
    path: Path
    size_bytes: int


def _blob_target(link: Path) -> Path | None:
    try:
        return (link.parent / link.readlink()).resolve() if link.is_symlink() else None
    except OSError:
        return None


def _model_blobs(model_dir: Path) -> set[Path]:
    blobs = set()
    for p in model_dir.rglob("*"):
        t = _blob_target(p)
        if t is not None:
            blobs.add(t)
            u = _blob_target(t)  # 共有 blobs へのリンクの先
            if u is not None:
                blobs.add(u)
    return blobs


def _size(paths: set[Path]) -> int:
    return sum(p.stat().st_size for p in paths if p.is_file())


def list_hf_cache(cache_dir: Path | None = None) -> list[CachedModel]:
    root = cache_dir or default_cache_dir()
    if not root.exists():
        return []
    out = []
    for d in sorted(root.glob("models--*")):
        repo = d.name.removeprefix("models--").replace("--", "/")
        out.append(CachedModel(repo, d, _size(_model_blobs(d))))
    return out


def hf_remove(repo_dir: Path) -> int:
    """モデルのディレクトリと、そのモデルだけが参照している共有 blob を消す。消したバイト数を返す。"""
    blobs = _model_blobs(repo_dir)
    removed = 0
    root = repo_dir.parent
    me = repo_dir.name
    for b in blobs:
        refs = b.with_name(b.name + ".refs")
        if refs.exists():  # 共有 blob: 参照元が自分だけなら消す
            owners = {
                line.split("/")[0] for line in refs.read_text(encoding="utf-8").splitlines() if line.strip()
            }
            if owners - {me}:
                continue
            for extra in (refs, b.with_name(b.name + ".lock")):
                extra.unlink(missing_ok=True)
        if b.is_file() and root in b.parents:
            removed += b.stat().st_size
            b.unlink()
    shutil.rmtree(repo_dir, ignore_errors=True)
    lock_dir = root / ".locks" / me
    shutil.rmtree(lock_dir, ignore_errors=True)
    return removed


def _hf_repo_size(repo: str, file_filter: str | None = None) -> int | None:
    try:
        with urllib.request.urlopen(f"https://huggingface.co/api/models/{repo}?blobs=true", timeout=20) as r:
            data = json.load(r)
    except OSError:
        return None
    sizes = [
        s.get("size") or 0
        for s in data.get("siblings", [])
        if file_filter is None or s["rfilename"] == file_filter or not s["rfilename"].startswith("onnx/")
    ]
    return sum(sizes) or None


def remote_size(name: str, http: httpx.Client | None = None) -> int | None:
    """取得する前に分かるダウンロードの大きさ（バイト）。分からなければ None。"""
    if name in CATALOG:
        kind, target = CATALOG[name]
        if kind == "whisper":
            return _hf_repo_size(f"Systran/faster-whisper-{target}")
        try:
            if kind == "fastembed-rerank":
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                supported: list[Any] = list(TextCrossEncoder.list_supported_models())
            else:
                from fastembed import TextEmbedding

                supported = list(TextEmbedding.list_supported_models())
            desc = next(m for m in supported if m["model"] == target)
            return int(float(desc["size_in_GB"]) * 1e9)
        except (ImportError, StopIteration, KeyError, ValueError):
            return None
    if name.startswith("ollama:"):
        model = name.removeprefix("ollama:")
        repo, _, tag = model.partition(":")
        path = repo if "/" in repo else f"library/{repo}"
        client = http or httpx.Client(timeout=20)
        try:
            r = client.get(
                f"https://registry.ollama.ai/v2/{path}/manifests/{tag or 'latest'}",
                headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
            )
            d = r.json()
            return int(sum(layer["size"] for layer in d["layers"]) + d["config"]["size"])
        except (httpx.HTTPError, KeyError, ValueError):
            return None
    return None


def pull(name: str, *, ollama_base: str | None = None) -> str:
    """モデルを取得する。戻り値は人向けの結果の 1 行。"""
    if name in CATALOG:
        kind, target = CATALOG[name]
        if kind == "fastembed-embedding":
            from ..providers.fastembed import FastEmbedEmbeddingAdapter

            FastEmbedEmbeddingAdapter(model=target).load()
        elif kind == "fastembed-rerank":
            from ..providers.fastembed.rerank import FastEmbedRerankAdapter

            FastEmbedRerankAdapter(model=target).load()
        else:
            from ..providers.faster_whisper import FasterWhisperAudioAdapter

            FasterWhisperAudioAdapter(model=target).load()
        return f"{name}: ready ({default_cache_dir()})"
    if name.startswith("ollama:"):
        base = ollama_base or PlatformCompat.resolve_ollama_base_url()
        r = httpx.post(
            f"{base}/api/pull", json={"model": name.removeprefix("ollama:"), "stream": False}, timeout=None
        )
        r.raise_for_status()
        return f"{name}: {r.json().get('status', 'done')}"
    if name.startswith("docker:"):
        from ..providers.docker import DockerSandboxAdapter

        DockerSandboxAdapter(image=name.removeprefix("docker:")).ensure_image()
        return f"{name}: ready"
    raise ValueError(f"unknown model {name!r} (see `omni-ai models catalog`)")


def remove(name: str, *, ollama_base: str | None = None) -> str:
    if name.startswith("ollama:"):
        base = ollama_base or PlatformCompat.resolve_ollama_base_url()
        r = httpx.request(
            "DELETE", f"{base}/api/delete", json={"model": name.removeprefix("ollama:")}, timeout=60
        )
        r.raise_for_status()
        return f"{name}: removed"
    if name.startswith("docker:"):
        docker, env = PlatformCompat.resolve_docker()
        p = subprocess.run(
            [docker, "image", "rm", name.removeprefix("docker:")], capture_output=True, env=env, timeout=60
        )
        return f"{name}: {'removed' if p.returncode == 0 else p.stderr.decode(errors='replace').strip()}"
    repo = CATALOG[name][1] if name in CATALOG else name
    if CATALOG.get(name, ("",))[0] == "whisper":
        repo = f"Systran/faster-whisper-{repo}"
    for m in list_hf_cache():
        if m.repo.lower() == repo.lower() or m.repo.lower().endswith(repo.split("/")[-1].lower()):
            freed = hf_remove(m.path)
            return f"{name}: removed {m.repo} ({freed / 1e6:.0f} MB freed)"
    return f"{name}: not in cache ({default_cache_dir()})"


def ollama_models(ollama_base: str | None = None) -> list[dict[str, Any]]:
    base = ollama_base or PlatformCompat.resolve_ollama_base_url()
    try:
        return list(httpx.get(f"{base}/api/tags", timeout=5).json().get("models", []))
    except (httpx.HTTPError, ValueError):
        return []
