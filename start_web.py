"""
Start the local oversell web console.

Default mode starts both:
  - FastAPI backend on http://127.0.0.1:8000
  - Vite frontend on http://127.0.0.1:5173

If web/dist exists, use --prod to serve the built frontend from FastAPI only.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import webbrowser
import os
from urllib.error import URLError
from urllib.request import urlopen
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
WEB_DIST = WEB_DIR / "dist"
LOG_DIR = ROOT / "logs"
NODEJS_DIR = Path(r"C:\Program Files\nodejs")


def _runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    if NODEJS_DIR.exists():
        env["PATH"] = f"{NODEJS_DIR};{env.get('PATH', '')}"
    return env


def _require_command(name: str) -> str:
    if name == "npm":
        npm_cmd = NODEJS_DIR / "npm.cmd"
        if npm_cmd.exists():
            return str(npm_cmd)
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"找不到命令: {name}")
    return path


def _start(cmd: list[str], cwd: Path, log_name: str) -> subprocess.Popen:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(LOG_DIR / log_name, "a", encoding="utf-8")
    log_file.write(f"\n\n===== start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    log_file.flush()
    return subprocess.Popen(cmd, cwd=str(cwd), env=_runtime_env(), stdout=log_file, stderr=log_file)


def _http_json(url: str, timeout: float = 1.0) -> dict | None:
    try:
        with urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def _http_ok(url: str, timeout: float = 1.0) -> bool:
    try:
        with urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 400
    except (URLError, OSError):
        return False


def _compatible_backend(url: str) -> bool:
    payload = _http_json(f"{url}/api/health")
    if not payload:
        return False
    if payload.get("app") != "oversell":
        raise RuntimeError(f"{url} 已被其他服务占用，请先关闭占用 8000 端口的程序。")
    if payload.get("version") != "0.2.0":
        raise RuntimeError(f"{url} 上运行的是旧版后端，请先关闭旧的控制台窗口后重新启动。")
    return True


def _tail_log(path: Path, lines: int = 40) -> str:
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])


def _install_frontend_deps_if_needed() -> None:
    if (WEB_DIR / "node_modules").exists():
        return
    npm = _require_command("npm")
    print("[INFO] web/node_modules 不存在，开始执行 npm install ...")
    subprocess.run([npm, "install"], cwd=str(WEB_DIR), check=True, env=_runtime_env())


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 oversell Vue + FastAPI 控制台")
    parser.add_argument("--prod", action="store_true", help="只启动 FastAPI，并托管 web/dist")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--skip-npm-install", action="store_true", help="跳过 npm install 检查")
    args = parser.parse_args()

    python = sys.executable
    backend_url = "http://127.0.0.1:8000"
    frontend_url = "http://127.0.0.1:5173"
    backend_proc: subprocess.Popen | None = None
    frontend_proc: subprocess.Popen | None = None

    if args.prod:
        if not WEB_DIST.exists():
            raise RuntimeError("web/dist 不存在，请先运行：cd web && npm run build")
        if _compatible_backend(backend_url):
            print(f"[INFO] 检测到已有后端服务，直接复用：{backend_url}")
        else:
            backend_proc = _start(
                [python, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", "8000"],
                ROOT,
                "backend.log",
            )
            time.sleep(2)
            if backend_proc.poll() is not None:
                tail = _tail_log(LOG_DIR / "backend.log")
                raise RuntimeError(f"后端启动失败，详见 logs/backend.log\n{tail}")
        url = backend_url
        print(f"[INFO] 控制台已启动：{url}")
        if not args.no_open:
            webbrowser.open(url)
        if backend_proc:
            backend_proc.wait()
        else:
            print("[INFO] 当前复用已有后端服务，本启动器不会关闭它。")
        return

    if not args.skip_npm_install:
        _install_frontend_deps_if_needed()

    npm = _require_command("npm")
    if _compatible_backend(backend_url):
        print(f"[INFO] 检测到已有后端服务，直接复用：{backend_url}")
    else:
        backend_proc = _start(
            [python, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", "8000"],
            ROOT,
            "backend.log",
        )

    if _http_ok(frontend_url):
        print(f"[INFO] 检测到已有前端服务，直接复用：{frontend_url}")
    else:
        frontend_proc = _start([npm, "run", "dev"], WEB_DIR, "frontend.log")

    time.sleep(2)
    if backend_proc and backend_proc.poll() is not None:
        tail = _tail_log(LOG_DIR / "backend.log")
        raise RuntimeError(f"后端启动失败，详见 logs/backend.log\n{tail}")
    if frontend_proc and frontend_proc.poll() is not None:
        tail = _tail_log(LOG_DIR / "frontend.log")
        raise RuntimeError(f"前端启动失败，详见 logs/frontend.log\n{tail}")

    url = frontend_url
    print(f"[INFO] 后端：{backend_url}")
    print(f"[INFO] 前端：{url}")
    print("[INFO] 日志：logs/backend.log, logs/frontend.log")
    if not args.no_open:
        webbrowser.open(url)

    try:
        while True:
            if backend_proc and backend_proc.poll() is not None:
                tail = _tail_log(LOG_DIR / "backend.log")
                raise RuntimeError(f"后端进程已退出，详见 logs/backend.log\n{tail}")
            if frontend_proc and frontend_proc.poll() is not None:
                tail = _tail_log(LOG_DIR / "frontend.log")
                raise RuntimeError(f"前端进程已退出，详见 logs/frontend.log\n{tail}")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] 正在停止控制台...")
    finally:
        for proc in (frontend_proc, backend_proc):
            if proc and proc.poll() is None:
                proc.terminate()


if __name__ == "__main__":
    main()
