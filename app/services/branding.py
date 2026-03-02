import colorsys
import os
import tempfile

DEFAULT_PRIMARY = "#0c3e6b"
DEFAULT_ACCENT = "#f56708"

# Lightness stops for each scale key (0–1 range)
_LIGHTNESS = {
    50: 0.97,
    100: 0.93,
    200: 0.85,
    300: 0.74,
    400: 0.62,
    500: 0.50,
    600: 0.39,
    700: 0.30,
    800: 0.22,
    900: 0.15,
    950: 0.10,
}

# Saturation scale factor per stop (tapers at extremes)
_SAT_SCALE = {
    50: 0.30,
    100: 0.50,
    200: 0.70,
    300: 0.85,
    400: 1.00,
    500: 1.00,
    600: 1.00,
    700: 0.95,
    800: 0.90,
    900: 0.85,
    950: 0.80,
}


def _hex_to_hls(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255
    return colorsys.rgb_to_hls(r, g, b)


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, round(r * 255))),
        max(0, min(255, round(g * 255))),
        max(0, min(255, round(b * 255))),
    )


def generate_tonal_scale(hex_color: str) -> dict:
    """Return {50: '#...', 100: '#...', ..., 950: '#...'} from a single hex color."""
    try:
        hue, _lightness, saturation = _hex_to_hls(hex_color)
        result = {}
        for key, l in _LIGHTNESS.items():
            s = saturation * _SAT_SCALE[key]
            r, g, b = colorsys.hls_to_rgb(hue, l, s)
            result[key] = _rgb_to_hex(r, g, b)
        return result
    except Exception:
        return _default_scale(hex_color)


def _default_scale(hex_color: str) -> dict:
    """Return a flat scale using the provided color for all stops (safe fallback)."""
    return {k: hex_color for k in _LIGHTNESS}


def extract_colors_from_image(image_bytes: bytes) -> tuple[str, str]:
    """Return (primary_hex, accent_hex) extracted from image bytes."""
    try:
        from colorthief import ColorThief

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".png")
        try:
            os.write(tmp_fd, image_bytes)
            os.close(tmp_fd)
            ct = ColorThief(tmp_path)
            palette = ct.get_palette(color_count=2, quality=1)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        def rgb_to_hex(rgb):
            return "#{:02x}{:02x}{:02x}".format(*rgb)

        if len(palette) >= 2:
            return rgb_to_hex(palette[0]), rgb_to_hex(palette[1])
        elif len(palette) == 1:
            primary = rgb_to_hex(palette[0])
            # Derive accent by shifting hue +30°
            r, g, b = [c / 255 for c in palette[0]]
            hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
            hue2 = (hue + 30 / 360) % 1.0
            r2, g2, b2 = colorsys.hls_to_rgb(hue2, lightness, saturation)
            accent = _rgb_to_hex(r2, g2, b2)
            return primary, accent
        else:
            return DEFAULT_PRIMARY, DEFAULT_ACCENT
    except Exception:
        return DEFAULT_PRIMARY, DEFAULT_ACCENT


def build_palette(primary_hex: str, accent_hex: str) -> dict:
    """Return {'navy': tonal_scale, 'accent': tonal_scale} for Tailwind injection."""
    return {
        "navy": generate_tonal_scale(primary_hex),
        "accent": generate_tonal_scale(accent_hex),
    }
