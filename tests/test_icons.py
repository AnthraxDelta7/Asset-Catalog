from __future__ import annotations

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor

from asset_catalogue.ui.icons import magnifier_icon


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_magnifier_icon_draws_within_its_bounds(qapp) -> None:
    """Regression guard: an earlier version set a devicePixelRatio on the
    pixmap *and* scaled the painter, which double-applied and left only a
    quarter of the circle visible once the button scaled it down.
    """
    icon = magnifier_icon(QColor("#ffffff"), size=32)
    image = icon.pixmap(QSize(32, 32)).toImage()
    assert not image.isNull()

    painted = [
        (x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 0
    ]
    assert painted, "icon rendered nothing"
    # The glyph has to occupy the whole box, not one corner of it: the
    # lens sits top-left and the handle runs to the bottom-right.
    assert min(x for x, _ in painted) < 12
    assert max(x for x, _ in painted) > 20
    assert min(y for _, y in painted) < 12
    assert max(y for _, y in painted) > 20


def test_magnifier_icon_uses_the_colour_it_is_given(qapp) -> None:
    """It's drawn from the palette's text colour so it matches the theme
    rather than being a fixed-colour emoji glyph.
    """
    image = magnifier_icon(QColor("#ff0000"), size=32).pixmap(QSize(32, 32)).toImage()
    reds = [
        image.pixelColor(x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 200
    ]
    assert reds
    assert all(c.red() > 200 and c.green() < 60 and c.blue() < 60 for c in reds)
