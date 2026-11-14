"""Model roles and per-node routing. The only module (with settings) that names models.

Nodes ask for a *role*; settings map roles to models; `ModelRouter.resolve(node, route, mode)` returns the
role, model and effort for one call. The LLM never picks its own model. Every pairing is validated when the
router is built, so a bad `MODEL_*` / `MODEL_ROUTES` value fails at startup and names the node.
"""

from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from app.core.settings import Settings
from app.llm.types import Effort

Role = Literal["fast", "balanced", "deep", "flagship"]
ROLES: tuple[Role, ...] = get_args(Role)
EFFORTS: tuple[Effort, ...] = get_args(Effort)

_ALL: frozenset[Effort] = frozenset(EFFORTS)

# Efforts each model accepts, probed 2026-11-13 (docs/evidence/spike_openai.md): gpt-6.1-sol rejects `none`.
# A model not listed here cannot be routed to until it is probed and added.
EFFORT_SUPPORT: dict[str, frozenset[Effort]] = {
    "gpt-6-luna": _ALL,
    "gpt-6.1-terra": _ALL,
    "gpt-6.1-sol": _ALL - {"none"},
    "gpt-6-astra": _ALL,
}

# One within-provider fallback attempt after retries are exhausted on a retryable error.
FALLBACK_ROLE: dict[Role, Role] = {"fast": "balanced", "balanced": "deep"}

DEGRADED_MODEL_FALLBACK = "DEGRADED_MODEL_FALLBACK"


class RoutingConfigError(ValueError):
    """Invalid role/model/effort configuration. The message names the node (or role) at fault."""


@dataclass(frozen=True)
class Route:
    role: Role
    effort: Effort
    model: str | None = None  # explicit pin from MODEL_ROUTES; None = the role's configured model


@dataclass(frozen=True)
class Resolved:
    role: Role
    model: str
    effort: Effort


# Default routing table. Key: "node", "node:route", "node::mode" or "node:route:mode".
# Lookup order is most specific first: node:route:mode, node:route, node::mode, node.
DEFAULT_ROUTES: dict[str, Route] = {
    "analyze_query": Route("fast", "none"),
    "evidence_gate": Route("balanced", "low"),
    "generate": Route("balanced", "none"),  # qa / lookup over retrieved evidence
    "generate::full_context": Route("balanced", "low"),
    "generate:compare": Route("deep", "low"),
    "generate:conflict": Route("deep", "low"),  # the gate reported conflicting sources
    "brief_extract": Route("balanced", "low"),
    "summary": Route("balanced", "low"),
    "followups": Route("fast", "none"),
    "memory_compact": Route("fast", "none"),
    "tag_category": Route("fast", "none"),
    "contextualize": Route("fast", "none"),
    "matrix_cell": Route("balanced", "low"),
    "eval_judge": Route("deep", "low"),
}


def route_key(node: str, route: str | None = None, mode: str | None = None) -> str:
    if mode is not None:
        return f"{node}:{route or ''}:{mode}"
    return f"{node}:{route}" if route is not None else node


def _candidate_keys(node: str, route: str | None, mode: str | None) -> list[str]:
    keys = [route_key(node, route, mode), route_key(node, route), route_key(node, None, mode), node]
    return list(dict.fromkeys(k for k in keys if k))


def parse_overrides(raw: dict[str, dict[str, Any]]) -> dict[str, Route]:
    """`MODEL_ROUTES` JSON → routes. Each value: {"role": ..., "effort": ..., "model": optional pin}."""
    out: dict[str, Route] = {}
    for key, v in raw.items():
        extra = set(v) - {"role", "effort", "model"}
        if extra or "role" not in v or "effort" not in v:
            raise RoutingConfigError(f"MODEL_ROUTES[{key}]: needs role and effort (and optionally model)")
        if v["role"] not in ROLES:
            raise RoutingConfigError(f"MODEL_ROUTES[{key}]: unknown role {v['role']!r}")
        if v["effort"] not in EFFORTS:
            raise RoutingConfigError(f"MODEL_ROUTES[{key}]: unknown effort {v['effort']!r}")
        out[key] = Route(cast(Role, v["role"]), cast(Effort, v["effort"]), v.get("model"))
    return out


class ModelRouter:
    def __init__(
        self,
        role_models: dict[Role, str],
        overrides: dict[str, Route] | None = None,
        defaults: dict[str, Route] | None = None,
    ):
        self.role_models = dict(role_models)
        self.routes: dict[str, Route] = {**(defaults if defaults is not None else DEFAULT_ROUTES)}
        self.routes.update(overrides or {})
        self._validate()

    @classmethod
    def from_settings(cls, s: Settings) -> "ModelRouter":
        role_models: dict[Role, str] = {
            "fast": s.model_fast,
            "balanced": s.model_balanced,
            "deep": s.model_deep,
            "flagship": s.model_flagship,
        }
        return cls(role_models, parse_overrides(s.model_routes))

    def _check(self, where: str, model: str, effort: Effort) -> None:
        supported = EFFORT_SUPPORT.get(model)
        if supported is None:
            raise RoutingConfigError(f"{where}: unknown model {model!r} (no EFFORT_SUPPORT entry)")
        if effort not in supported:
            raise RoutingConfigError(f"{where}: model {model!r} does not support effort {effort!r}")

    def _model_of(self, r: Route) -> str:
        return r.model or self.role_models[r.role]

    def _validate(self) -> None:
        for role in ROLES:
            if role not in self.role_models:
                raise RoutingConfigError(f"role {role!r} has no model")
            if self.role_models[role] not in EFFORT_SUPPORT:
                raise RoutingConfigError(f"role {role!r}: unknown model {self.role_models[role]!r}")
        for key, r in self.routes.items():
            self._check(f"route {key!r}", self._model_of(r), r.effort)

    def resolve(
        self,
        node: str,
        route: str | None = None,
        mode: str | None = None,
        *,
        default: Route | None = None,
    ) -> Resolved:
        """Most specific table row wins; `default` (the prompt's front-matter) applies when no row matches."""
        for key in _candidate_keys(node, route, mode):
            r = self.routes.get(key)
            if r is not None:
                break
        else:
            if default is None:
                raise RoutingConfigError(f"no route for node {node!r}")
            r = default
            self._check(f"node {node!r}", self._model_of(r), r.effort)
        return Resolved(r.role, self._model_of(r), r.effort)

    def fallback(self, res: Resolved) -> Resolved | None:
        """The role one tier up, or None. Keeps the effort when the fallback model supports it, otherwise the
        lowest effort it supports: the downgrade is recorded as DEGRADED_MODEL_FALLBACK, never silent."""
        role = FALLBACK_ROLE.get(res.role)
        if role is None:
            return None
        model = self.role_models[role]
        supported = EFFORT_SUPPORT[model]
        effort = res.effort if res.effort in supported else next(e for e in EFFORTS if e in supported)
        return Resolved(role, model, effort)
