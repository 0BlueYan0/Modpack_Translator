from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from urllib.error import URLError

import pytest

# scripts/ 是 namespace package，需要專案根目錄在 sys.path 上
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import updater
from scripts.updater import (
    DownloadCancelled,
    UpdateInfo,
    check_for_update,
    download_update,
    is_newer_version,
)


# ────────────────────────────────────────────── 基本常數 / 版本比較


def test_github_repo_points_to_current_account():
    # 舊帳號 Koudesuk 的 releases 停在 v1.4.1，指錯 repo 會讓更新檢查永遠找不到新版
    assert updater.GITHUB_REPO == "0BlueYan0/Modpack_Translator"


def test_preserve_lists_cover_model_and_cache_essentials():
    # outputs/（含 translation_cache.json）與 .runtime 必須在保留清單，
    # 否則就地更新會清掉翻譯快取
    assert "outputs" in updater.PRESERVE_TOP_LEVEL
    assert ".runtime" in updater.PRESERVE_TOP_LEVEL
    assert ".venv" in updater.PRESERVE_TOP_LEVEL
    assert ".env" in updater.PRESERVE_FILES


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("v1.6.1", "1.6.0", True),
        ("1.7.0", "1.6.9", True),
        ("v1.10.0", "1.9.9", True),
        ("v1.6.0", "1.6.0", False),
        ("v1.5.9", "1.6.0", False),
        ("1.6.0.1", "1.6.0", True),
        ("v2.0", "1.99.99", True),
    ],
)
def test_is_newer_version(latest, current, expected):
    assert is_newer_version(latest, current) is expected


# ────────────────────────────────────────────── release asset 選擇


def _release(tag: str, assets: list[dict], **extra) -> dict:
    return {"tag_name": tag, "html_url": f"https://example.com/{tag}", "assets": assets, **extra}


def test_select_asset_prefers_versioned_zip_and_reads_sha256(monkeypatch):
    sha = "a" * 64
    monkeypatch.setattr(updater, "_download_text", lambda url: f"{sha}  Modpack_Translator-v1.7.0.zip")
    release = _release(
        "v1.7.0",
        [
            {"name": "other.zip", "browser_download_url": "u0", "size": 1},
            {"name": "Modpack_Translator-v1.7.0.zip", "browser_download_url": "u1", "size": 123},
            {"name": "Modpack_Translator-v1.7.0.zip.sha256", "browser_download_url": "u2", "size": 65},
        ],
    )
    asset, sha256 = updater._select_asset(release)
    assert asset["name"] == "Modpack_Translator-v1.7.0.zip"
    assert sha256 == sha


def test_select_asset_falls_back_to_digest_field():
    release = _release(
        "v1.7.0",
        [
            {
                "name": "Modpack_Translator-v1.7.0.zip",
                "browser_download_url": "u1",
                "size": 123,
                "digest": "sha256:" + "b" * 64,
            }
        ],
    )
    asset, sha256 = updater._select_asset(release)
    assert sha256 == "b" * 64


def test_select_asset_raises_when_no_zip():
    with pytest.raises(RuntimeError):
        updater._select_asset(_release("v1.7.0", [{"name": "readme.txt"}]))


def test_select_asset_prefers_exact_name_over_prefix_match():
    release = _release(
        "v1.7.0",
        [
            # 前綴相同的干擾資產排在前面，仍應選中精確檔名
            {"name": "Modpack_Translator-v1.7.0-cuda.zip", "browser_download_url": "u0", "size": 1},
            {"name": "Modpack_Translator-v1.7.0.zip", "browser_download_url": "u1", "size": 123},
        ],
    )
    asset, _ = updater._select_asset(release)
    assert asset["name"] == "Modpack_Translator-v1.7.0.zip"


# ────────────────────────────────────────────── check_for_update


def test_check_for_update_returns_info_for_newer_release(monkeypatch):
    release = _release(
        "v9.9.9",
        [{"name": "Modpack_Translator-v9.9.9.zip", "browser_download_url": "u", "size": 42}],
        body="notes",
    )
    monkeypatch.setattr(updater, "_request_json", lambda url, timeout=8.0: release)
    info = check_for_update("1.6.0")
    assert info is not None
    assert info.version == "9.9.9"
    assert info.asset_name == "Modpack_Translator-v9.9.9.zip"
    assert info.asset_size == 42


def test_check_for_update_none_when_up_to_date(monkeypatch):
    release = _release(
        "v1.6.0",
        [{"name": "Modpack_Translator-v1.6.0.zip", "browser_download_url": "u", "size": 42}],
    )
    monkeypatch.setattr(updater, "_request_json", lambda url, timeout=8.0: release)
    assert check_for_update("1.6.0") is None
    assert check_for_update("1.6.0", raise_errors=True) is None


def test_check_for_update_skips_prerelease(monkeypatch):
    release = _release(
        "v9.9.9",
        [{"name": "Modpack_Translator-v9.9.9.zip", "browser_download_url": "u", "size": 42}],
        prerelease=True,
    )
    monkeypatch.setattr(updater, "_request_json", lambda url, timeout=8.0: release)
    assert check_for_update("1.6.0") is None


def test_check_for_update_network_error_silent_vs_raise(monkeypatch):
    def _boom(url, timeout=8.0):
        raise URLError("offline")

    monkeypatch.setattr(updater, "_request_json", _boom)
    assert check_for_update("1.6.0") is None
    with pytest.raises(RuntimeError):
        check_for_update("1.6.0", raise_errors=True)


def test_check_for_update_missing_asset_silent_vs_raise(monkeypatch):
    release = _release("v9.9.9", [{"name": "readme.txt"}])
    monkeypatch.setattr(updater, "_request_json", lambda url, timeout=8.0: release)
    assert check_for_update("1.6.0") is None
    with pytest.raises(RuntimeError):
        check_for_update("1.6.0", raise_errors=True)


def test_check_for_update_rate_limit_message(monkeypatch):
    from urllib.error import HTTPError

    def _limited(url, timeout=8.0):
        raise HTTPError(url, 403, "rate limit exceeded", None, None)

    monkeypatch.setattr(updater, "_request_json", _limited)
    assert check_for_update("1.6.0") is None
    with pytest.raises(RuntimeError, match="流量限制"):
        check_for_update("1.6.0", raise_errors=True)


def test_check_for_update_non_json_response_silent_vs_raise(monkeypatch):
    def _garbage(url, timeout=8.0):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr(updater, "_request_json", _garbage)
    assert check_for_update("1.6.0") is None
    with pytest.raises(RuntimeError):
        check_for_update("1.6.0", raise_errors=True)


# ────────────────────────────────────────────── download_update


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)

    def read(self, n: int) -> bytes:
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _make_info(payload: bytes, sha256: str | None) -> UpdateInfo:
    return UpdateInfo(
        version="9.9.9",
        tag_name="v9.9.9",
        release_url="https://example.com",
        notes="",
        asset_name="Modpack_Translator-v9.9.9.zip",
        asset_url="https://example.com/dl.zip",
        asset_size=len(payload),
        sha256=sha256,
    )


@pytest.fixture
def dl_env(monkeypatch, tmp_path):
    update_dir = tmp_path / "updates"
    monkeypatch.setattr(updater, "UPDATE_DIR", update_dir)
    monkeypatch.setattr(updater, "UPDATE_STATE", tmp_path / "update_state.json")
    return update_dir


def test_download_update_writes_file_and_reports_progress(monkeypatch, dl_env):
    payload = b"x" * (2 * 1024 * 1024 + 512)  # 3 個 chunk
    monkeypatch.setattr(updater, "urlopen", lambda req, timeout=30: _FakeResponse(payload))
    info = _make_info(payload, hashlib.sha256(payload).hexdigest())

    seen: list[tuple[int, int]] = []

    def cb(done: int, total: int) -> bool:
        seen.append((done, total))
        return True

    dest = download_update(info, progress_cb=cb)
    assert dest.read_bytes() == payload
    assert seen[-1] == (len(payload), len(payload))
    assert len(seen) == 3
    state = json.loads((updater.UPDATE_STATE).read_text(encoding="utf-8"))
    assert state["version"] == "9.9.9"


def test_download_update_checksum_mismatch_removes_tmp(monkeypatch, dl_env):
    payload = b"hello world"
    monkeypatch.setattr(updater, "urlopen", lambda req, timeout=30: _FakeResponse(payload))
    info = _make_info(payload, "0" * 64)
    with pytest.raises(RuntimeError, match="checksum"):
        download_update(info)
    assert not any(dl_env.glob("*.part"))
    assert not (dl_env / info.asset_name).exists()


def test_download_update_cancel_raises_and_cleans_up(monkeypatch, dl_env):
    payload = b"x" * (3 * 1024 * 1024)
    monkeypatch.setattr(updater, "urlopen", lambda req, timeout=30: _FakeResponse(payload))
    info = _make_info(payload, hashlib.sha256(payload).hexdigest())
    with pytest.raises(DownloadCancelled):
        download_update(info, progress_cb=lambda done, total: False)
    assert not any(dl_env.glob("*.part"))
    assert not (dl_env / info.asset_name).exists()


def test_download_update_size_mismatch_raises(monkeypatch, dl_env):
    payload = b"short"
    monkeypatch.setattr(updater, "urlopen", lambda req, timeout=30: _FakeResponse(payload))
    # 宣稱 100 bytes 但實際只有 5 bytes；無 sha256 時大小檢查是唯一防線
    info = UpdateInfo(
        version="9.9.9",
        tag_name="v9.9.9",
        release_url="https://example.com",
        notes="",
        asset_name="Modpack_Translator-v9.9.9.zip",
        asset_url="https://example.com/dl.zip",
        asset_size=100,
        sha256=None,
    )
    with pytest.raises(RuntimeError, match="大小不符"):
        download_update(info)
    assert not any(dl_env.glob("*.part"))
    assert not (dl_env / info.asset_name).exists()


# ────────────────────────────────────────────── 就地覆蓋 / 保留 / 備份


def test_copy_tree_contents_preserves_outputs(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    (src / "src").mkdir(parents=True)
    (src / "src" / "new.py").write_text("new", encoding="utf-8")
    (src / "main.py").write_text("new main", encoding="utf-8")
    # 更新包裡即使意外帶了 outputs / .env，也不能覆蓋使用者資料
    (src / "outputs").mkdir()
    (src / "outputs" / "translation_cache.json").write_text("{}", encoding="utf-8")
    (src / ".env").write_text("SHOULD_NOT_COPY", encoding="utf-8")

    (dest / "outputs").mkdir(parents=True)
    (dest / "outputs" / "translation_cache.json").write_text('{"cached": true}', encoding="utf-8")
    (dest / "src").mkdir()
    (dest / "src" / "old.py").write_text("old", encoding="utf-8")
    (dest / "main.py").write_text("old main", encoding="utf-8")
    (dest / ".env").write_text("MY_SECRET", encoding="utf-8")

    updater._copy_tree_contents(src, dest)

    assert (dest / "outputs" / "translation_cache.json").read_text(encoding="utf-8") == '{"cached": true}'
    assert (dest / ".env").read_text(encoding="utf-8") == "MY_SECRET"
    assert (dest / "main.py").read_text(encoding="utf-8") == "new main"
    assert (dest / "src" / "new.py").exists()
    assert not (dest / "src" / "old.py").exists()  # 舊目錄整個換新


def test_copy_tree_contents_preserves_user_configs(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    (src / "configs" / "languages").mkdir(parents=True)
    (src / "configs" / "model.yaml").write_text("model: {new_default: 1}", encoding="utf-8")
    (src / "configs" / "paths.yaml").write_text("paths: {new: 1}", encoding="utf-8")
    (src / "configs" / "languages" / "zh_tw.yaml").write_text("prompt: v2", encoding="utf-8")

    (dest / "configs" / "languages").mkdir(parents=True)
    (dest / "configs" / "model.yaml").write_text(
        'model: {base_gguf_path: "D:/models/my5gb.gguf"}', encoding="utf-8"
    )
    (dest / "configs" / "paths.yaml").write_text("paths: {custom: 1}", encoding="utf-8")
    (dest / "configs" / "languages" / "zh_tw.yaml").write_text("prompt: v1", encoding="utf-8")

    updater._copy_tree_contents(src, dest)

    # 使用者的機器特定設定保留（否則 base_gguf_path 被重置 → 5GB 模型重新下載）
    assert "my5gb.gguf" in (dest / "configs" / "model.yaml").read_text(encoding="utf-8")
    assert "custom" in (dest / "configs" / "paths.yaml").read_text(encoding="utf-8")
    # 屬於程式內容的語言設定則跟著新版走
    assert (dest / "configs" / "languages" / "zh_tw.yaml").read_text(encoding="utf-8") == "prompt: v2"


def test_copy_tree_contents_installs_configs_fresh_when_absent(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    (src / "configs").mkdir(parents=True)
    (src / "configs" / "model.yaml").write_text("model: {}", encoding="utf-8")
    dest.mkdir()

    updater._copy_tree_contents(src, dest)
    assert (dest / "configs" / "model.yaml").read_text(encoding="utf-8") == "model: {}"


def test_copy_tree_contents_merges_adapter_dir(tmp_path):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    (src / "adapter").mkdir(parents=True)
    (src / "adapter" / "shipped_lora.gguf").write_text("v2", encoding="utf-8")
    (dest / "adapter").mkdir(parents=True)
    (dest / "adapter" / "shipped_lora.gguf").write_text("v1", encoding="utf-8")
    (dest / "adapter" / "my_custom_lora.gguf").write_text("custom", encoding="utf-8")

    updater._copy_tree_contents(src, dest)

    # 內建 LoRA 更新、使用者自訂 LoRA 保留
    assert (dest / "adapter" / "shipped_lora.gguf").read_text(encoding="utf-8") == "v2"
    assert (dest / "adapter" / "my_custom_lora.gguf").read_text(encoding="utf-8") == "custom"


def test_prune_backups_keeps_latest(monkeypatch, tmp_path):
    backup_dir = tmp_path / "update_backup"
    monkeypatch.setattr(updater, "BACKUP_DIR", backup_dir)
    names = ["20260101-000000", "20260201-000000", "20260301-000000", "20260401-000000"]
    for name in names:
        (backup_dir / name).mkdir(parents=True)
    updater._prune_backups(keep=2)
    remaining = sorted(p.name for p in backup_dir.iterdir())
    assert remaining == names[-2:]


def test_prune_backups_missing_dir_is_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "BACKUP_DIR", tmp_path / "nope")
    updater._prune_backups(keep=2)  # 不應拋錯


def test_apply_update_replaces_code_and_preserves_user_data(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("old main", encoding="utf-8")
    (project / "outputs").mkdir()
    (project / "outputs" / "translation_cache.json").write_text('{"cached": true}', encoding="utf-8")
    (project / ".venv").mkdir()
    (project / ".venv" / "marker").write_text("venv", encoding="utf-8")

    runtime = project / ".runtime"
    monkeypatch.setattr(updater, "PROJECT_ROOT", project)
    monkeypatch.setattr(updater, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(updater, "UPDATE_DIR", runtime / "updates")
    monkeypatch.setattr(updater, "UPDATE_STATE", runtime / "update_state.json")
    monkeypatch.setattr(updater, "BACKUP_DIR", runtime / "update_backup")

    zip_path = tmp_path / "Modpack_Translator-v9.9.9.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Modpack_Translator-v9.9.9/main.py", "new main")
        zf.writestr("Modpack_Translator-v9.9.9/configs/model.yaml", "model: {}")

    updater.apply_update(zip_path, restart=False, refresh_environment=False)

    assert (project / "main.py").read_text(encoding="utf-8") == "new main"
    assert (project / "configs" / "model.yaml").exists()
    # 使用者資料完好
    assert (project / "outputs" / "translation_cache.json").read_text(encoding="utf-8") == '{"cached": true}'
    assert (project / ".venv" / "marker").exists()
    # 舊版程式碼有備份
    backups = list((runtime / "update_backup").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "main.py").read_text(encoding="utf-8") == "old main"


def test_apply_update_backs_up_only_touched_items(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("old main", encoding="utf-8")
    # 使用者放在根目錄、更新不會動到的大檔：不應被備份
    (project / "my_local_model.gguf").write_text("5GB pretend", encoding="utf-8")

    runtime = project / ".runtime"
    monkeypatch.setattr(updater, "PROJECT_ROOT", project)
    monkeypatch.setattr(updater, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(updater, "UPDATE_DIR", runtime / "updates")
    monkeypatch.setattr(updater, "UPDATE_STATE", runtime / "update_state.json")
    monkeypatch.setattr(updater, "BACKUP_DIR", runtime / "update_backup")

    zip_path = tmp_path / "u.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Modpack_Translator-v9.9.9/main.py", "new main")

    updater.apply_update(zip_path, restart=False, refresh_environment=False)

    backups = list((runtime / "update_backup").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "main.py").exists()
    assert not (backups[0] / "my_local_model.gguf").exists()
    # 更新也沒動到這個檔案
    assert (project / "my_local_model.gguf").read_text(encoding="utf-8") == "5GB pretend"


def test_apply_update_restores_backup_on_copy_failure(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("old main", encoding="utf-8")

    runtime = project / ".runtime"
    monkeypatch.setattr(updater, "PROJECT_ROOT", project)
    monkeypatch.setattr(updater, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(updater, "UPDATE_DIR", runtime / "updates")
    monkeypatch.setattr(updater, "UPDATE_STATE", runtime / "update_state.json")
    monkeypatch.setattr(updater, "BACKUP_DIR", runtime / "update_backup")

    zip_path = tmp_path / "u.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Modpack_Translator-v9.9.9/main.py", "new main")

    real_copy = updater._copy_tree_contents
    calls = {"n": 0}

    def flaky_copy(src_root, dest_root, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:  # 第一次（套用新版）失敗，第二次（還原）成功
            (dest_root / "main.py").write_text("HALF WRITTEN", encoding="utf-8")
            raise OSError("disk error")
        return real_copy(src_root, dest_root, **kwargs)

    monkeypatch.setattr(updater, "_copy_tree_contents", flaky_copy)

    with pytest.raises(OSError):
        updater.apply_update(zip_path, restart=False, refresh_environment=False)

    # 半新半舊的檔案被備份還原
    assert (project / "main.py").read_text(encoding="utf-8") == "old main"
    log_text = (runtime / "updater.log").read_text(encoding="utf-8")
    assert "copy failed" in log_text
    assert "restore complete" in log_text


def test_apply_update_rollback_restores_user_configs(monkeypatch, tmp_path):
    """複製 configs 中途失敗：還原不得把半安裝的新版設定蓋回使用者設定。"""
    project = tmp_path / "project"
    (project / "configs").mkdir(parents=True)
    (project / "configs" / "model.yaml").write_text("user version", encoding="utf-8")
    (project / "main.py").write_text("old main", encoding="utf-8")

    runtime = project / ".runtime"
    monkeypatch.setattr(updater, "PROJECT_ROOT", project)
    monkeypatch.setattr(updater, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(updater, "UPDATE_DIR", runtime / "updates")
    monkeypatch.setattr(updater, "UPDATE_STATE", runtime / "update_state.json")
    monkeypatch.setattr(updater, "BACKUP_DIR", runtime / "update_backup")

    zip_path = tmp_path / "u.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Modpack_Translator-v9.9.9/configs/model.yaml", "new default")
        zf.writestr("Modpack_Translator-v9.9.9/main.py", "new main")

    real_copytree = updater.shutil.copytree

    def flaky_copytree(src, dst, **kwargs):
        result = real_copytree(src, dst, **kwargs)
        # 只在「套用新版」複製 configs 完成後失敗（此時 stash 尚未還原，
        # dest 的 model.yaml 是新版預設值）；備份與還原階段不受影響
        if Path(dst).name == "configs" and "apply" in Path(src).parts:
            raise OSError("disk error")
        return result

    monkeypatch.setattr(updater.shutil, "copytree", flaky_copytree)

    with pytest.raises(OSError):
        updater.apply_update(zip_path, restart=False, refresh_environment=False)

    assert (project / "configs" / "model.yaml").read_text(encoding="utf-8") == "user version"
    log_text = (runtime / "updater.log").read_text(encoding="utf-8")
    assert "restore complete" in log_text


def test_finalize_ps1_has_bom(monkeypatch, tmp_path):
    """Windows PowerShell 5.1 讀無 BOM 檔用 ANSI 代碼頁；
    中文安裝路徑會被誤解碼，finalize 腳本必須帶 UTF-8 BOM。"""
    if updater.os.name != "nt":
        pytest.skip("Windows-only finalize script")
    monkeypatch.setattr(updater, "PROJECT_ROOT", tmp_path / "測試中文路徑")
    monkeypatch.setattr(updater, "FINALIZE_SCRIPT", tmp_path / "finalize_update")
    script = updater._write_finalize_script(restart=True)
    raw = script.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert "$true" in text
    assert "setup exit code" in text


def test_pid_helpers(monkeypatch):
    import os as _os

    assert updater._pid_alive(_os.getpid()) is True
    # 幾乎不可能存在的 PID；_wait_for_pid_exit 應立即返回
    dead_pid = 0x7FFFFFF
    start = updater.time.time()
    updater._wait_for_pid_exit(dead_pid, timeout=5.0)
    assert updater.time.time() - start < 2.0


# ────────────────────────────────────────────── 並行更新防護 / finalize 互斥


@pytest.fixture
def pid_env(monkeypatch, tmp_path):
    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    pid_file = runtime / "finalize_update.pid"
    monkeypatch.setattr(updater, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(updater, "FINALIZE_PID_FILE", pid_file)
    return pid_file


def test_finalize_in_progress_false_without_pid_file(pid_env):
    assert updater.finalize_in_progress() is False


def test_finalize_in_progress_true_for_live_pid(pid_env):
    import os as _os

    pid_env.write_text(str(_os.getpid()), encoding="utf-8")
    assert updater.finalize_in_progress() is True


def test_finalize_in_progress_grace_period_for_fresh_dead_pid(pid_env):
    # 交接空窗：apply 程序寫入自身 PID 後結束，finalize 腳本還沒接手改寫。
    # 檔案夠新時就算 PID 已死也要視為進行中，否則第二個更新會趁隙動 .venv
    pid_env.write_text(str(0x7FFFFFF), encoding="utf-8")
    assert updater.finalize_in_progress() is True


def test_finalize_in_progress_stale_dead_pid_cleaned(pid_env):
    import os as _os

    pid_env.write_text(str(0x7FFFFFF), encoding="utf-8")
    old = updater.time.time() - 3600
    _os.utime(pid_env, (old, old))
    assert updater.finalize_in_progress() is False
    # 過期殘留要清掉，否則之後永遠擋住更新
    assert not pid_env.exists()


def test_wait_for_finalize_times_out(monkeypatch, pid_env):
    monkeypatch.setattr(updater, "finalize_in_progress", lambda: True)
    with pytest.raises(RuntimeError):
        updater._wait_for_finalize(timeout=0.2, poll=0.05)


def test_wait_for_finalize_returns_when_clear(monkeypatch, pid_env):
    monkeypatch.setattr(updater, "finalize_in_progress", lambda: False)
    updater._wait_for_finalize(timeout=0.2, poll=0.05)  # 不應拋錯


def _guard_project(monkeypatch, tmp_path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("old main", encoding="utf-8")
    runtime = project / ".runtime"
    monkeypatch.setattr(updater, "PROJECT_ROOT", project)
    monkeypatch.setattr(updater, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(updater, "UPDATE_DIR", runtime / "updates")
    monkeypatch.setattr(updater, "UPDATE_STATE", runtime / "update_state.json")
    monkeypatch.setattr(updater, "BACKUP_DIR", runtime / "update_backup")
    monkeypatch.setattr(updater, "FINALIZE_PID_FILE", runtime / "finalize_update.pid")
    return project


def _guard_zip(tmp_path) -> Path:
    zip_path = tmp_path / "u.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Modpack_Translator-v9.9.9/main.py", "new main")
    return zip_path


def test_apply_update_aborts_when_gui_never_exits(monkeypatch, tmp_path):
    """GUI 超時仍未退出就動檔案，會把 .venv 從活著的程序腳下刪成半殘。"""
    project = _guard_project(monkeypatch, tmp_path)
    zip_path = _guard_zip(tmp_path)
    monkeypatch.setattr(updater, "_wait_for_pid_exit", lambda pid, timeout=60.0: None)
    monkeypatch.setattr(updater, "_pid_alive", lambda pid: True)

    with pytest.raises(RuntimeError, match="仍在執行"):
        updater.apply_update(
            zip_path, restart=False, refresh_environment=False, wait_pid=12345
        )
    assert (project / "main.py").read_text(encoding="utf-8") == "old main"


def test_apply_update_waits_for_other_finalize_before_touching_files(monkeypatch, tmp_path):
    """使用者重開舊程式再點一次更新：第二個 apply 必須等第一個 finalize 結束。"""
    project = _guard_project(monkeypatch, tmp_path)
    zip_path = _guard_zip(tmp_path)

    def _always_busy(*args, **kwargs):
        raise RuntimeError("前一次更新的環境安裝仍在進行")

    monkeypatch.setattr(updater, "_wait_for_finalize", _always_busy)

    with pytest.raises(RuntimeError, match="前一次"):
        updater.apply_update(zip_path, restart=False, refresh_environment=False)
    assert (project / "main.py").read_text(encoding="utf-8") == "old main"


def test_launch_finalize_writes_pid_placeholder(monkeypatch, tmp_path):
    import os as _os

    runtime = tmp_path / ".runtime"
    pid_file = runtime / "finalize_update.pid"
    monkeypatch.setattr(updater, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(updater, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(updater, "FINALIZE_PID_FILE", pid_file)
    monkeypatch.setattr(updater, "FINALIZE_SCRIPT", runtime / "finalize_update")
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

    updater._launch_finalize_script(restart=False)
    assert pid_file.read_text(encoding="utf-8").strip() == str(_os.getpid())


def test_finalize_ps1_safe_venv_removal_and_pid_lifecycle(monkeypatch, tmp_path):
    if updater.os.name != "nt":
        pytest.skip("Windows-only finalize script")
    monkeypatch.setattr(updater, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(updater, "FINALIZE_SCRIPT", tmp_path / "finalize_update")
    script = updater._write_finalize_script(restart=True)
    text = script.read_bytes().decode("utf-8-sig")
    # 腳本必須接手 PID 檔並在結束時清掉自己的（且只清自己的）
    assert "finalize_update.pid" in text
    # 直接遞迴刪除 .venv 遇檔案鎖會半刪（pyvenv.cfg 沒了、python.exe 還在），
    # 之後 uv sync 永遠死在 "No pyvenv.cfg file"；必須整個改名成功才刪，
    # 改不動就保留完整 venv 交給 uv sync 就地調和
    assert "Rename-Item" in text
    assert ".venv.delete-" in text


def test_finalize_sh_safe_venv_removal_and_pid_lifecycle(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.os, "name", "posix")
    monkeypatch.setattr(updater, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(updater, "FINALIZE_SCRIPT", tmp_path / "finalize_update")
    script = updater._write_finalize_script(restart=True)
    text = script.read_text(encoding="utf-8")
    assert "finalize_update.pid" in text
    assert ".venv.delete-" in text


def test_apply_update_rejects_unsafe_zip(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime = project / ".runtime"
    monkeypatch.setattr(updater, "PROJECT_ROOT", project)
    monkeypatch.setattr(updater, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(updater, "UPDATE_DIR", runtime / "updates")
    monkeypatch.setattr(updater, "UPDATE_STATE", runtime / "update_state.json")
    monkeypatch.setattr(updater, "BACKUP_DIR", runtime / "update_backup")

    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../evil.py", "boom")

    with pytest.raises(RuntimeError, match="Unsafe path"):
        updater.apply_update(zip_path, restart=False, refresh_environment=False)
