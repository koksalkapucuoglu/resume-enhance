"""The catalogue of resume designs.

One registry, three consumers: the PDF service (which file renders a key), the
editor's template picker (cards, filters, labels) and the MCP `list_templates`
tool (key + description for a calling model). Before this module those three
lived in three places and drifted; a template added to one was invisible to the
others.

A design is a *layout* plus a set of *tokens*. The layout is structural HTML
(single column, sidebar, banner) and is shared; the tokens are typography,
colour, density and section-header treatment, and are what actually make two
templates look like different resumes. Adding a design is normally a new entry
here, not a new HTML file.

Only fonts installed in the image may be named (see Dockerfile). A missing font
silently falls back to a serif default, which is how a design ends up looking
nothing like its thumbnail.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


# Font stacks. Every family listed is either installed in the image or a
# metric-compatible substitute for it, so PDF and browser preview agree.
SERIF_CLASSIC = '"Times New Roman", Tinos, "Liberation Serif", Times, serif'
SERIF_BOOK = 'Caladea, Cambria, "Liberation Serif", Georgia, serif'
SANS_CLASSIC = 'Arial, Arimo, "Liberation Sans", Helvetica, sans-serif'
SANS_HUMANIST = 'Carlito, Calibri, Arimo, "Liberation Sans", sans-serif'
MONO = 'Cousine, "Liberation Mono", "Courier New", monospace'


@dataclass(frozen=True)
class ResumeTemplate:
    """One selectable design."""

    key: str
    name: str
    # One line, written for a model choosing a template through MCP as much as
    # for a person reading the card.
    description: str
    # Structural HTML under resume/templates/resume_templates/.
    layout: str
    # Grouping for the picker's filter chips.
    family: str
    tokens: Dict[str, str] = field(default_factory=dict)
    # Shown as a badge on the card. "ATS" means single column, no graphics,
    # nothing a parser has to guess at.
    ats_safe: bool = True

    @property
    def template_file(self) -> str:
        return f"resume_templates/layout_{self.layout}.html"

    @property
    def resolved(self) -> Dict[str, str]:
        """Tokens with the base defaults filled in.

        The picker's thumbnails read these, so a card is drawn from the same
        numbers the template renders with.
        """
        return {**BASE_TOKENS, **self.tokens}


# Defaults every template inherits; an entry overrides only what differs.
BASE_TOKENS: Dict[str, str] = {
    "font_body": SERIF_CLASSIC,
    "font_heading": SERIF_CLASSIC,
    "font_size": "10.5pt",
    "line_height": "1.35",
    "accent": "#000000",
    "ink": "#000000",
    "muted": "#444444",
    "rule": "#000000",
    "rule_width": "1px",
    "page_margin": "0.5in",
    "section_gap": "0.55rem",
    "item_gap": "0.5rem",
    "name_size": "20pt",
    "name_case": "uppercase",
    "name_weight": "700",
    "name_spacing": "1.5px",
    "header_align": "center",
    # rule | caps-rule | bar | left-bar | plain
    "section_style": "rule",
    "section_size": "11pt",
    "section_spacing": "0.5px",
    "bullet": "disc",
    # right | rail
    "date_style": "right",
    "link_color": "#0066cc",
    # Full-bleed layouts (banner, sidebar) keep their own inner padding because
    # their @page margin is zero.
    "inner_padding": "0.45in",
    "header_bg": "transparent",
    "header_ink": "inherit",
    # Sidebar layouts only.
    "sidebar_width": "32%",
    "sidebar_bg": "#f1f5f9",
    "sidebar_ink": "#0f172a",
}


TEMPLATES: Tuple[ResumeTemplate, ...] = (
    ResumeTemplate(
        key="faangpath-simple",
        name="FaangPath Simple",
        description="Single column, classic serif. The safest choice for ATS screening.",
        layout="single",
        family="classic",
        tokens={},
    ),
    ResumeTemplate(
        key="compact-ats",
        name="Compact ATS",
        description=(
            "Dense single column in a humanist sans. Fits a long career on one "
            "page without shrinking the type."
        ),
        layout="single",
        family="modern",
        tokens={
            "font_body": SANS_HUMANIST,
            "font_heading": SANS_HUMANIST,
            "font_size": "10pt",
            "line_height": "1.25",
            "page_margin": "0.45in",
            "section_gap": "0.4rem",
            "item_gap": "0.35rem",
            "name_size": "18pt",
            "name_spacing": "0.5px",
            "header_align": "left",
            "section_style": "caps-rule",
            "section_size": "10pt",
            "muted": "#333333",
        },
    ),
    ResumeTemplate(
        key="engineering-classic",
        name="Engineering Classic",
        description=(
            "Book serif with small-caps section headers. Reads like a technical "
            "CV rather than a marketing page."
        ),
        layout="single",
        family="classic",
        tokens={
            "font_body": SERIF_BOOK,
            "font_heading": SERIF_BOOK,
            "font_size": "10.5pt",
            "section_style": "caps-rule",
            "section_spacing": "1.5px",
            "name_spacing": "2px",
            "rule": "#333333",
            "muted": "#3a3a3a",
        },
    ),
    ResumeTemplate(
        key="ivy-serif",
        name="Ivy Serif",
        description=(
            "Centred serif with generous leading and wide margins. The classic "
            "university career-office layout."
        ),
        layout="single",
        family="classic",
        tokens={
            "font_size": "11pt",
            "line_height": "1.45",
            "page_margin": "0.7in",
            "section_gap": "0.7rem",
            "name_size": "22pt",
            "name_spacing": "3px",
            "section_style": "rule",
            "section_spacing": "2px",
        },
    ),
    ResumeTemplate(
        key="executive-serif",
        name="Executive",
        description=(
            "Understated serif with wide letter-spacing and airy sections. "
            "Suits senior and leadership profiles."
        ),
        layout="single",
        family="classic",
        tokens={
            "font_body": SERIF_BOOK,
            "font_heading": SANS_CLASSIC,
            "font_size": "10.5pt",
            "line_height": "1.4",
            "page_margin": "0.65in",
            "section_gap": "0.75rem",
            "name_size": "21pt",
            "name_spacing": "4px",
            "section_style": "plain",
            "section_spacing": "3px",
            "section_size": "9.5pt",
            "muted": "#555555",
            "rule": "#999999",
        },
    ),
    ResumeTemplate(
        key="timeline-rail",
        name="Timeline Rail",
        description=(
            "Single column with the dates in a left rail, so the career "
            "chronology reads down one edge."
        ),
        layout="single",
        family="modern",
        tokens={
            "font_body": SANS_CLASSIC,
            "font_heading": SANS_CLASSIC,
            "font_size": "10pt",
            "date_style": "rail",
            "header_align": "left",
            "section_style": "left-bar",
            "accent": "#1d4ed8",
            "rule": "#cbd5e1",
            "name_size": "19pt",
            "name_spacing": "0.5px",
        },
    ),
    ResumeTemplate(
        key="dev-mono",
        name="Dev Mono",
        description=(
            "Monospaced headers and dates over a sans body. Reads as a "
            "developer's resume without any graphics."
        ),
        layout="single",
        family="modern",
        tokens={
            "font_body": SANS_HUMANIST,
            "font_heading": MONO,
            "font_size": "10pt",
            "line_height": "1.3",
            "header_align": "left",
            "section_style": "caps-rule",
            "section_size": "9.5pt",
            "section_spacing": "1px",
            "name_size": "17pt",
            "name_spacing": "1px",
            "accent": "#0f766e",
            "rule": "#94a3b8",
            "bullet": "square",
            "link_color": "#0f766e",
        },
    ),
    ResumeTemplate(
        key="accent-banner",
        name="Accent Banner",
        description=(
            "Name and contacts in a coloured banner, single column below. "
            "Still parses cleanly; the colour is decoration, not structure."
        ),
        layout="banner",
        family="bold",
        tokens={
            "font_body": SANS_CLASSIC,
            "font_heading": SANS_CLASSIC,
            "font_size": "10pt",
            # The banner bleeds out of this margin; it is not a zero-margin page.
            "page_margin": "0.45in",
            "accent": "#1e3a8a",
            "section_style": "bar",
            "section_size": "10pt",
            "rule": "#1e3a8a",
            "name_size": "22pt",
            "name_spacing": "2px",
            "link_color": "#1e3a8a",
        },
    ),
    ResumeTemplate(
        key="modern-sidebar",
        name="Modern Sidebar",
        description="Two columns: contact and skills in a left sidebar.",
        layout="sidebar",
        family="two-column",
        ats_safe=False,
        tokens={
            "font_body": SANS_CLASSIC,
            "font_heading": SANS_CLASSIC,
            "font_size": "10pt",
            "page_margin": "0in",
            "header_align": "left",
            "section_style": "caps-rule",
            "section_size": "10pt",
            "accent": "#2563eb",
            "rule": "#cbd5e1",
            "name_size": "20pt",
            "name_spacing": "1px",
            "sidebar_bg": "#f1f5f9",
            "sidebar_ink": "#0f172a",
            "header_bg": "#1e293b",
            "header_ink": "#ffffff",
        },
    ),
    ResumeTemplate(
        key="split-column",
        name="Split Column",
        description=(
            "Two columns with a dark sidebar for contact, skills and education. "
            "Strong visual identity; keep it for design-friendly employers."
        ),
        layout="sidebar",
        family="two-column",
        ats_safe=False,
        tokens={
            "font_body": SANS_HUMANIST,
            "font_heading": SANS_HUMANIST,
            "font_size": "10pt",
            "page_margin": "0in",
            "header_align": "left",
            "section_style": "caps-rule",
            "section_size": "9.5pt",
            "section_spacing": "1.5px",
            "accent": "#0f172a",
            "rule": "#cbd5e1",
            "name_size": "21pt",
            "name_spacing": "1.5px",
            "sidebar_width": "34%",
            "sidebar_bg": "#0f172a",
            "header_bg": "#ffffff",
            "header_ink": "#0f172a",
            "sidebar_ink": "#f8fafc",
            "link_color": "#0f172a",
        },
    ),
)


BY_KEY: Dict[str, ResumeTemplate] = {t.key: t for t in TEMPLATES}

DEFAULT_TEMPLATE_KEY = "faangpath-simple"

# The families the picker offers as filter chips, in display order.
FAMILIES: Tuple[Tuple[str, str], ...] = (
    ("classic", "Classic"),
    ("modern", "Modern"),
    ("bold", "Bold"),
    ("two-column", "Two column"),
)


def get(key: str) -> ResumeTemplate:
    """The template for a key, falling back to the default for unknown keys."""
    return BY_KEY.get(key) or BY_KEY[DEFAULT_TEMPLATE_KEY]


def selector_html_map() -> Dict[str, str]:
    """`settings.TEMPLATE_SELECTOR_HTML_MAP`: key -> layout template path."""
    return {t.key: t.template_file for t in TEMPLATES}


def resolved_tokens(key: str) -> Dict[str, str]:
    """Base tokens with the template's overrides applied."""
    return {**BASE_TOKENS, **get(key).tokens}


def catalog() -> List[ResumeTemplate]:
    """Every template, in picker order."""
    return list(TEMPLATES)


# The words a template prints itself, in the language the resume is WRITTEN in
# (Resume.language) — not the interface language. A Turkish resume downloaded
# by someone browsing in English still needs Turkish headings.
DEFAULT_LANGUAGE = "en"

SECTION_LABELS: Dict[str, Dict[str, str]] = {
    "en": {
        "contact": "CONTACT",
        "focus_areas": "WHAT I'M WORKING ON",
        "education": "EDUCATION",
        "skills": "SKILLS",
        "experience": "EXPERIENCE",
        "projects": "PROJECTS & PUBLICATIONS",
        "present": "Present",
        # Joins degree and field: "Master of Electrical Engineering".
        "degree_joiner": " of ",
    },
    "tr": {
        "contact": "İLETİŞİM",
        "focus_areas": "ŞU AN ÜZERİNDE ÇALIŞTIKLARIM",
        "education": "EĞİTİM",
        "skills": "YETENEKLER",
        "experience": "DENEYİM",
        "projects": "PROJELER VE YAYINLAR",
        "present": "Halen",
        # "Yüksek Lisans of Elektrik Mühendisliği" was the English joiner
        # printed into Turkish resumes.
        "degree_joiner": " – ",
    },
}


def language_code(value) -> str:
    """A language this catalogue has labels for, or the default."""
    return value if value in SECTION_LABELS else DEFAULT_LANGUAGE


def labels(language) -> Dict[str, str]:
    return SECTION_LABELS[language_code(language)]


def rendering_language(language):
    """
    Context manager: render with Django's translations for `language`.

    Month names come from Django's `date` filter, which follows the active
    translation — so "Aug 2022" only becomes "Ağu 2022" if the render happens
    inside this block. Imported lazily because settings imports this module
    before Django is configured.
    """
    from django.utils import translation

    return translation.override(language_code(language))


def design_context(key: str, context: Dict, language: str = DEFAULT_LANGUAGE) -> Dict:
    """Add the design a key selects to a resume's render context.

    Every render path — PDF, editor preview, saved-resume preview, application
    snapshot — goes through here, so a template never has to guess which design
    it is being rendered as, or in which language to print its headings.
    """
    template = get(key)
    code = language_code(language)
    return {
        **context,
        "tpl": template,
        "tokens": resolved_tokens(template.key),
        "template_key": template.key,
        "labels": SECTION_LABELS[code],
        "language": code,
    }
