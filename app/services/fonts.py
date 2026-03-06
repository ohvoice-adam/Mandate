DEFAULT_HEADLINE_FONT = "Besley"
DEFAULT_BODY_FONT = "Figtree"

# Each entry: (display_name, google_fonts_spec, fallback_stack)
# google_fonts_spec is the `family=` value for the Google Fonts CSS API v2.
# fallback_stack is appended after the quoted font name in CSS font-family.

HEADLINE_FONTS = [
    ("Besley",             "Besley:ital,wght@0,400;0,500;0,600;0,700;1,400",         "Georgia, serif"),
    ("Bitter",             "Bitter:ital,wght@0,400;0,500;0,600;0,700;1,400",          "Georgia, serif"),
    ("Cormorant Garamond", "Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400",    "Georgia, serif"),
    ("DM Serif Display",   "DM+Serif+Display:ital@0;1",                               "Georgia, serif"),
    ("Fraunces",           "Fraunces:ital,wght@0,400;0,500;0,600;0,700;1,400",        "Georgia, serif"),
    ("Libre Baskerville",  "Libre+Baskerville:ital,wght@0,400;0,700;1,400",           "Georgia, serif"),
    ("Lora",               "Lora:ital,wght@0,400;0,500;0,600;0,700;1,400",            "Georgia, serif"),
    ("Merriweather",       "Merriweather:ital,wght@0,300;0,400;0,700;1,300;1,400",    "Georgia, serif"),
    ("Outfit",             "Outfit:wght@400;500;600;700",                              "ui-sans-serif, system-ui, sans-serif"),
    ("Playfair Display",   "Playfair+Display:ital,wght@0,400;0,500;0,600;0,700;1,400", "Georgia, serif"),
    ("Plus Jakarta Sans",  "Plus+Jakarta+Sans:wght@400;500;600;700",                  "ui-sans-serif, system-ui, sans-serif"),
    ("Raleway",            "Raleway:ital,wght@0,400;0,500;0,600;0,700;1,400",         "ui-sans-serif, system-ui, sans-serif"),
    ("Sora",               "Sora:wght@400;500;600;700",                               "ui-sans-serif, system-ui, sans-serif"),
    ("Space Grotesk",      "Space+Grotesk:wght@400;500;600;700",                      "ui-sans-serif, system-ui, sans-serif"),
    ("Young Serif",        "Young+Serif",                                             "Georgia, serif"),
]

BODY_FONTS = [
    ("Figtree",        "Figtree:wght@300;400;500;600;700",          "ui-sans-serif, system-ui, sans-serif"),
    ("DM Sans",        "DM+Sans:wght@300;400;500;600;700",          "ui-sans-serif, system-ui, sans-serif"),
    ("IBM Plex Sans",  "IBM+Plex+Sans:wght@300;400;500;600;700",    "ui-sans-serif, system-ui, sans-serif"),
    ("Inter",          "Inter:wght@300;400;500;600;700",            "ui-sans-serif, system-ui, sans-serif"),
    ("Lato",           "Lato:wght@300;400;700",                     "ui-sans-serif, system-ui, sans-serif"),
    ("Manrope",        "Manrope:wght@300;400;500;600;700",          "ui-sans-serif, system-ui, sans-serif"),
    ("Mulish",         "Mulish:wght@300;400;500;600;700",           "ui-sans-serif, system-ui, sans-serif"),
    ("Nunito",         "Nunito:wght@300;400;500;600;700",           "ui-sans-serif, system-ui, sans-serif"),
    ("Open Sans",      "Open+Sans:wght@300;400;500;600;700",        "ui-sans-serif, system-ui, sans-serif"),
    ("Outfit",         "Outfit:wght@300;400;500;600;700",           "ui-sans-serif, system-ui, sans-serif"),
    ("Plus Jakarta Sans", "Plus+Jakarta+Sans:wght@300;400;500;600;700", "ui-sans-serif, system-ui, sans-serif"),
    ("Poppins",        "Poppins:wght@300;400;500;600;700",          "ui-sans-serif, system-ui, sans-serif"),
    ("Rubik",          "Rubik:wght@300;400;500;600;700",            "ui-sans-serif, system-ui, sans-serif"),
    ("Source Sans 3",  "Source+Sans+3:wght@300;400;500;600;700",    "ui-sans-serif, system-ui, sans-serif"),
    ("Work Sans",      "Work+Sans:wght@300;400;500;600;700",        "ui-sans-serif, system-ui, sans-serif"),
]

_HEADLINE_MAP = {name: (spec, fallback) for name, spec, fallback in HEADLINE_FONTS}
_BODY_MAP = {name: (spec, fallback) for name, spec, fallback in BODY_FONTS}


def build_font_url(headline_font: str, body_font: str) -> str:
    """Return a Google Fonts CSS2 URL loading both fonts."""
    h_spec = _HEADLINE_MAP.get(headline_font, _HEADLINE_MAP[DEFAULT_HEADLINE_FONT])[0]
    b_spec = _BODY_MAP.get(body_font, _BODY_MAP[DEFAULT_BODY_FONT])[0]
    if h_spec == b_spec:
        params = f"family={h_spec}"
    else:
        params = f"family={h_spec}&family={b_spec}"
    return f"https://fonts.googleapis.com/css2?{params}&display=swap"


def get_headline_css_stack(font_name: str) -> str:
    """Return the CSS font-family value for a headline font."""
    _, fallback = _HEADLINE_MAP.get(font_name, _HEADLINE_MAP[DEFAULT_HEADLINE_FONT])
    return f"'{font_name}', {fallback}"


def get_body_css_stack(font_name: str) -> str:
    """Return the CSS font-family value for a body font."""
    _, fallback = _BODY_MAP.get(font_name, _BODY_MAP[DEFAULT_BODY_FONT])
    return f"'{font_name}', {fallback}"
