from apps.core.normalization import token_variants


def test_latin_and_persian_names_find_each_other():
    assert "405" in token_variants("persia")
    assert "persia" in token_variants("persia")
