import pytest

import icons


def test_icon_comes_from_the_one_bundled_font(qapp):
    assert not icons.icon("fa5s.save", color="#888").isNull()


def test_other_fonts_are_refused(qapp):
    with pytest.raises(ValueError, match="fa5s"):
        icons.icon("mdi6.content-save")


def test_font_file_names_match_the_spec_filter():
    # NetworkEd.spec keeps only qtawesome fonts whose name starts with this prefix; if a
    # qtawesome upgrade renames the files, the exe would ship without its icons.
    for name in icons.font_files():
        assert name.startswith("fontawesome5-solid-webfont"), name
