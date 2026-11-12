import pathlib

import yaml

CONFIG = pathlib.Path(__file__).resolve().parents[4] / "ops/otel/collector.yaml"


def test_redaction_attributes() -> None:
    cfg = yaml.safe_load(CONFIG.read_text())
    actions = cfg["processors"]["attributes/redact_genai"]["actions"]
    assert {a["key"] for a in actions if a["action"] == "delete"} == {
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.system_instructions",
    }
    for signal in ("traces", "logs"):
        procs = cfg["service"]["pipelines"][signal]["processors"]
        assert procs.index("attributes/redact_genai") < procs.index("batch"), signal
        assert procs[0] == "memory_limiter"
