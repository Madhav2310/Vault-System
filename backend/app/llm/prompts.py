"""Prompt registry (design §19): `backend/prompts/<id>@<version>.md` with a flat front-matter block.

    ---
    id: analyze_query
    version: 1
    purpose: analyze_query
    role: fast                # fast | balanced | deep | flagship; the router maps it to a model
    output: schema            # schema | segments | citations | text
    effort: low               # none | low | medium | high | xhigh | max (per-model support: routing.py)
    max_tokens: 2048
    ---
    <system prompt body>

The body is the system prompt and is sent verbatim (no templating), so it stays a stable cache prefix.
"""

import hashlib
import pathlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, cast, get_args

from app.llm.routing import ROLES, Role
from app.llm.types import Effort

PROMPTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "prompts"

Output = Literal["schema", "segments", "citations", "text"]
STRUCTURED_OUTPUTS = frozenset({"schema", "segments"})  # the call carries a strict JSON schema
REQUIRED_FIELDS = ("id", "version", "purpose", "role", "output", "effort", "max_tokens")
_FILENAME = re.compile(r"^(?P<id>[a-z_][a-z0-9_]*)@(?P<version>[1-9][0-9]*)\.md$")
_FRONT = re.compile(r"\A---\n(?P<head>.*?)\n---\n(?P<body>.*)\Z", re.S)


class PromptRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class Prompt:
    id: str
    version: int
    purpose: str
    role: Role
    output: Output
    effort: Effort
    max_tokens: int
    body: str
    digest: str  # sha256 of the body; recorded with runs

    @property
    def key(self) -> tuple[str, int]:
        return (self.id, self.version)


def parse_prompt(path: pathlib.Path) -> Prompt:
    where = path.name
    m = _FRONT.match(path.read_text(encoding="utf-8"))
    if not m:
        raise PromptRegistryError(f"{where}: missing front-matter block")
    meta: dict[str, str] = {}
    for line in m["head"].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise PromptRegistryError(f"{where}: bad front-matter line {line!r}")
        meta[key.strip()] = value.split(" #", 1)[0].strip()
    missing = [f for f in REQUIRED_FIELDS if not meta.get(f)]
    if missing:
        raise PromptRegistryError(f"{where}: missing front-matter fields {missing}")
    if meta["output"] not in get_args(Output):
        raise PromptRegistryError(f"{where}: output must be one of {get_args(Output)}")
    if meta["effort"] not in get_args(Effort):
        raise PromptRegistryError(f"{where}: effort must be one of {get_args(Effort)}")
    if meta["role"] not in ROLES:
        raise PromptRegistryError(f"{where}: role must be one of {list(ROLES)}")
    try:
        version, max_tokens = int(meta["version"]), int(meta["max_tokens"])
    except ValueError:
        raise PromptRegistryError(f"{where}: version and max_tokens must be integers") from None
    fm = _FILENAME.match(path.name)
    if not fm or (fm["id"], int(fm["version"])) != (meta["id"], version):
        raise PromptRegistryError(f"{where}: file name must be <id>@<version>.md matching the front-matter")
    body = m["body"].strip()
    if not body:
        raise PromptRegistryError(f"{where}: empty body")
    return Prompt(
        id=meta["id"],
        version=version,
        purpose=meta["purpose"],
        role=meta["role"],
        output=cast(Output, meta["output"]),
        effort=cast(Effort, meta["effort"]),
        max_tokens=max_tokens,
        body=body,
        digest=hashlib.sha256(body.encode()).hexdigest(),
    )


class PromptRegistry:
    def __init__(self, prompts: Iterable[Prompt] = ()):
        self._prompts: dict[tuple[str, int], Prompt] = {}
        for p in prompts:
            self.register(p)

    def register(self, prompt: Prompt) -> None:
        if prompt.key in self._prompts:
            raise PromptRegistryError(f"duplicate prompt {prompt.id}@{prompt.version}")
        self._prompts[prompt.key] = prompt

    def get(self, prompt_id: str, version: int) -> Prompt:
        try:
            return self._prompts[(prompt_id, version)]
        except KeyError:
            raise PromptRegistryError(f"unknown prompt {prompt_id}@{version}") from None

    def __len__(self) -> int:
        return len(self._prompts)

    @classmethod
    def load(cls, *dirs: pathlib.Path) -> "PromptRegistry":
        return cls(parse_prompt(p) for d in dirs for p in sorted(d.glob("*.md")))


@lru_cache(maxsize=1)
def default_registry() -> PromptRegistry:
    return PromptRegistry.load(PROMPTS_DIR)
