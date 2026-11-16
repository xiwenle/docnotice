"""Process lifecycle for the real 8000 instance, sandbox uvicorn, and host probes."""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import MatrixConfig
from .io_utils import read_json, utc_now, write_json_atomic

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

# Local test hosts must bypass any system proxy; otherwise *.localhost probes can hang.
NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _powershell(script: str, *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    if not IS_WINDOWS:
        raise RuntimeError("PowerShell process discovery is only implemented for Windows")
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def port_info(port: int) -> dict[str, Any] | None:
    if not port_in_use(port):
        return None
    script = (
        f"$c = Get-NetTCPConnection -State Listen -LocalPort {int(port)} -ErrorAction SilentlyContinue "
        "| Select-Object -First 1; "
        "if (-not $c) { exit 0 }; "
        "$p = Get-CimInstance Win32_Process -Filter (\"ProcessId = \" + $c.OwningProcess) "
        "-ErrorAction SilentlyContinue; "
        "if (-not $p) { exit 0 }; "
        "@{pid=$p.ProcessId; name=$p.Name; executable_path=$p.ExecutablePath; "
        "command_line=$p.CommandLine; creation_date=$p.CreationDate; local_address=$c.LocalAddress; "
        "local_port=$c.LocalPort} | ConvertTo-Json -Compress"
    )
    result = _powershell(script)
    if result.returncode != 0:
        raise RuntimeError(f"cannot inspect port {port}: {result.stdout[:500]}")
    text = (result.stdout or "").strip()
    if not text:
        return None
    try:
        info = json.loads(text)
    except json.JSONDecodeError:
        return None
    command_line = str(info.get("command_line") or "")
    # Do not persist secrets that may have been embedded in a launch command.
    for marker in ("SECRET_KEY", "PASSWORD", "TOKEN", "COOKIE"):
        if marker in command_line.upper():
            info["command_line"] = "[REDACTED: contains sensitive marker]"
            break
    info["pid"] = int(info.get("pid") or 0)
    return info


def process_info(pid: int) -> dict[str, Any] | None:
    script = (
        f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\" -ErrorAction SilentlyContinue; "
        "if (-not $p) { exit 0 }; "
        "@{pid=$p.ProcessId; name=$p.Name; executable_path=$p.ExecutablePath; "
        "command_line=$p.CommandLine} | ConvertTo-Json -Compress"
    )
    result = _powershell(script)
    text = (result.stdout or "").strip()
    if not text:
        return None
    try:
        info = json.loads(text)
    except json.JSONDecodeError:
        return None
    command_line = str(info.get("command_line") or "")
    for marker in ("SECRET_KEY", "PASSWORD", "TOKEN", "COOKIE"):
        if marker in command_line.upper():
            info["command_line"] = "[REDACTED: contains sensitive marker]"
            break
    info["pid"] = int(info.get("pid") or 0)
    return info

def save_real_server(cfg: MatrixConfig) -> dict[str, Any]:
    info = port_info(cfg.real_port)
    payload = {
        "captured_at": utc_now(),
        "port": cfg.real_port,
        "was_listening": info is not None,
        "process": info,
        "restore_working_directory": str(cfg.source_root),
    }
    write_json_atomic(cfg.evidence_root / "real_server.json", payload)
    return payload


def _looks_like_project_uvicorn(cfg: MatrixConfig, info: dict[str, Any]) -> bool:
    """True when the listener is this project's own uvicorn, not an unrelated program."""
    command_line = str(info.get("command_line") or "").lower()
    if not command_line:
        return False
    if "uvicorn" not in command_line or "app.main:app" not in command_line:
        return False
    source = str(cfg.source_root).replace("\\", "/").lower()
    return source in command_line.replace("\\", "/")


def stop_real_server(cfg: MatrixConfig) -> dict[str, Any]:
    saved = save_real_server(cfg)
    info = saved.get("process")
    if not info:
        return saved | {"stopped": False, "reason": "no listener"}
    if not _looks_like_project_uvicorn(cfg, info):
        raise RuntimeError(
            "refusing to stop the listener on port "
            f"{cfg.real_port}: it is not this project's uvicorn "
            f"(command line unverified). Process left running."
        )
    pid = int(info["pid"])
    script = (
        f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\" -ErrorAction SilentlyContinue; "
        "if ($p) { Stop-Process -Id " + str(pid) + " -Force -ErrorAction Stop }"
    )
    result = _powershell(script, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"failed to stop real server pid {pid}: {result.stdout[:500]}")
    saved["stopped"] = True
    saved["stopped_at"] = utc_now()
    write_json_atomic(cfg.evidence_root / "real_server.json", saved)
    return saved


def _split_windows_command_line(command_line: str) -> list[str]:
    try:
        parts = shlex.split(command_line, posix=False)
    except ValueError:
        parts = command_line.split()
    return [part.strip('"') for part in parts if part.strip()]


def restore_real_server(cfg: MatrixConfig) -> dict[str, Any]:
    """Restore the previously captured :8000 command via hidden Start-Process."""
    saved = read_json(cfg.evidence_root / "real_server.json", default=None)
    if not saved or not saved.get("was_listening") or not saved.get("process"):
        return {"restored": False, "reason": "no captured listener"}
    info = saved["process"]
    command_line = str(info.get("command_line") or "")
    if not command_line or command_line.startswith("[REDACTED"):
        raise RuntimeError("cannot restore real server: command line was not safely captured")
    parts = _split_windows_command_line(command_line)
    if not parts:
        raise RuntimeError("cannot restore real server: empty command line")
    # Do not trust the parent interpreter path reported by WMI. On Windows a
    # venv python is often reported as its base interpreter, which would start
    # the server outside the project venv. Prefer the first token of the saved
    # command line, then the project venv when it is a python executable.
    command_python = str(cfg.source_python)
    if "python" in Path(command_python).name.lower() and Path(command_python).is_file():
        executable = command_python
    else:
        executable = str(info.get("executable_path") or command_python)
    args = parts[1:]
    argument_literal = "@(" + ",".join(_ps_quote(item) for item in args) + ")"
    cwd = str(saved.get("restore_working_directory") or cfg.source_root)
    script = (
        f"$p = Start-Process -FilePath {_ps_quote(executable)} "
        f"-ArgumentList {argument_literal} -WorkingDirectory {_ps_quote(cwd)} "
        "-WindowStyle Hidden -PassThru; $p.Id | ConvertTo-Json -Compress"
    )
    result = _powershell(script, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"failed to restore real server: {result.stdout[:500]}")
    restored_pid: int | None = None
    try:
        restored_pid = int((result.stdout or "").strip())
    except ValueError:
        pass
    out = {"restored": True, "pid": restored_pid, "command": [executable, *args]}
    write_json_atomic(cfg.evidence_root / "real_server-restore.json", out)
    return out


def _sandbox_state_path(cfg: MatrixConfig) -> Path:
    return cfg.evidence_root / "sandbox-server.json"


def start_sandbox(cfg: MatrixConfig) -> dict[str, Any]:
    cfg.ensure_layout()
    if port_in_use(cfg.port):
        raise RuntimeError(f"sandbox port {cfg.port} is already in use")
    if not (cfg.code_dir / "app" / "main.py").is_file():
        raise RuntimeError("sandbox code is not prepared; run prepare before g1")
    log_path = cfg.evidence_root / "sandbox-server.log"
    cmd = [
        str(cfg.source_python),
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(cfg.port),
        "--workers",
        "1",
        "--no-access-log",
        "--log-level",
        "warning",
    ]
    log_fh = log_path.open("a", encoding="utf-8", newline="\n")
    creationflags = CREATE_NO_WINDOW if IS_WINDOWS else 0
    proc = subprocess.Popen(
        cmd,
        cwd=str(cfg.code_dir),
        env=cfg.child_env(),
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=not IS_WINDOWS,
        creationflags=creationflags,
    )
    state = {
        "started_at": utc_now(),
        "pid": proc.pid,
        "port": cfg.port,
        "cwd": str(cfg.code_dir),
        "command": cmd,
        "log": str(log_path),
    }
    write_json_atomic(_sandbox_state_path(cfg), state)
    log_fh.close()
    return state


def wait_health(cfg: MatrixConfig, *, timeout_seconds: int = 60) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with NO_PROXY_OPENER.open(cfg.health_url, timeout=3) as response:
                if response.status == 200:
                    return {"ok": True, "url": cfg.health_url, "status": response.status}
        except Exception as exc:  # noqa: BLE001 - health polling must return useful diagnostics
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"sandbox health check failed: {cfg.health_url}: {last_error}")


def _sandbox_process_matches(cfg: MatrixConfig, info: dict[str, Any] | None) -> bool:
    if not info:
        return False
    command_line = str(info.get("command_line") or "")
    code = str(cfg.code_dir).replace("\\", "/").lower()
    return ("uvicorn" in command_line.lower() and "app.main:app" in command_line.lower()
            and (code in command_line.replace("\\", "/").lower() or str(cfg.port) in command_line))


def stop_sandbox(cfg: MatrixConfig) -> dict[str, Any]:
    state = read_json(_sandbox_state_path(cfg), default=None)
    pid = int((state or {}).get("pid") or 0)
    if not pid:
        return {"stopped": False, "reason": "no sandbox pid"}
    info = port_info(cfg.port) or process_info(pid)
    if info and int(info.get("pid") or 0) != pid:
        # Repeated manual G1 runs can leave a stale state PID while the port is
        # owned by a newer sandbox uvicorn. Verify the live process, then use it.
        if not _sandbox_process_matches(cfg, info):
            raise RuntimeError("refusing to kill a port listener that is not the sandbox uvicorn")
        pid = int(info["pid"])
        state = dict(state or {})
        state["pid"] = pid
        write_json_atomic(_sandbox_state_path(cfg), state)
    if not info or not _sandbox_process_matches(cfg, info):
        raise RuntimeError("refusing to kill a port listener that is not the sandbox uvicorn")
    script = f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\" -ErrorAction SilentlyContinue; if($p){{Stop-Process -Id {pid} -Force -ErrorAction Stop}}"
    result = _powershell(script, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"failed to stop sandbox: {result.stdout[:500]}")
    out = {"stopped": True, "pid": pid, "stopped_at": utc_now()}
    write_json_atomic(cfg.evidence_root / "sandbox-server-stop.json", out)
    return out


def _cookie_header_is_host_only(header: str | None) -> dict[str, Any]:
    raw = header or ""
    lower = raw.lower()
    return {
        "has_access_token": "access_token=" in lower,
        "has_domain_attribute": "domain=" in lower,
        "has_httponly": "httponly" in lower,
        "has_samesite_lax": "samesite=lax" in lower.replace(" ", ""),
        "has_secure": "secure" in lower,
    }


def probe_login_cookie(cfg: MatrixConfig, host: str, username: str, password: str) -> dict[str, Any]:
    url = f"http://{host}:{cfg.port}/api/v1/auth/login"
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with NO_PROXY_OPENER.open(request, timeout=20) as response:
            cookie = response.headers.get("Set-Cookie")
            status = response.status
    except urllib.error.HTTPError as exc:
        return {"host": host, "status": exc.code, "cookie": None, "ok": False}
    except (TimeoutError, OSError) as exc:
        return {"host": host, "status": None, "cookie": None, "ok": False, "error": type(exc).__name__}
    info = _cookie_header_is_host_only(cookie)
    return {
        "host": host,
        "status": status,
        "cookie": info,
        "ok": info["has_access_token"] and not info["has_domain_attribute"]
        and info["has_httponly"] and info["has_samesite_lax"],
    }


def check_hosts(cfg: MatrixConfig) -> dict[str, Any]:
    """Check DNS, health, and host-only authentication cookies without ego."""
    hosts = ["admin.localhost", "user-a.localhost", "user-b.localhost", "target.localhost", "outside.localhost", "anon.localhost"]
    results: dict[str, Any] = {"base": cfg.base_url, "hosts": {}}
    for host in hosts:
        try:
            address = socket.gethostbyname(host)
        except OSError as exc:
            results["hosts"][host] = {"dns": False, "error": str(exc)}
            continue
        health = None
        try:
            with NO_PROXY_OPENER.open(f"http://{host}:{cfg.port}/health", timeout=10) as response:
                health = response.status
        except Exception as exc:  # noqa: BLE001
            health = {"error": str(exc)}
        results["hosts"][host] = {"dns": True, "address": address, "health": health}
    cookie_probes = {
        "admin.localhost": (cfg.admin_username, cfg.admin_password),
        "user-a.localhost": (cfg.user_username, cfg.user_password),
        "user-b.localhost": ("user_fin_1", cfg.user_password),
        "target.localhost": ("user_hr_1", cfg.user_password),
        "outside.localhost": ("user_fin_1", cfg.user_password),
    }
    results["cookie_probes"] = {}
    for host, (username, password) in cookie_probes.items():
        results["cookie_probes"][host] = probe_login_cookie(cfg, host, username, password)
    results["ok"] = (
        all(item.get("dns") is True and item.get("health") == 200 for item in results["hosts"].values())
        and all(item.get("ok") is True for item in results["cookie_probes"].values())
    )
    write_json_atomic(cfg.evidence_root / "g1-hosts.json", results)
    return results


__all__ = [
    "check_hosts",
    "port_in_use",
    "port_info",
    "probe_login_cookie",
    "restore_real_server",
    "save_real_server",
    "start_sandbox",
    "stop_real_server",
    "stop_sandbox",
    "wait_health",
]