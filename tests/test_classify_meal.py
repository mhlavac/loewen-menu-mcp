"""Tests for the classify_meal heuristic (pure, no I/O)."""

import pytest

from server import classify_meal


@pytest.mark.parametrize(
    "name",
    ["Bratwurst", "Schweinegulasch", "Kassler", "Wurstgulasch"],
)
def test_pork_dishes(name):
    result = classify_meal(name)
    assert "pork" in result["contains"], name
    assert result["vegetarian"] is False, name


@pytest.mark.parametrize(
    "name",
    [
        "Hähnchennuggets",
        "Hühnerfrikassee",
        "Geflügelbällchen",
        "Hähnchenkeule",
        "Würzfleisch vom Huhn",
    ],
)
def test_poultry_dishes(name):
    result = classify_meal(name)
    assert "poultry" in result["contains"], name
    assert result["vegetarian"] is False, name


@pytest.mark.parametrize(
    "name",
    ["Fischstäbchen", "Fischbällchen (mit Lachs)", "Fischragout"],
)
def test_fish_dishes(name):
    result = classify_meal(name)
    assert "fish" in result["contains"], name
    assert result["vegetarian"] is False, name


@pytest.mark.parametrize(
    "name",
    ["Milchreis", "Grießbrei", "Kartoffelpuffer mit Apfelmus", "Vanillepudding"],
)
def test_sweet_vegetarian_dishes(name):
    result = classify_meal(name)
    assert result["sweet"] is True, name
    assert result["vegetarian"] is True, name
    assert result["contains"] == [], name


@pytest.mark.parametrize("name", ["Eierfrikassee", "Eier in Senfsoße"])
def test_egg_dishes_are_vegetarian(name):
    result = classify_meal(name)
    assert "egg" in result["contains"], name
    # egg does not break lacto-ovo vegetarian
    assert result["vegetarian"] is True, name
    # egg is the only animal category — no meat
    assert set(result["contains"]) == {"egg"}, name


@pytest.mark.parametrize(
    "name",
    [
        "Chili sin Carne",
        "veg. Gemüsenuggets",
        "Gemüsepfanne",
        "Linseneintopf",
        "Kichererbsencurry",
    ],
)
def test_vegetarian_no_animal(name):
    result = classify_meal(name)
    assert result["vegetarian"] is True, name
    assert result["contains"] == [], name


def test_case_insensitive():
    assert classify_meal("BRATWURST")["contains"] == ["pork"]
    assert classify_meal("bratwurst")["contains"] == ["pork"]


def test_empty_string_is_vegetarian():
    result = classify_meal("")
    assert result == {"vegetarian": True, "contains": [], "sweet": False}


def test_huehnerfrikassee_is_poultry_not_egg():
    # frikassee implies egg ONLY when no meat keyword fired
    result = classify_meal("Hühnerfrikassee")
    assert "poultry" in result["contains"]
    assert "egg" not in result["contains"]


def test_bare_gulasch_defaults_to_beef():
    result = classify_meal("Gulasch mit Nudeln")
    assert "beef" in result["contains"]
    assert result["vegetarian"] is False


# --- regressions from code review --------------------------------------------

def test_egg_word_boundary_not_eintopf():
    # "...Eintopf" must NOT be tagged egg (regression: the old space-padded
    # " ei" keyword false-matched "Bohnen Eintopf").
    assert "egg" not in classify_meal("Grüne Bohnen Eintopf")["contains"]


def test_egg_standalone_word_caught():
    # "mit Ei" at a word boundary IS egg (old keyword missed it without a space).
    assert "egg" in classify_meal("Bandnudeln mit Ei")["contains"]


def test_egg_compound_caught():
    assert "egg" in classify_meal("Eier in Senfsoße")["contains"]


def test_fish_no_bare_sea_false_positive():
    # The bare "sea" fish keyword was removed; "Seasonal" must not tag fish.
    assert "fish" not in classify_meal("Seasonal Gemüsepfanne")["contains"]
