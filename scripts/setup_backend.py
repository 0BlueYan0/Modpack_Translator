#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
BACKEND_JSON = RUNTIME_DIR / "backend.json"

LLAMA_CPP_PYTHON_VERSION = "0.3.23"
LLAMA_CPP_SERVER_DEPS = [
    "fastapi>=0.100.0",
    "uvicorn>=0.22.0",
    "pydantic-settings>=2.0.1",
    "sse-starlette>=1.6.1",
    "starlette-context>=0.3.6,<0.4",
    "PyYAML>=5.1",
]
CUDA_WIN_WHEEL = (
    "https://github.com/abetlen/llama-cpp-python/releases/download/"
    "v0.3.23-cu124/llama_cpp_python-0.3.23-py3-none-win_amd64.whl"
)
CUDA_LINUX_WHEEL = (
    "https://github.com/abetlen/llama-cpp-python/releases/download/"
    "v0.3.23-cu124/llama_cpp_python-0.3.23-py3-none-linux_x86_64.whl"
)
CPU_WHEEL_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/cpu"

AMD_WIN_ZIP = (
    "https://repo.radeon.com/rocm/llama.cpp/windows/rocm-rel-7.2.1/"
    "llama-b8407-windows-rocm-7.2.1-gfx110X-gfx115X-gfx120X-x64.zip"
)
AMD_LINUX_ZIP = (
    "https://repo.radeon.com/rocm/llama.cpp/linux/rocm-rel-7.2.1/"
    "llama-b8407-ubuntu-24.04-rocm-7.2.1-gfx110X-gfx115X-gfx120X-x64.zip"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化本機 llama.cpp 模型後端。")
    parser.add_argument(
        "--backend",
        choices=("auto", "cuda", "amd", "cpu"),
        default="auto",
        help="要安裝的後端。預設為自動偵測。",
    )
    parser.add_argument(
        "--skip-model-download",
        action="store_true",
        help="初始化時不要下載基礎 GGUF 模型。",
    )
    return parser.parse_args()


def run(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd))
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def run_allow_fail(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd))
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=False)


def run_capture(cmd: list[str]) -> str:
    print("+ " + " ".join(cmd))
    completed = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    if completed.stderr:
        print(completed.stderr, end="")
    return completed.stdout.strip()


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def command_succeeds(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def command_output(cmd: list[str]) -> str:
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        return completed.stdout
    except OSError:
        return ""


def load_model_config() -> dict:
    text = (PROJECT_ROOT / "configs" / "model.yaml").read_text(encoding="utf-8")
    in_model = False
    config: dict[str, object] = {}
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not raw_line.startswith(" ") and line.rstrip(":") == "model":
            in_model = True
            continue
        if not in_model:
            continue
        if raw_line and not raw_line.startswith(" "):
            break
        if ":" not in line:
            continue
        key, value = line.strip().split(":", 1)
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            config[key] = value[1:-1]
        elif value.lower() in ("true", "false"):
            config[key] = value.lower() == "true"
        else:
            try:
                config[key] = int(value)
            except ValueError:
                try:
                    config[key] = float(value)
                except ValueError:
                    config[key] = value
    return config


def resolve_project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def detect_nvidia() -> bool:
    return command_exists("nvidia-smi") and command_succeeds(["nvidia-smi"])


def detect_amd() -> bool:
    system = platform.system().lower()
    if system == "windows":
        output = command_output(["wmic", "path", "win32_VideoController", "get", "name"])
    elif system == "linux":
        output = command_output(["lspci"])
        if not output:
            output = command_output(["rocminfo"])
    else:
        return False
    output = output.lower()
    return any(token in output for token in ("amd", "radeon", "advanced micro devices"))


def select_backend(requested: str) -> str:
    if requested != "auto":
        return requested
    if detect_nvidia():
        return "cuda"
    if detect_amd():
        return "amd"
    return "cpu"


def install_cuda_backend() -> None:
    system = platform.system().lower()
    if system == "windows":
        wheel = CUDA_WIN_WHEEL
    elif system == "linux":
        wheel = CUDA_LINUX_WHEEL
    else:
        print("CUDA wheel 目前只設定 Windows 和 Linux，改用 CPU 後端。")
        install_cpu_backend()
        return
    run_allow_fail(["uv", "pip", "uninstall", "llama-cpp-python"])
    run(["uv", "pip", "install", *LLAMA_CPP_SERVER_DEPS])
    run(["uv", "pip", "install", f"llama-cpp-python @ {wheel}"])


def install_cpu_backend() -> None:
    run_allow_fail(["uv", "pip", "uninstall", "llama-cpp-python"])
    run(["uv", "pip", "install", *LLAMA_CPP_SERVER_DEPS])
    run(
        [
            "uv",
            "pip",
            "install",
            f"llama-cpp-python[server]=={LLAMA_CPP_PYTHON_VERSION}",
            "--extra-index-url",
            CPU_WHEEL_INDEX,
        ]
    )


def download(url: str, dest: Path) -> None:
    if dest.exists():
        print(f"使用已下載的快取檔案：{dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"正在下載：{url}")
    urllib.request.urlretrieve(url, dest)


def find_llama_server(root: Path) -> Path:
    exe_name = "llama-server.exe" if platform.system().lower() == "windows" else "llama-server"
    matches = sorted(root.rglob(exe_name))
    if not matches:
        raise FileNotFoundError(f"在 {root} 找不到 {exe_name}")
    server = matches[0]
    if platform.system().lower() != "windows":
        server.chmod(server.stat().st_mode | 0o111)
    return server


def install_amd_backend() -> Path:
    system = platform.system().lower()
    if system == "windows":
        url = AMD_WIN_ZIP
    elif system == "linux":
        url = AMD_LINUX_ZIP
    else:
        raise RuntimeError("AMD 預編譯 llama.cpp binary 目前只支援 Windows/Linux。")

    archive = RUNTIME_DIR / "downloads" / Path(urlparse(url).path).name
    extract_dir = RUNTIME_DIR / "llama_cpp_amd"
    download(url, archive)

    if not extract_dir.exists() or not list(extract_dir.rglob("llama-server*")):
        print(f"正在解壓縮：{archive}")
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extract_dir)
    return find_llama_server(extract_dir)


def resolve_base_model(cfg: dict, skip_download: bool) -> Path:
    if cfg.get("base_gguf_path"):
        path = resolve_project_path(cfg["base_gguf_path"])
        if not path.exists():
            raise FileNotFoundError(f"找不到 base_gguf_path：{path}")
        return path
    if skip_download:
        return Path(cfg["base_hf_filename"])
    print(
        f"正在從 {cfg['base_hf_repo']} 下載基礎模型 {cfg['base_hf_filename']} "
        "（檔案較大，首次執行才需要）..."
    )
    code = (
        "from huggingface_hub import hf_hub_download; "
        f"print(hf_hub_download(repo_id={cfg['base_hf_repo']!r}, "
        f"filename={cfg['base_hf_filename']!r}))"
    )
    try:
        return Path(run_capture([sys.executable, "-c", code]).splitlines()[-1])
    except subprocess.CalledProcessError:
        print("偵測到 HuggingFace/PyYAML 匯入失敗，正在修復套件...")
        run(["uv", "pip", "install", "--force-reinstall", "pyyaml>=6.0", "huggingface-hub>=0.23.0"])
        return Path(run_capture([sys.executable, "-c", code]).splitlines()[-1])


def resolve_lora(cfg: dict) -> Path:
    path = resolve_project_path(cfg["lora_gguf_path"])
    if not path.exists():
        raise FileNotFoundError(f"找不到 LoRA 適配器：{path}")
    return path


def server_host_port(server_url: str) -> tuple[str, int]:
    parsed = urlparse(server_url)
    return parsed.hostname or "127.0.0.1", parsed.port or 8080


def ensure_server_port_free(cfg: dict) -> None:
    host, port = server_host_port(cfg["server_url"])
    try:
        with socket.create_connection((host, port), timeout=1):
            raise RuntimeError(
                f"{host}:{port} 已經有程式在使用。請先關閉翻譯器或殘留的 llama-server，"
                "再重新執行初始化腳本。"
            )
    except OSError:
        return


def python_server_command(cfg: dict, base_model: Path, lora: Path, gpu_layers: int) -> list[str]:
    host, port = server_host_port(cfg["server_url"])
    return [
        sys.executable,
        "-m",
        "llama_cpp.server",
        "--model",
        str(base_model),
        "--lora_path",
        str(lora),
        "--n_gpu_layers",
        str(gpu_layers),
        "--n_ctx",
        str(cfg["n_ctx"]),
        "--host",
        host,
        "--port",
        str(port),
    ]


def binary_server_command(server: Path, cfg: dict, base_model: Path, lora: Path) -> list[str]:
    host, port = server_host_port(cfg["server_url"])
    gpu_layers = "99" if int(cfg["n_gpu_layers"]) < 0 else str(cfg["n_gpu_layers"])
    command = [
        str(server),
        "-m",
        str(base_model),
        "-c",
        str(cfg["n_ctx"]),
        "-ngl",
        gpu_layers,
        "-fa",
        "on",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if float(cfg["lora_scale"]) == 1.0:
        command.extend(["--lora", str(lora)])
    else:
        command.extend(["--lora-scaled", str(lora), str(cfg["lora_scale"])])
    return command


def write_backend_config(backend: str, command: list[str], cfg: dict, base_model: Path) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "backend": backend,
        "server_url": cfg["server_url"],
        "server_api_key": cfg["server_api_key"],
        "server_model": Path(base_model).name,
        "server_command": command,
        "created_at": int(time.time()),
    }
    BACKEND_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"已寫入後端設定：{BACKEND_JSON}")


def main() -> None:
    args = parse_args()
    cfg = load_model_config()
    ensure_server_port_free(cfg)
    backend = select_backend(args.backend)
    print(f"選擇的後端：{backend}")

    base_model = resolve_base_model(cfg, args.skip_model_download)
    lora = resolve_lora(cfg)

    if backend == "cuda":
        install_cuda_backend()
        command = python_server_command(cfg, base_model, lora, int(cfg["n_gpu_layers"]))
    elif backend == "amd":
        server = install_amd_backend()
        command = binary_server_command(server, cfg, base_model, lora)
    else:
        install_cpu_backend()
        command = python_server_command(cfg, base_model, lora, 0)

    write_backend_config(backend, command, cfg, base_model)
    print("後端初始化完成。正常啟動程式即可，程式會自動啟動本機模型服務。")


if __name__ == "__main__":
    main()
