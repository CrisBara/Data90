from football_analytics.sync.normalizer import normalize_name


def test_name_normalization_is_accent_and_case_insensitive() -> None:
    assert normalize_name("Atlético de Madrid") == "atletico de madrid"
    assert normalize_name("  MAN-CITY FC ") == "man city fc"


def test_name_normalization_does_not_merge_distinct_words() -> None:
    assert normalize_name("United") != normalize_name("City")
