"""Vision UI stays default; Apple and Spatial are opt-in connections fields."""
from tests.test_mediamanager_approve import _import_server


def test_connections_public_defaults_vision_theme():
    server = _import_server()
    public = server.connections_public({})
    assert public.ui_theme == "vision"


def test_connections_public_keeps_apple_theme():
    server = _import_server()
    public = server.connections_public({"ui_theme": "apple"})
    assert public.ui_theme == "apple"


def test_connections_public_keeps_spatial_theme():
    server = _import_server()
    public = server.connections_public({"ui_theme": "spatial"})
    assert public.ui_theme == "spatial"


def test_unknown_theme_falls_back_to_vision():
    server = _import_server()
    public = server.connections_public({"ui_theme": "neon"})
    assert public.ui_theme == "vision"


def test_normalize_ui_theme():
    server = _import_server()
    assert server.normalize_ui_theme("apple") == "apple"
    assert server.normalize_ui_theme("APPLE") == "apple"
    assert server.normalize_ui_theme("spatial") == "spatial"
    assert server.normalize_ui_theme(" Spatial ") == "spatial"
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


def test_sidebar_icon_size_defaults_to_the_original_rail():
    server = _import_server()
    public = server.connections_public({})
    assert public.sidebar_icon_size == 40


def test_normalize_sidebar_icon_size_clamps_to_the_allowed_range():
    server = _import_server()
    assert server.normalize_sidebar_icon_size(98) == 98
    assert server.normalize_sidebar_icon_size(140) == 98
    assert server.normalize_sidebar_icon_size(4) == 28
    assert server.normalize_sidebar_icon_size("64") == 64
    assert server.normalize_sidebar_icon_size(None) == 40


def test_connections_public_keeps_a_saved_icon_size():
    server = _import_server()
    public = server.connections_public({"sidebar_icon_size": 72})
    assert public.sidebar_icon_size == 72
