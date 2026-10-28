from instagram_scrapers.instagram import Profile
from instagram_scrapers.report import cap_note, md_table, percent, plural
from tests.factories import full_profile


def test_md_table_escapes_cells() -> None:
    assert md_table(["a", "b"], [["x|y", "line\nbreak"]], align="lr") == [
        "| a | b |",
        "| :--- | ---: |",
        "| x\\|y | line break |",
    ]


def test_percent() -> None:
    assert percent(part=1, whole=3) == "33%"
    assert percent(part=0, whole=0) == "n/a"
    assert percent(part=1, whole=300) == "0.3%"


def test_cap_note_business() -> None:
    brand = Profile.from_api(
        full_profile("1", "brand", followers=268_000_000, account_type=2)
    )
    note = cap_note(profile=brand, listed=1365)
    assert "only 1,365 followers" in note
    assert "some business and verified accounts" in note


def test_cap_note_personal() -> None:
    person = Profile.from_api(full_profile("1", "person", followers=116))
    note = cap_note(profile=person, listed=110)
    assert "110 of 116" in note
    assert "business" not in note


def test_plural() -> None:
    assert plural(count=1, word="winner") == "1 winner"
    assert plural(count=2, word="entry", many="entries") == "2 entries"
    assert plural(count=1_000, word="account") == "1,000 accounts"
