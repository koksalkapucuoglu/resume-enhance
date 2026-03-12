from resume.i18n import TRANSLATIONS


def ui_translations(request):
    """Inject UI translation dict and current ui_language into every template context."""
    lang = "en"
    if request.user.is_authenticated:
        try:
            lang = request.user.profile.ui_language or "en"
        except Exception:
            lang = "en"
    return {
        "UI": TRANSLATIONS.get(lang, TRANSLATIONS["en"]),
        "ui_lang": lang,
    }
