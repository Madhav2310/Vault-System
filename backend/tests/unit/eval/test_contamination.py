"""No default prompt (system or judge) shares wording or a proper name with the evaluation set."""

from eval import contamination as C


def test_default_prompts_share_no_wording_or_names_with_any_split() -> None:
    files = [C.PROMPTS_DIR / f"{pid}@{v}.md" for pid, v in C.default_prompts()]
    assert C.findings(files) == []


def test_a_shared_five_gram_is_found_and_generic_phrasing_is_not() -> None:
    idx = {g: {("dev", "item-1")} for g in C.grams("the supplier must repair the pump within five days")}
    assert C.matches("Example: the supplier must repair the pump today.", idx) == {
        ("dev", "item-1"): ["supplier must repair the pump", "the supplier must repair the"]
    }
    assert (
        C.matches(
            "The documents provided do not state it.", {"the documents provided do not": {("dev", "x")}}
        )
        == {}
    )


def test_a_shared_party_name_is_found_and_generic_names_are_not() -> None:
    idx = {"Brightmoor": {("test", "item-2")}}
    assert C.name_matches("In the Brightmoor lease, rent is due in March.", idx) == {
        ("test", "item-2"): ["Brightmoor"]
    }
    assert C.names("Agreement. The Customer pays in March.") == {"Customer", "March"} and not (
        C.names("The Customer pays in March.") - C.GENERIC_NAMES
    )
