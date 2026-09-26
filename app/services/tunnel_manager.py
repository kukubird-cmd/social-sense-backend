"""
Tunnel Manager Service
Manages automated background tunnels (ngrok with localtunnel fallback),
auto-detects live public URLs via the local ngrok client API (port 4040),
and persists the active webhook base URL.
"""
import os
import sys
import time
import shutil
import asyncio
import logging
import subprocess
from typing import Optional, Dict, Any
import httpx

logger = logging.getLogger("tunnel_manager")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TUNNEL_FILE = os.path.join(BASE_DIR, "tunnel_url.txt")

_active_process: Optional[subprocess.Popen] = None
_current_tunnel_url: str = ""

# Load persisted URL on import if available
if os.path.exists(TUNNEL_FILE):
    try:
        with open(TUNNEL_FILE, "r", encoding="utf-8") as f:
            saved = f.read().strip()
            if saved and (saved.startswith("http://") or saved.startswith("https://")):
                _current_tunnel_url = saved
            else:
                _current_tunnel_url = ""
    except Exception:
        pass


def get_current_url() -> str:
    global _current_tunnel_url
    return _current_tunnel_url


def set_current_url(url: str) -> str:
    global _current_tunnel_url
    cleaned = url.strip().rstrip("/")
    _current_tunnel_url = cleaned
    try:
        with open(TUNNEL_FILE, "w", encoding="utf-8") as f:
            f.write(cleaned)
    except Exception as e:
        logger.warning(f"Could not persist tunnel URL: {e}")
    return cleaned


async def check_ngrok_api() -> Optional[str]:
    """Check if a local ngrok agent is running on port 4040 and return its https public URL."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://127.0.0.1:4040/api/tunnels")
            if resp.status_code == 200:
                tunnels = resp.json().get("tunnels", [])
                for t in tunnels:
                    u = t.get("public_url", "")
                    if u.startswith("https://"):
                        return u
                # If only http is found, return that
                for t in tunnels:
                    u = t.get("public_url", "")
                    if u:
                        return u
    except Exception:
        pass
    return None


async def get_tunnel_status() -> Dict[str, Any]:
    """Returns comprehensive tunnel status."""
    global _current_tunnel_url, _active_process

    # 1. Check if local ngrok agent is active
    ngrok_url = await check_ngrok_api()
    if ngrok_url:
        _current_tunnel_url = ngrok_url
        set_current_url(ngrok_url)
        return {
            "status": "online",
            "tunnel_url": _current_tunnel_url,
            "type": "ngrok",
            "source": "ngrok_agent_4040",
            "process_active": _active_process is not None and _active_process.poll() is None,
            "message": "ngrok tunnel active via local agent (port 4040)"
        }

    # 2. Check if a managed background process is running
    is_running = _active_process is not None and _active_process.poll() is None

    # 3. Read saved file if memory is empty
    if not _current_tunnel_url and os.path.exists(TUNNEL_FILE):
        try:
            with open(TUNNEL_FILE, "r", encoding="utf-8") as f:
                saved = f.read().strip()
                if saved:
                    _current_tunnel_url = saved
        except Exception:
            pass

    tunnel_type = "custom"
    if "ngrok" in _current_tunnel_url:
        tunnel_type = "ngrok"
    elif "loca.lt" in _current_tunnel_url:
        tunnel_type = "localtunnel"
    elif "serveo" in _current_tunnel_url:
        tunnel_type = "serveo"

    return {
        "status": "online" if _current_tunnel_url else "offline",
        "tunnel_url": _current_tunnel_url,
        "type": tunnel_type,
        "source": "configured_tunnel" if _current_tunnel_url else "none",
        "process_active": is_running,
        "message": (
            f"Active {tunnel_type} tunnel: {_current_tunnel_url}"
            if _current_tunnel_url
            else "No active public tunnel detected. Start ngrok or input your URL."
        )
    }


def _find_ngrok_bin() -> Optional[str]:
    """Find ngrok executable on system path or standard locations."""
    # Check standard PATH
    path_bin = shutil.which("ngrok")
    if path_bin:
        return path_bin

    # Windows user local AppData
    app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(app_data, "Programs", "ngrok", "ngrok.exe"),
        os.path.join(app_data, "Microsoft", "WinGet", "Links", "ngrok.exe"),
        os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Local", "Microsoft", "WinGet", "Packages", "Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe", "ngrok.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


async def start_background_tunnel(port: int = 8000, preferred: str = "ngrok") -> Dict[str, Any]:
    """
    Spawns ngrok in the background. If ngrok fails or is blocked by OS policies,
    falls back to localtunnel automatically.
    """
    global _active_process, _current_tunnel_url

    # Check if ngrok is already running on port 4040
    existing = await check_ngrok_api()
    if existing:
        set_current_url(existing)
        return {
            "status": "online",
            "tunnel_url": existing,
            "type": "ngrok",
            "source": "already_running_ngrok",
            "message": f"Connected to existing ngrok tunnel: {existing}"
        }

    # If an existing process is still running, check if it already has a URL
    if _active_process is not None and _active_process.poll() is None:
        status = await get_tunnel_status()
        if status["status"] == "online":
            return status

    # Attempt 1: Start ngrok
    ngrok_bin = _find_ngrok_bin()
    ngrok_started = False

    if preferred == "ngrok" and ngrok_bin:
        try:
            logger.info(f"Starting background ngrok process via {ngrok_bin} on port {port}...")
            # Use CREATE_NO_WINDOW on Windows
            creationflags = 0x08000000 if sys.platform == "win32" else 0
            _active_process = subprocess.Popen(
                [ngrok_bin, "http", str(port), "--log=stdout"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creationflags
            )
            # Wait up to 5 seconds for ngrok API to come up
            for _ in range(10):
                await asyncio.sleep(0.5)
                if _active_process.poll() is not None:
                    # Process exited early (e.g. App Control block or error)
                    break
                url = await check_ngrok_api()
                if url:
                    set_current_url(url)
                    return {
                        "status": "online",
                        "tunnel_url": url,
                        "type": "ngrok",
                        "source": "auto_started_ngrok",
                        "message": f"Successfully launched background ngrok tunnel: {url}"
                    }
        except Exception as e:
            logger.warning(f"Failed to launch ngrok binary: {e}")

    # Attempt 2: Fallback to localtunnel via npx
    logger.info("Attempting localtunnel fallback in background...")
    try:
        npx_cmd = "npx.cmd" if sys.platform == "win32" else "npx"
        creationflags = 0x08000000 if sys.platform == "win32" else 0
        proc = subprocess.Popen(
            [npx_cmd, "--yes", "localtunnel", "--port", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=creationflags
        )
        _active_process = proc

        # Read line from stdout to get the URL
        loop = asyncio.get_running_loop()
        def read_output():
            try:
                for line in iter(proc.stdout.readline, ''):
                    if "your url is:" in line.lower():
                        return line.split(":", 1)[1].strip()
            except Exception:
                return None
            return None

        # Wait up to 8 seconds for localtunnel to emit URL
        for _ in range(16):
            await asyncio.sleep(0.5)
            if proc.poll() is not None:
                break
            # Try non-blocking check
            url = await loop.run_in_executor(None, read_output)
            if url and (url.startswith("http://") or url.startswith("https://")):
                set_current_url(url)
                return {
                    "status": "online",
                    "tunnel_url": url,
                    "type": "localtunnel",
                    "source": "auto_started_localtunnel",
                    "message": f"Background localtunnel active: {url}"
                }
    except Exception as e:
        logger.error(f"Failed to launch localtunnel: {e}")

    # Attempt 3: SSH tunnel via serveo.net (works when ngrok is blocked by App Control)
    logger.info("Attempting SSH tunnel via serveo.net...")
    try:
        ssh_cmd = "ssh"
        serveo_log = os.path.join(os.environ.get("TEMP", "/tmp"), f"serveo_{port}.txt")
        with open(serveo_log, "w") as f:
            f.write("")
        creationflags = 0x08000000 if sys.platform == "win32" else 0
        ssh_proc = subprocess.Popen(
            [
                ssh_cmd,
                "-o", "StrictHostKeyChecking=no",
                "-o", "ServerAliveInterval=60",
                "-o", "ExitOnForwardFailure=yes",
                "-R", f"80:localhost:{port}",
                "serveo.net"
            ],
            stdout=open(serveo_log, "w"),
            stderr=subprocess.STDOUT,
            creationflags=creationflags
        )
        _active_process = ssh_proc

        # Wait up to 10 seconds for serveo to print the public URL
        for _ in range(20):
            await asyncio.sleep(0.5)
            if ssh_proc.poll() is not None:
                break
            try:
                with open(serveo_log, "r") as f:
                    content = f.read()
                import re
                match = re.search(r"Forwarding HTTP traffic from (https?://\S+)", content)
                if not match:
                    match = re.search(r"(https://[a-z0-9\-]+\.serveousercontent\.com)", content)
                if match:
                    url = match.group(1).strip()
                    set_current_url(url)
                    return {
                        "status": "online",
                        "tunnel_url": url,
                        "type": "serveo",
                        "source": "auto_started_serveo",
                        "message": f"SSH tunnel via serveo.net active: {url}"
                    }
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Failed to launch SSH/serveo tunnel: {e}")

    # Fallback to current or saved URL
    status = await get_tunnel_status()
    if status["status"] == "online":
        return status

    return {
        "status": "offline",
        "tunnel_url": _current_tunnel_url,
        "type": "none",
        "source": "failed",
        "message": "Could not automatically launch tunnel. Run 'ssh -R 80:localhost:8000 serveo.net' in a terminal and paste the URL into the dashboard."
    }


def stop_background_tunnel():
    """Stops any managed background tunnel process."""
    global _active_process
    if _active_process is not None:
        try:
            _active_process.terminate()
            _active_process.wait(timeout=2)
        except Exception:
            try:
                _active_process.kill()
            except Exception:
                pass
        _active_process = None
