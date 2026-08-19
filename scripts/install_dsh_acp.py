"""Optional portable install of DeepSeek's published ACP package.

Product ``dsh`` has no ACP profile. Any user can either:

- ``npm install -g @deepseek-ai/dsh-acp-demo`` (same prefix as their ``dsh``), or
- run this helper, which writes ``$AGENT_BRIDGE_HOME/dsh-acp`` and does not
  need write access to the global npm prefix.

    .\\.venv\\Scripts\\python.exe scripts\\install_dsh_acp.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_bridge.dsh_home import dsh_acp_install_dir, dsh_acp_packages


def main() -> int:
    npm = shutil.which("npm")
    if not npm:
        print("npm not found", file=sys.stderr)
        return 2
    prefix = dsh_acp_install_dir()
    prefix.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    cmd = [npm, "install", "--prefix", str(prefix), *dsh_acp_packages()]
    print(" ".join(cmd))
    proc = subprocess.run(cmd, env=env, check=False)
    js = prefix / "node_modules" / "@deepseek-ai" / "dsh-acp-demo" / "lib" / "bin.js"
    if proc.returncode != 0:
        return proc.returncode
    if not js.is_file():
        print(f"install finished but bin missing: {js}", file=sys.stderr)
        return 1
    print(f"installed {js}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
