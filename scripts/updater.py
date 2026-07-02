#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
UPDATE_DIR = RUNTIME_DIR / "updates"
UPDATE_STATE = RUNTIME_DIR / "update_state.json"
BACKUP_DIR = RUNTIME_DIR / "update_backup"
FINALIZE_SCRIPT = RUNTIME_DIR / "finalize_update"

GITHUB_REPO = "0BlueYan0/Modpack_Translator"
RELEASE_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"
ASSET_PREFIX = "Modpack_Translator"

PRESERVE_TOP_LEVEL = {
    ".git",
    ".runtime",
    ".venv",
    "venv",
    "env",
    "outputs",
    "Failed Items",
    "mods_bak",
    "quests_bak",
    "data",
    "logs",
}
PRESERVE_FILES = {".env"}

# 就地合併（不先刪除）的頂層目錄：更新內建檔案，但保留使用者自行放入的檔案
# （例如放在 adapter/ 裡的自訂 LoRA）。
PRESERVE_MERGE_DIRS = {"adapter"}

# 目錄整體更新、但保留舊值的使用者設定檔（相對路徑）。
# configs/model.yaml 存有 base_gguf_path 等機器特定設定，被重置會迫使
# 使用者重新下載 ~5GB 基礎模型；新版新增的欄位由 pydantic 預設值補上。
PRESERVE_RELATIVE_FILES = {
    ("configs", "model.yaml"),
    ("configs", "paths.yaml"),
}


class DownloadCancelled(RuntimeError):
    """使用者中途取消下載。"""


def _log(message: str) -> None:
    """更新流程以分離程序執行、stdout/stderr 皆為 DEVNULL，
    此 log 檔是失敗時唯一的診斷線索。"""
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        with (RUNTIME_DIR / "updater.log").open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_pid_exit(pid: int, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.3)


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    tag_name: str
    release_url: str
    notes: str
    asset_name: str
    asset_url: str
    asset_size: int
    sha256: str | None = None


def normalize_version(value: str) -> str:
    value = value.strip()
    return value[1:] if value.lower().startswith("v") else value


def version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in normalize_version(value).split("."):
        number = ""
        for char in chunk:
            if not char.isdigit():
                break
            number += char
        parts.append(int(number or "0"))
    return tuple(parts)


def is_newer_version(latest: str, current: str) -> bool:
    left = version_tuple(latest)
    right = version_tuple(current)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def _current_version_for_agent() -> str:
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        from modpack_translator.version import __version__

        return __version__
    except Exception:
        return "unknown"


def _request_json(url: str, timeout: float = 8.0) -> dict:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Modpack-Translator-Updater/{_current_version_for_agent()}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_text(url: str, timeout: float = 8.0) -> str:
    request = Request(
        url,
        headers={"User-Agent": f"Modpack-Translator-Updater/{_current_version_for_agent()}"},
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _select_asset(release: dict) -> tuple[dict, str | None]:
    tag = str(release.get("tag_name") or "")
    normalized = normalize_version(tag)
    assets = release.get("assets") or []
    # 精確檔名優先，避免同 release 有多個相似 zip 時取到錯的
    zip_assets = [
        asset
        for asset in assets
        if str(asset.get("name", "")) == f"{ASSET_PREFIX}-v{normalized}.zip"
    ]
    if not zip_assets:
        zip_assets = [
            asset
            for asset in assets
            if str(asset.get("name", "")).lower().endswith(".zip")
            and str(asset.get("name", "")).startswith(f"{ASSET_PREFIX}-v{normalized}")
        ]
    if not zip_assets:
        zip_assets = [
            asset
            for asset in assets
            if str(asset.get("name", "")).lower().endswith(".zip")
            and str(asset.get("name", "")).startswith(ASSET_PREFIX)
        ]
    if not zip_assets:
        raise RuntimeError(
            f"No release asset named {ASSET_PREFIX}-v{normalized}.zip was found."
        )

    zip_asset = zip_assets[0]
    sha_asset = next(
        (asset for asset in assets if asset.get("name") == f"{zip_asset['name']}.sha256"),
        None,
    )
    sha256: str | None = None
    if sha_asset:
        sha_text = _download_text(str(sha_asset["browser_download_url"]))
        sha256 = sha_text.strip().split()[0].lower()
    elif isinstance(zip_asset.get("digest"), str) and zip_asset["digest"].startswith("sha256:"):
        sha256 = zip_asset["digest"].split(":", 1)[1].lower()
    return zip_asset, sha256


def check_for_update(current_version: str, raise_errors: bool = False) -> UpdateInfo | None:
    """查詢 GitHub Releases 是否有新版本。

    raise_errors=False（預設）：任何網路 / 資產錯誤一律回傳 None（啟動時靜默檢查用）。
    raise_errors=True：網路錯誤或找不到更新資產時拋出例外，讓手動檢查能區分
    「已是最新」與「檢查失敗」。
    """
    try:
        release = _request_json(RELEASE_API_URL)
    except HTTPError as exc:
        if raise_errors:
            if exc.code in (403, 429):
                raise RuntimeError(
                    "GitHub API 已達流量限制（rate limit），請稍後再試。"
                ) from exc
            raise RuntimeError(f"無法連線 GitHub 檢查更新：{exc}") from exc
        return None
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        # ValueError 涵蓋 JSONDecodeError（例如強制門戶回傳非 JSON 的 200 回應）
        if raise_errors:
            raise RuntimeError(f"無法連線 GitHub 檢查更新：{exc}") from exc
        return None

    if release.get("draft") or release.get("prerelease"):
        return None

    tag_name = str(release.get("tag_name") or "")
    if not tag_name or not is_newer_version(tag_name, current_version):
        return None

    try:
        asset, sha256 = _select_asset(release)
    except Exception as exc:
        if raise_errors:
            raise RuntimeError(
                f"發現新版本 {tag_name}，但無法取得更新檔：{exc}"
            ) from exc
        return None

    return UpdateInfo(
        version=normalize_version(tag_name),
        tag_name=tag_name,
        release_url=str(release.get("html_url") or RELEASES_URL),
        notes=str(release.get("body") or ""),
        asset_name=str(asset["name"]),
        asset_url=str(asset["browser_download_url"]),
        asset_size=int(asset.get("size") or 0),
        sha256=sha256,
    )


def download_update(info: UpdateInfo, progress_cb=None) -> Path:
    """下載更新 zip 並驗證 sha256。

    progress_cb(downloaded_bytes, total_bytes) -> bool：每讀取一個區塊呼叫一次，
    回傳 False 表示取消下載（拋出 DownloadCancelled 並清除暫存檔）。
    """
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPDATE_DIR / info.asset_name
    tmp = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()

    request = Request(
        info.asset_url,
        headers={"User-Agent": f"Modpack-Translator-Updater/{_current_version_for_agent()}"},
    )
    downloaded = 0
    try:
        with urlopen(request, timeout=30) as response, tmp.open("wb") as fh:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                fh.write(chunk)
                downloaded += len(chunk)
                if progress_cb is not None and not progress_cb(downloaded, info.asset_size):
                    raise DownloadCancelled("下載已取消")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    if info.asset_size and downloaded != info.asset_size:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"下載大小不符：預期 {info.asset_size} bytes，實得 {downloaded} bytes。"
        )

    actual = digest.hexdigest()
    if info.sha256 and actual.lower() != info.sha256.lower():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"Update checksum mismatch: expected {info.sha256}, got {actual}."
        )

    tmp.replace(dest)
    UPDATE_STATE.parent.mkdir(parents=True, exist_ok=True)
    UPDATE_STATE.write_text(
        json.dumps(
            {
                "version": info.version,
                "tag_name": info.tag_name,
                "asset_name": info.asset_name,
                "asset_path": str(dest),
                "sha256": actual,
                "downloaded_at": int(time.time()),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return dest


def launch_apply_update(zip_path: Path, restart: bool = True) -> subprocess.Popen:
    # --wait-pid：讓 apply 程序先等 GUI 完全退出再動檔案，
    # 避免 .venv 刪除 / 檔案覆蓋與仍在執行的 GUI 競態
    args = [
        sys.executable,
        str(Path(__file__).resolve()),
        "apply",
        str(zip_path),
        f"--wait-pid={os.getpid()}",
    ]
    if restart:
        args.append("--restart")
    return subprocess.Popen(
        args,
        cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=(os.name != "nt"),
    )


def _prune_backups(keep: int = 2) -> None:
    """只保留最近 keep 份更新備份，避免 .runtime/update_backup 無限膨脹。"""
    if not BACKUP_DIR.exists():
        return
    backups = sorted(p for p in BACKUP_DIR.iterdir() if p.is_dir())
    for old in backups[:-keep] if keep > 0 else backups:
        shutil.rmtree(old, ignore_errors=True)


def _safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members: list[zipfile.ZipInfo] = []
    for info in zf.infolist():
        path = Path(info.filename)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"Unsafe path in update archive: {info.filename}")
        members.append(info)
    return members


def _archive_root(members: list[zipfile.ZipInfo]) -> str:
    roots = {
        Path(info.filename).parts[0]
        for info in members
        if Path(info.filename).parts and not info.filename.endswith("/")
    }
    return next(iter(roots)) if len(roots) == 1 else ""


def _copy_tree_contents(src_root: Path, dest_root: Path, preserve: bool = True) -> None:
    """把 src_root 的內容覆蓋到 dest_root。

    preserve=True（套用更新）：合併 PRESERVE_MERGE_DIRS、保留 PRESERVE_RELATIVE_FILES。
    preserve=False（從備份還原）：純粹整體換回備份內容——還原時 dest 是半安裝的
    新版檔案，若再走保留邏輯會把新版設定檔誤存回使用者設定。
    """
    # 頂層目錄名稱 → 該目錄下需保留的相對路徑集合
    preserve_by_dir: dict[str, set[str]] = {}
    if preserve:
        for parts in PRESERVE_RELATIVE_FILES:
            preserve_by_dir.setdefault(parts[0], set()).add(str(Path(*parts[1:])))

    for src in src_root.iterdir():
        name = src.name
        if name in PRESERVE_TOP_LEVEL or name in PRESERVE_FILES:
            continue
        dest = dest_root / name

        # 合併目錄：覆蓋內建檔案，但不刪除使用者自行放入的檔案
        if preserve and name in PRESERVE_MERGE_DIRS and src.is_dir() and dest.is_dir() and not dest.is_symlink():
            shutil.copytree(src, dest, dirs_exist_ok=True)
            continue

        # 目錄整體換新前，先暫存其中要保留的使用者設定檔
        stash: list[tuple[Path, bytes]] = []
        if src.is_dir() and dest.is_dir():
            for rel in preserve_by_dir.get(name, ()):
                old = dest / rel
                if old.is_file():
                    stash.append((old, old.read_bytes()))

        if dest.exists():
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)

        for path, blob in stash:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)


def _write_finalize_script(restart: bool) -> Path:
    current_pid = os.getpid()
    if os.name == "nt":
        script = FINALIZE_SCRIPT.with_suffix(".ps1")
        script.write_text(
            f"""
$ErrorActionPreference = "Continue"
while (Get-Process -Id {current_pid} -ErrorAction SilentlyContinue) {{
    Start-Sleep -Milliseconds 300
}}
Set-Location -LiteralPath {str(PROJECT_ROOT)!r}
$log = ".runtime\\updater.log"
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] finalize: cleaning old environment"
if (Test-Path -LiteralPath ".venv") {{
    Remove-Item -LiteralPath ".venv" -Recurse -Force -ErrorAction SilentlyContinue
}}
Remove-Item -LiteralPath ".runtime\\backend.json" -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath ".runtime\\llama-server.log" -Force -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath ".runtime\\llama_cpp_amd") {{
    Remove-Item -LiteralPath ".runtime\\llama_cpp_amd" -Recurse -Force -ErrorAction SilentlyContinue
}}
if (Test-Path -LiteralPath ".runtime\\downloads") {{
    Remove-Item -LiteralPath ".runtime\\downloads" -Recurse -Force -ErrorAction SilentlyContinue
}}
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] finalize: running setup"
& ".\\setup_windows.bat" *>> $log
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] finalize: setup exit code $LASTEXITCODE"
if ($LASTEXITCODE -eq 0 -and ${str(bool(restart)).lower()}) {{
    Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] finalize: restarting app"
    Start-Process -FilePath "uv" -ArgumentList @("run", "python", "main.py") -WorkingDirectory {str(PROJECT_ROOT)!r} -WindowStyle Hidden
}}
""".lstrip(),
            # utf-8-sig：Windows PowerShell 5.1 讀無 BOM 檔會用 ANSI 代碼頁，
            # 中文安裝路徑會被誤解碼導致重啟失敗
            encoding="utf-8-sig",
        )
        return script

    script = FINALIZE_SCRIPT.with_suffix(".sh")
    script.write_text(
        f"""#!/usr/bin/env sh
while kill -0 {current_pid} 2>/dev/null; do
  sleep 0.3
done
cd {str(PROJECT_ROOT)!r} || exit 1
log=".runtime/updater.log"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] finalize: cleaning old environment" >> "$log"
rm -rf .venv
rm -f .runtime/backend.json .runtime/llama-server.log
rm -rf .runtime/llama_cpp_amd .runtime/downloads
chmod +x ./setup_unix.sh 2>/dev/null || true
echo "[$(date '+%Y-%m-%d %H:%M:%S')] finalize: running setup" >> "$log"
./setup_unix.sh >> "$log" 2>&1
status=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S')] finalize: setup exit $status" >> "$log"
if [ "$status" -eq 0 ] && [ "{'1' if restart else '0'}" = "1" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] finalize: restarting app" >> "$log"
  nohup uv run python main.py >/dev/null 2>&1 &
fi
exit "$status"
""",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | 0o111)
    return script


def _launch_finalize_script(restart: bool) -> None:
    script = _write_finalize_script(restart)
    if os.name == "nt":
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        subprocess.Popen(
            ["sh", str(script)],
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def apply_update(
    zip_path: Path,
    restart: bool,
    refresh_environment: bool = True,
    wait_pid: int | None = None,
) -> None:
    zip_path = zip_path.resolve()
    if not zip_path.exists():
        raise FileNotFoundError(zip_path)

    _log(f"apply start: {zip_path.name}")
    if wait_pid:
        _wait_for_pid_exit(wait_pid)
        _log(f"gui process {wait_pid} exited")

    apply_dir = UPDATE_DIR / "apply"
    if apply_dir.exists():
        shutil.rmtree(apply_dir)
    apply_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        members = _safe_members(zf)
        zf.extractall(apply_dir, members=members)
    root = _archive_root(members)
    src_root = apply_dir / root if root else apply_dir

    # 只備份更新會動到的項目；使用者放在根目錄的其他檔案（例如大型模型檔）
    # 不會被覆蓋，也就不需要備份
    incoming_names = {p.name for p in src_root.iterdir()}
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_root = BACKUP_DIR / time.strftime("%Y%m%d-%H%M%S")
    backup_root.mkdir(parents=True, exist_ok=True)
    for item in PROJECT_ROOT.iterdir():
        if item.name in PRESERVE_TOP_LEVEL or item.name in PRESERVE_FILES:
            continue
        if item.name not in incoming_names:
            continue
        target = backup_root / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(item, target)
    _prune_backups(keep=2)
    _log(f"backup done: {backup_root.name}")

    try:
        _copy_tree_contents(src_root, PROJECT_ROOT)
    except BaseException as exc:
        # 套用中途失敗：從備份還原，避免留下半新半舊、無法啟動的安裝
        _log(f"copy failed: {exc!r}; restoring backup {backup_root.name}")
        try:
            _copy_tree_contents(backup_root, PROJECT_ROOT, preserve=False)
            _log("restore complete")
        except BaseException as restore_exc:
            _log(f"RESTORE FAILED: {restore_exc!r}; manual restore from {backup_root}")
        raise
    _log("copy done")

    UPDATE_STATE.write_text(
        json.dumps(
            {
                "applied_at": int(time.time()),
                "zip_path": str(zip_path),
                "backup_path": str(backup_root),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    if refresh_environment:
        _launch_finalize_script(restart)
    elif restart:
        subprocess.Popen(
            [sys.executable, str(PROJECT_ROOT / "main.py")],
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=(os.name != "nt"),
        )


def _cmd_check(args: argparse.Namespace) -> int:
    info = check_for_update(args.current_version)
    if info is None:
        return 1
    print(json.dumps(info.__dict__, ensure_ascii=False, indent=2))
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    info = check_for_update(args.current_version)
    if info is None:
        return 1
    print(download_update(info))
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    apply_update(
        Path(args.zip_path),
        restart=args.restart,
        refresh_environment=not args.keep_environment,
        wait_pid=args.wait_pid,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Modpack Translator updater")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Check GitHub Releases for an update")
    check.add_argument("--current-version", default=_current_version_for_agent())
    check.set_defaults(func=_cmd_check)

    download = sub.add_parser("download", help="Download the latest update")
    download.add_argument("--current-version", default=_current_version_for_agent())
    download.set_defaults(func=_cmd_download)

    apply = sub.add_parser("apply", help="Apply a downloaded update zip")
    apply.add_argument("zip_path")
    apply.add_argument("--restart", action="store_true")
    apply.add_argument("--keep-environment", action="store_true")
    apply.add_argument("--wait-pid", type=int, default=None,
                       help="Wait for this PID to exit before touching files")
    apply.set_defaults(func=_cmd_apply)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
