"""Vision UI stays default; Apple is an opt-in connections field."""
from tests.test_mediamanager_approve import _import_server


def test_connections_public_defaults_vision_theme():
    server = _import_server()
    public = server.connections_public({})
    assert public.ui_theme == "vision"


def test_connections_public_keeps_apple_theme():
    server = _import_server()
    public = server.connections_public({"ui_theme": "apple"})
    assert public.ui_theme == "apple"


def test_unknown_theme_falls_back_to_vision():
    server = _import_server()
    public = server.connections_public({"ui_theme": "neon"})
    assert public.ui_theme == "vision"


def test_normalize_ui_theme():
    server = _import_server()
    assert server.normalize_ui_theme("apple") == "apple"
    assert server.normalize_ui_theme("APPLE") == "apple"
    assert server.normalize_ui_theme(None) == "vision"
    assert server.normalize_ui_theme("vision") == "vision"


def test_connections_public_defaults_glass_intensity():
    server = _import_server()
    public = server.connections_public({})
    assert public.glass_intensity == 78


def test_normalize_glass_intensity():
    server = _import_server()
    assert server.normalize_glass_intensity(0) == 0
    assert server.normalize_glass_intensity(100) == 100
    assert server.normalize_glass_intensity(140) == 100
    assert server.normalize_glass_intensity(-8) == 0
    assert server.normalize_glass_intensity("64") == 64
    assert server.normalize_glass_intensity(None) == 78
