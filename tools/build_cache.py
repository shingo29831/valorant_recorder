"""Module: @role: ビルド工程の入出力ハッシュを管理し、差分がないステップのスキップと高速ZIP生成を担うモジュール"""
import argparse
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / ".build_cache"
MANIFEST_PATH = CACHE_DIR / "manifest.json"
HASH_CHUNK_SIZE = 1024 * 1024

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    ".env",
    "__pycache__",
    ".build_cache",
    "ai_meta",
    "Output",
    "main.build",
    "main.dist",
    "main.onefile-build",
    "records",
    "extracted_logs",
}


class BuildStep(str, Enum):
    PIP = "pip"
    DEEPFILTER = "deepfilter"
    CPP = "cpp"
    NUITKA = "nuitka"
    INNO = "inno"


def _to_rel_posix(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _hash_file(path: Path, stat_cache: Dict[str, Dict[str, Any]]) -> str:
    key = _to_rel_posix(path)
    st = path.stat()
    cached = stat_cache.get(key)
    if cached and cached.get("size") == st.st_size and cached.get("mtime_ns") == st.st_mtime_ns:
        return str(cached["sha256"])

    digest = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    sha = digest.hexdigest()
    stat_cache[key] = {
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "sha256": sha,
    }
    return sha


def _load_manifest() -> Dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        return {"stat_cache": {}, "steps": {}}
    try:
        with MANIFEST_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"stat_cache": {}, "steps": {}}
        data.setdefault("stat_cache", {})
        data.setdefault("steps", {})
        return data
    except (OSError, json.JSONDecodeError) as e:
        print(f"[Cache] Warning: failed to load manifest ({e}); treating cache as cold.")
        return {"stat_cache": {}, "steps": {}}


def _save_manifest(manifest: Dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = MANIFEST_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    os.replace(tmp_path, MANIFEST_PATH)


def _collect_tree_files(root_dir: Path, suffixes: Optional[Tuple[str, ...]] = None, exclude_dirs: Optional[set[str]] = None) -> List[Path]:
    if not root_dir.exists():
        return []
    result: List[Path] = []
    excluded = exclude_dirs or set()
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = sorted(d for d in dirnames if d not in excluded)
        for name in sorted(filenames):
            if suffixes and not name.lower().endswith(suffixes):
                continue
            result.append(Path(dirpath) / name)
    return result


def _find_deepfilter_src_dir() -> Optional[Path]:
    df_src_env = os.environ.get("DEEPFILTER_SRC")
    if df_src_env:
        cand = Path(df_src_env)
        return cand if cand.exists() else None
    sibling = PROJECT_ROOT.parent / "DeepFilterNet"
    if sibling.exists():
        return sibling
    fallback = Path(r"C:\Users\Shingo\Projects\DeepFilterNet")
    if fallback.exists():
        return fallback
    return None


def _collect_step_spec(step: BuildStep, extra: str, stat_cache: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, str], Dict[str, str], List[Path]]:
    inputs: Dict[str, str] = {}
    meta: Dict[str, str] = {"extra": extra}
    outputs: List[Path] = []

    def add_files(paths: List[Path]) -> None:
        for p in paths:
            if p.is_file():
                inputs[_to_rel_posix(p)] = _hash_file(p, stat_cache)
            else:
                inputs[_to_rel_posix(p)] = "<MISSING>"

    if step == BuildStep.PIP:
        add_files([PROJECT_ROOT / "requirements.txt"])
        meta["python_exe"] = sys.executable
        meta["python_ver"] = sys.version.split()[0]

    elif step == BuildStep.DEEPFILTER:
        add_files([PROJECT_ROOT / "setup_deepfilter.py"])
        df_base = PROJECT_ROOT / "cpp" / "audio_processor"
        outputs = [
            df_base / "bin" / "deep_filter.dll",
            df_base / "lib" / "deep_filter.lib",
            df_base / "include" / "deep_filter.h",
            df_base / "models" / "DeepFilterNet3" / "DeepFilterNet3_onnx.tar.gz",
        ]
        src_dir = _find_deepfilter_src_dir()
        if src_dir and (src_dir / "target").exists():
            for dirpath, dirnames, filenames in os.walk(src_dir / "target"):
                dirnames[:] = sorted(d for d in dirnames if d not in ("deps", "build"))
                for name in sorted(filenames):
                    lower = name.lower()
                    if name == "deep_filter.h" or (
                        ("deep_filter" in lower or "deepfilter" in lower)
                        and lower.endswith((".dll", ".lib", ".dll.lib"))
                    ):
                        p = Path(dirpath) / name
                        inputs[f"df_src:{p}"] = _hash_file(p, stat_cache)

    elif step == BuildStep.CPP:
        cpp_dir = PROJECT_ROOT / "cpp" / "audio_processor"
        src_files: List[Path] = []
        for pattern in ("*.cpp", "*.h", "CMakeLists.txt"):
            src_files.extend(sorted(cpp_dir.glob(pattern)))
        for sub in ("bin", "lib", "include"):
            src_files.extend(_collect_tree_files(cpp_dir / sub))
        add_files(src_files)
        outputs = [
            cpp_dir / "build" / "Release" / "audio_processor.dll",
            cpp_dir / "build" / "Release" / "native_recorder.dll",
        ]

    elif step == BuildStep.NUITKA:
        py_files = _collect_tree_files(
            PROJECT_ROOT,
            suffixes=(".py",),
            exclude_dirs=EXCLUDED_DIRS | {"cpp"},
        )
        # cpp配下のbuild生成物を除いたPythonスクリプトがあれば含める
        cpp_py_files = _collect_tree_files(
            PROJECT_ROOT / "cpp",
            suffixes=(".py",),
            exclude_dirs={"build", "out", "__pycache__"},
        )
        asset_files = _collect_tree_files(PROJECT_ROOT / "assets")
        model_files = _collect_tree_files(PROJECT_ROOT / "cpp" / "audio_processor" / "models")
        dll_dir = PROJECT_ROOT / "cpp" / "audio_processor" / "build" / "Release"
        dll_files = sorted(dll_dir.glob("*.dll")) if dll_dir.exists() else []
        add_files(
            py_files
            + cpp_py_files
            + asset_files
            + model_files
            + dll_files
            + [PROJECT_ROOT / "auth.key", PROJECT_ROOT / "requirements.txt"]
        )
        meta["python_exe"] = sys.executable
        meta["python_ver"] = sys.version.split()[0]
        outputs = [PROJECT_ROOT / "ValoReco.exe"]

    elif step == BuildStep.INNO:
        add_files([
            PROJECT_ROOT / "ValoReco.exe",
            PROJECT_ROOT / "installer.iss",
            PROJECT_ROOT / "assets" / "icon.ico",
        ])
        outputs = [PROJECT_ROOT / "Output" / "ValoReco_Setup.exe"]

    return inputs, meta, outputs


def _hash_outputs(outputs: List[Path], stat_cache: Dict[str, Dict[str, Any]]) -> Tuple[bool, Dict[str, str]]:
    out_hashes: Dict[str, str] = {}
    all_valid = True
    for p in outputs:
        if not p.is_file() or p.stat().st_size == 0:
            all_valid = False
            out_hashes[_to_rel_posix(p)] = "<MISSING>"
        else:
            out_hashes[_to_rel_posix(p)] = _hash_file(p, stat_cache)
    return all_valid, out_hashes


def check_step(step: BuildStep, extra: str, force: bool) -> int:
    if force:
        print(f"[Cache] {step.value}: forced rebuild requested.")
        return 1

    manifest = _load_manifest()
    prev = manifest.get("steps", {}).get(step.value)
    if not prev:
        print(f"[Cache] {step.value}: no previous build cache -> build required.")
        return 1

    # 失敗時の再検証に備え、checkではstat_cacheを破壊更新せずコピーを使う
    working_stat_cache = dict(manifest.get("stat_cache", {}))
    inputs, meta, outputs = _collect_step_spec(step, extra, working_stat_cache)
    outputs_ok, out_hashes = _hash_outputs(outputs, working_stat_cache)

    if not outputs_ok:
        missing = [k for k, v in out_hashes.items() if v == "<MISSING>"]
        print(f"[Cache] {step.value}: missing output ({', '.join(missing[:3])}) -> build required.")
        return 1

    if step == BuildStep.DEEPFILTER:
        tar_path = PROJECT_ROOT / "cpp" / "audio_processor" / "models" / "DeepFilterNet3" / "DeepFilterNet3_onnx.tar.gz"
        if tar_path.stat().st_size < 1024:
            print(f"[Cache] {step.value}: model archive too small -> build required.")
            return 1

    prev_inputs = prev.get("inputs", {})
    prev_meta = prev.get("meta", {})
    prev_outputs = prev.get("outputs", {})

    if prev_meta != meta:
        print(f"[Cache] {step.value}: build configuration changed -> build required.")
        return 1

    if prev_inputs != inputs:
        changed = [k for k in sorted(set(inputs) | set(prev_inputs)) if inputs.get(k) != prev_inputs.get(k)]
        preview = ", ".join(changed[:3]) + (" ..." if len(changed) > 3 else "")
        print(f"[Cache] {step.value}: input changed ({preview}) -> build required.")
        return 1

    if prev_outputs != out_hashes:
        print(f"[Cache] {step.value}: output artifact modified -> build required.")
        return 1

    print(f"[Cache] {step.value}: up-to-date -> skipped.")
    return 0


def mark_step(step: BuildStep, extra: str) -> int:
    manifest = _load_manifest()
    stat_cache = manifest.setdefault("stat_cache", {})
    inputs, meta, outputs = _collect_step_spec(step, extra, stat_cache)
    outputs_ok, out_hashes = _hash_outputs(outputs, stat_cache)
    if not outputs_ok:
        print(f"[Cache] {step.value}: warning - output missing when marking cache.")
        return 1

    manifest.setdefault("steps", {})[step.value] = {
        "inputs": inputs,
        "meta": meta,
        "outputs": out_hashes,
    }
    _save_manifest(manifest)
    print(f"[Cache] {step.value}: cache updated.")
    return 0


def make_zip(src_rel: str, dest_rel: str, force: bool) -> int:
    src_path = (PROJECT_ROOT / src_rel).resolve()
    dest_path = (PROJECT_ROOT / dest_rel).resolve()
    if not src_path.is_file():
        print(f"[Error] Source file not found for zip: {src_path}")
        return 1

    manifest = _load_manifest()
    stat_cache = manifest.setdefault("stat_cache", {})
    src_hash = _hash_file(src_path, stat_cache)
    step_key = f"zip:{_to_rel_posix(dest_path)}"
    prev = manifest.get("steps", {}).get(step_key)

    if not force and prev and dest_path.is_file():
        dest_hash = _hash_file(dest_path, stat_cache)
        if prev.get("src_hash") == src_hash and prev.get("dest_hash") == dest_hash:
            print(f"[Cache] {dest_rel}: up-to-date -> skipped.")
            return 0

    tmp_zip = dest_path.with_suffix(".zip.tmp")
    if tmp_zip.exists():
        tmp_zip.unlink()

    print(f"[Zip] Creating {dest_rel} from {src_rel}...")
    # 既に圧縮済みのバイナリに対して高速レベルでZIP梱包する
    with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        zf.write(src_path, arcname=src_path.name)
    os.replace(tmp_zip, dest_path)

    dest_hash = _hash_file(dest_path, stat_cache)
    manifest.setdefault("steps", {})[step_key] = {
        "src_hash": src_hash,
        "dest_hash": dest_hash,
    }
    _save_manifest(manifest)
    print(f"[Zip] Created {dest_rel} successfully.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build cache manager for ValoReco")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_p = subparsers.add_parser("check")
    check_p.add_argument("step", choices=[s.value for s in BuildStep])
    check_p.add_argument("--extra", default="")
    check_p.add_argument("--force", action="store_true")

    mark_p = subparsers.add_parser("mark")
    mark_p.add_argument("step", choices=[s.value for s in BuildStep])
    mark_p.add_argument("--extra", default="")

    zip_p = subparsers.add_parser("make-zip")
    zip_p.add_argument("src")
    zip_p.add_argument("dest")
    zip_p.add_argument("--force", action="store_true")

    args = parser.parse_args()
    if args.command == "check":
        return check_step(BuildStep(args.step), args.extra, args.force)
    if args.command == "mark":
        return mark_step(BuildStep(args.step), args.extra)
    if args.command == "make-zip":
        return make_zip(args.src, args.dest, args.force)
    return 1


if __name__ == "__main__":
    sys.exit(main())
