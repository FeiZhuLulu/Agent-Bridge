from pathlib import Path

from agent_bridge.adapters.acp import AcpAdapter
from agent_bridge.adapters.antigravity import AgyAdapter
from agent_bridge.adapters.base import Adapter
from agent_bridge.adapters.fake import FakeAdapter
from agent_bridge.config import AgentConfig, EnvConfig


def build_adapter(cfg: AgentConfig, home: Path, env_config: EnvConfig | None = None) -> Adapter:
    if cfg.protocol == "fake":
        return FakeAdapter(cfg, home, env_config)
    if cfg.protocol == "agy":
        return AgyAdapter(cfg, home, env_config)
    if cfg.protocol == "acp":
        return AcpAdapter(cfg, home, env_config)
    raise ValueError(f"unsupported protocol {cfg.protocol!r} for agent {cfg.name}")
