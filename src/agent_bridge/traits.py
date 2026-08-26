"""Provider-neutral worker capability declarations.

Traits describe what an agent configuration has explicitly promised.  A custom
agent without a declaration stays ``unknown`` until its integration supplies
one.  This keeps callers from treating an unfamiliar ACP implementation as if
it behaved like a bundled worker.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, Field


class Capability(StrEnum):
    unknown = "unknown"
    supported = "supported"
    unsupported = "unsupported"


class Assurance(StrEnum):
    """How strongly Bridge can state an identity property."""

    unknown = "unknown"
    declared = "declared"
    applied = "applied"
    observed = "observed"


class PermissionAssurance(StrEnum):
    """How strongly Bridge can state the effective permission policy."""

    enforced = "enforced"
    observed = "observed"
    unsupported = "unsupported"


class ObservationSource(StrEnum):
    adapter = "adapter"
    grok_sampler = "grok_sampler"
    kimi_sampler = "kimi_sampler"


class LaunchResolver(StrEnum):
    standard = "standard"
    dsh = "dsh"


class ProbeProfile(StrEnum):
    generic = "generic"
    dsh = "dsh"
    kimi = "kimi"
    opencode = "opencode"


class AgentTraits(BaseModel):
    """Normalized capabilities for one configured worker.

    The first five fields describe the worker control surface.  Assurance
    fields intentionally do not claim policy enforcement; they only state the
    strongest runtime evidence this Bridge integration currently has.
    """

    resume: Capability = Capability.unknown
    cancellation: Capability = Capability.unknown
    model_selection: Capability = Capability.unknown
    effort_selection: Capability = Capability.unknown
    permission_assurance: PermissionAssurance = PermissionAssurance.unsupported
    identity_assurance: Assurance = Assurance.unknown
    revivability: Capability = Capability.unknown
    observation_source: ObservationSource = ObservationSource.adapter
    launch_resolver: LaunchResolver = LaunchResolver.standard
    probe_profile: ProbeProfile = ProbeProfile.generic
    probe_notes: list[str] = Field(default_factory=list)
    result_hint: str | None = None


BUILTIN_TRAITS: dict[str, AgentTraits] = {
    "grok": AgentTraits(
        resume="supported", cancellation="supported", model_selection="supported", effort_selection="supported",
        permission_assurance="unsupported", identity_assurance="observed", revivability="supported",
        observation_source="grok_sampler",
        probe_notes=["model=grok models slugs via session/setModel after /new; effort=off|low|medium|high|max (off->none, max->xhigh)"],
        result_hint="Grok system-prompt identity is not the selected model; use observed_model from this payload.",
    ),
    "kimi": AgentTraits(
        resume="supported", cancellation="supported", model_selection="supported", effort_selection="supported",
        permission_assurance="observed", identity_assurance="unknown", revivability="supported",
        observation_source="kimi_sampler", probe_profile="kimi",
        probe_notes=["model=slugs the session advertises e.g. kimi-code/k3, kimi-code/k3-256k; effort mapped onto that model's thinking levels; mode forced to yolo"],
        result_hint="Kimi reports a failed turn as end_turn with empty text; an empty result is only clean if warnings is empty.",
    ),
    "cursor": AgentTraits(
        resume="unsupported", cancellation="supported", model_selection="unsupported", effort_selection="unsupported", revivability="unsupported",
    ),
    "dsh": AgentTraits(
        resume="unsupported", cancellation="supported", model_selection="supported", effort_selection="supported",
        permission_assurance="unsupported", identity_assurance="declared", revivability="unsupported",
        launch_resolver="dsh", probe_profile="dsh",
        probe_notes=["effort=off|low|high|max via dispatch_task.effort; same session model change respawns"],
    ),
    "opencode": AgentTraits(
        resume="supported", cancellation="supported", model_selection="supported", effort_selection="supported",
        permission_assurance="observed", identity_assurance="applied", revivability="supported", probe_profile="opencode",
        probe_notes=["model=provider/model slugs the session advertises e.g. opencode/..., xai/...; effort mapped onto that model's variants", "auth=provider API keys via `opencode auth` (official OpenCode Zen / Go, or any connected provider); no product login"],
        result_hint="OpenCode observed_model/effort are the last values Bridge successfully set on the session after mapping, not a live sampler.",
    ),
    "antigravity": AgentTraits(
        resume="supported", cancellation="supported", model_selection="supported", effort_selection="supported",
        permission_assurance="unsupported", identity_assurance="declared", revivability="supported",
        probe_notes=["model=agy models slugs e.g. gemini-3.7-flash; effort=low|medium|high"],
    ),
}


def traits_for(agent: object) -> AgentTraits:
    """Return an agent's explicit traits, or an all-unknown declaration.

    Explicit TOML traits always win.  For backward-compatible direct
    ``AgentConfig`` construction, exact bundled identifiers have a registered
    declaration too.  There is no name heuristic: anything not explicitly
    registered or configured gets the safe all-unknown default.
    """

    traits = getattr(agent, "traits", None)
    fields_set = getattr(agent, "model_fields_set", set())
    if isinstance(traits, AgentTraits) and "traits" in fields_set:
        return traits
    return BUILTIN_TRAITS.get(str(getattr(agent, "name", "")), AgentTraits())


def traits_for_historical_agent(
    name: str,
    configured_agents: Mapping[str, object],
) -> AgentTraits:
    """Resolve traits for a persisted task without requiring its config entry.

    A currently configured agent keeps any local trait override.  Once an
    agent has been removed, only an exact bundled registration may contribute
    a historical hint; custom names deliberately fall back to all-unknown.
    """

    agent = configured_agents.get(name)
    if agent is not None:
        return traits_for(agent)
    return BUILTIN_TRAITS.get(name, AgentTraits())
