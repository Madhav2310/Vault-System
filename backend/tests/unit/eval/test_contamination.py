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


def test_a_shared_figure_is_found_and_years_and_small_numbers_are_not() -> None:
    prompt = "Net sales were $5,466.312 million, HR@10 0.657, in 2024, over 12 months, 3 parties."
    assert C.numbers(prompt) == {"5466", "0.657"}  # large figures by integer part: gold writes 5466.00
    assert C.numbers("The answer is $5466.00 million, or 0.6570.") == {"5466", "0.657"}
    assert C.numbers("In 1999 and 2024, 12 of 350 votes, version 1.2.") == set()
    idx = {"5466": {("dev", "item-3")}, "0.657": {("dev", "item-4")}}
    assert C.number_matches(prompt, idx) == {("dev", "item-3"): ["5466"], ("dev", "item-4"): ["0.657"]}
