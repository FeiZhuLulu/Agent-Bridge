import pytest

from agent_bridge.config import AgentConfig, EnvConfig, load_config
from agent_bridge.probes import probe_agent
from agent_bridge.traits import Assurance, Capability, ObservationSource, traits_for


def test_bundled_traits_are_explicit_and_normalized(tmp_path):
    traits = traits_for(load_config(tmp_path).agents["grok"])

    assert traits.resume is Capability.supported
    assert traits.cancellation is Capability.supported
    assert traits.model_selection is Capability.supported
    assert traits.effort_selection is Capability.supported
    assert traits.permission_assurance is Assurance.declared
    assert traits.identity_assurance is Assurance.observed
    assert traits.revivability is Capability.supported
    assert traits.observation_source is ObservationSource.grok_sampler


def test_unknown_agent_capabilities_are_not_inferred_from_its_name():
    traits = traits_for(AgentConfig(name="grok-compatible", protocol="acp", command=["custom"]))

    assert traits.resume is Capability.unknown
    assert traits.cancellation is Capability.unknown
    assert traits.model_selection is Capability.unknown
    assert traits.effort_selection is Capability.unknown
    assert traits.permission_assurance is Assurance.unknown
    assert traits.identity_assurance is Assurance.unknown
    assert traits.revivability is Capability.unknown


def test_kimi_identity_is_not_inferred_from_request_binding(tmp_path):
    traits = traits_for(load_config(tmp_path).agents["kimi"])

    assert traits.identity_assurance is Assurance.unknown


def test_legacy_bundled_identifier_uses_an_explicit_registry_entry():
    traits = traits_for(AgentConfig(name="dsh", protocol="acp", command=["dsh-acp-demo"]))

    assert traits.launch_resolver.value == "dsh"
    assert traits.model_selection is Capability.supported


@pytest.mark.asyncio
async def test_probe_exposes_traits_for_coordinator_capability_routing(monkeypatch):
    async def probe_version(executable):
        return "probe"

    monkeypatch.setattr("agent_bridge.probes.resolve_command", lambda command, fallbacks=None: list(command))
    monkeypatch.setattr("agent_bridge.probes.build_worker_env", lambda *args, **kwargs: {})
    monkeypatch.setattr("agent_bridge.probes._version_string", probe_version)

    row = await probe_agent(
        AgentConfig(name="grok", protocol="acp", command=["grok"]),
        EnvConfig(discover_proxy=False, inherit=[]),
    )

    assert row["traits"]["model_selection"] == "supported"
    assert row["traits"]["identity_assurance"] == "observed"
