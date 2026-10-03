"""
Resume operations that the agent's tools delegate to.

Intent classification used to live here: one LLM call picked a single intent and
a handler map ran it. That is the agent loop's job now (`agent_loop.py`), which
lets the model call several tools and read their results. What remains is the
domain logic the tools in `agent_tools.py` wrap, and language detection, used to
localize confirmations.
"""

import json
import logging

from django.conf import settings
from django.urls import reverse

from resume import resume_templates
from resume.models import Resume, ResumeRevision
from resume.services import resume_content, revision_service
from resume.openai_engine import send_openai_message

logger = logging.getLogger(__name__)

class AgentService:
    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    # Words that reliably mark a Turkish message in this product's vocabulary.
    # Turkish-specific characters are the strongest signal, but plenty of real
    # requests ("yeteneklerime AWS ekle") contain none, so the common verbs and
    # nouns users actually type here are listed too.
    _TR_CHARS = set("çğıöşüÇĞİÖŞÜ")
    _TR_WORDS = {
        # function words
        "ve", "ile", "bir", "bu", "de", "da", "mi", "mu", "ne", "icin", "için",
        "olan", "var", "yok", "evet", "hayir", "hayır", "tamam", "merhaba",
        "selam", "nasil", "nasıl", "lutfen", "lütfen", "son", "kac", "kaç",
        "tane", "hangi", "bana", "benim", "sadece", "daha", "gibi", "sonra",
        # things users ask for
        "ekle", "ekler", "sil", "kaldir", "kaldır", "degistir", "değiştir",
        "guncelle", "güncelle", "goster", "göster", "listele", "indir",
        "cevir", "çevir", "olustur", "oluştur", "duzenle", "düzenle",
        "yaz", "yap", "ac", "aç", "geri", "al", "analiz", "et", "karsilastir",
        "karşılaştır", "kopyala", "onizle", "önizle", "degistirme",
        # domain nouns
        "ozgecmis", "özgeçmiş", "deneyim", "deneyimi", "deneyimlerim",
        "yetenek", "yetenekler", "yeteneklerime", "yeteneklerim", "egitim",
        "eğitim", "proje", "projeler", "sablon", "şablon", "dil", "limit",
        "limitlerim", "kota",
    }

    def _detect_language(self, message: str, history=None) -> str:
        """
        Detect the conversation language.

        Recent history is consulted as well: people rarely switch language
        mid-conversation, so one terse message ("AWS ekle") should not flip a
        Turkish conversation into English.
        """
        if self._looks_turkish(message):
            return "tr"
        for turn in reversed(list(history or [])[-6:]):
            if turn.get("role") != "user":
                continue
            if self._looks_turkish(turn.get("content") or ""):
                return "tr"
        return "en"

    def _looks_turkish(self, text: str) -> bool:
        if any(c in self._TR_CHARS for c in text):
            return True
        words = {w.strip(".,!?;:'\"") for w in text.lower().split()}
        return bool(words & self._TR_WORDS)

    # ------------------------------------------------------------------
    # Private: LLM classification
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Private: intent executors
    # ------------------------------------------------------------------

    def _exec_list_resumes(self, user, lang: str) -> dict:
        resume_objs = list(Resume.objects.filter(user=user).order_by("-updated_at"))
        data = [
            {
                "id": r.id,
                "display_name": r.display_name,
                "owner_name": r.owner_name,
                "updated_at": r.updated_at.strftime("%Y-%m-%d"),
                "template_selector": r.template_selector,
            }
            for r in resume_objs
        ]
        if not data:
            msg = {
                "en": "You don't have any resumes yet. Say 'create new resume' to get started!",
                "tr": "Henüz hic resume'unuz yok. Baslamak icin 'yeni resume olustur' diyebilirsiniz!",
            }.get(lang, "No resumes yet.")
        else:
            count = len(data)
            msg = (
                f"{count} resume'unuz var:"
                if lang == "tr"
                else f"You have {count} resume{'s' if count != 1 else ''}:"
            )
        quick_replies = (
            ["Preview first resume", "Score against a job posting", "Create new"]
            if lang == "en"
            else ["İlk resume'u önizle", "İlana göre puanla", "Yeni oluştur"]
        )
        return {
            "type": "chat",
            "message": msg,
            "data": data,
            "data_type": "resume_list",
            "quick_replies": quick_replies if data else None,
        }

    def _exec_preview_resume(
        self, user, params: dict, lang: str, active_resume=None
    ) -> dict:
        resume = self._resolve_resume(user, params) or active_resume
        if not resume:
            return self._resume_not_found(lang, params)
        msg = {
            "en": f"Showing preview of **{resume.display_name}**.",
            "tr": f"**{resume.display_name}** onizlemesi sagda gosteriliyor.",
        }.get(lang, f"Previewing resume {resume.id}.")
        quick_replies = (
            ["Score against a job posting", "Download PDF", "Edit in form"]
            if lang == "en"
            else ["İlana göre puanla", "PDF indir", "Formda düzenle"]
        )
        return {
            "type": "preview",
            "resume_id": resume.id,
            "resume_name": resume.display_name,
            "message": msg,
            "quick_replies": quick_replies,
        }

    def _exec_download_resume(self, user, params: dict, lang: str) -> dict:
        resume = self._resolve_resume(user, params)
        if not resume:
            return self._resume_not_found(lang, params)
        url = reverse("resume:download_resume_pdf", args=[resume.id])
        safe_name = (resume.owner_name or "resume").replace(" ", "_")
        filename = f"{safe_name}_{resume.pk}.pdf"
        msg = {
            "en": f"Downloading **{resume.display_name}** as PDF...",
            "tr": f"**{resume.display_name}** PDF olarak indiriliyor...",
        }.get(lang, f"Downloading resume {resume.id}.")
        return {
            "type": "download",
            "download_url": url,
            "filename": filename,
            "message": msg,
        }

    def _exec_upload_resume(self, lang: str) -> dict:
        msg = {
            "en": "Choose the PDF you'd like me to import.",
            "tr": "Iceri aktarmami istediginiz PDF'i secin.",
        }.get(lang, "Choose a PDF.")
        return {
            "type": "request_upload",
            "source": "pdf",
            "upload_url": reverse("resume:upload_cv"),
            "message": msg,
        }

    def _exec_check_quota(self, user, lang: str) -> dict:
        usage = user.profile.usage()
        if usage["is_pro"]:
            msg = {
                "en": "You are on **Pro** — no limits.",
                "tr": "**Pro** kullanıyorsunuz — sınır yok.",
            }.get(lang, "Pro — no limits.")
            return {"type": "chat", "message": msg, "data": {"tier": "pro"}}

        a = usage["allowances"]
        labels = {
            "en": [("ai_credits", "AI credits this month"), ("downloads", "PDF downloads this month"),
                   ("resumes", "Resumes"), ("job_copies", "Job copies")],
            "tr": [("ai_credits", "Bu ayki AI kredisi"), ("downloads", "Bu ayki PDF indirme"),
                   ("resumes", "CV"), ("job_copies", "İlan kopyası")],
        }.get(lang) or []
        lines = [f"- {label}: {a[key]['used']}/{a[key]['limit']}" for key, label in labels]
        head = "**Ücretsiz plan:**" if lang == "tr" else "**Free plan:**"
        foot = (
            "AI kredisi içe aktarma, iyileştirme ve sohbet mesajlarında kullanılır; aylık kotalar ay başında yenilenir."
            if lang == "tr"
            else "AI credits cover imports, enhancements and chat messages; monthly allowances renew on the 1st."
        )
        data = {"tier": "free"}
        for key, _ in labels:
            data[f"{key}_remaining"] = a[key]["left"]
            data[f"{key}_limit"] = a[key]["limit"]
        msg = head + "\n" + "\n".join(lines) + "\n\n" + foot
        return {"type": "chat", "message": msg, "data": data, "data_type": "quota"}

    def _exec_duplicate_resume(self, user, params: dict, lang: str) -> dict:
        resume = self._resolve_resume(user, params)
        if not resume:
            return self._resume_not_found(lang, params)
        profile = user.profile
        if not profile.can_create_resume():
            limit = settings.FREE_TIER_LIMITS["resume_count"]
            msg = {
                "en": f"Resume limit reached ({limit}). Upgrade to Pro for unlimited.",
                "tr": f"Resume limiti doldu ({limit}). Sinirsiz icin Pro'ya gecin.",
            }.get(lang, "Resume limit reached.")
            return {"type": "chat", "message": msg}
        new_resume = Resume.objects.create(
            user=user, title=f"{resume.title} (Copy)", content=resume.content.copy()
        )
        url = reverse("resume:resume_form_edit", args=[new_resume.pk])
        msg = {
            "en": f"**{resume.display_name}** duplicated! Opening copy in editor...",
            "tr": f"**{resume.display_name}** kopyalandi! Kopya editorde aciliyor...",
        }.get(lang, "Duplicated.")
        return {"type": "redirect", "url": url, "message": msg}

    def _exec_edit_resume(self, user, params: dict, lang: str) -> dict:
        resume = self._resolve_resume(user, params)
        if not resume:
            return self._resume_not_found(lang, params)
        url = reverse("resume:resume_form_edit", args=[resume.id])
        msg = {
            "en": f"Opening **{resume.display_name}** in the editor...",
            "tr": f"**{resume.display_name}** editorde aciliyor...",
        }.get(lang, f"Opening editor for resume {resume.id}.")
        return {"type": "redirect", "url": url, "message": msg}

    def _exec_modify_resume(
        self, user, params: dict, lang: str, user_message: str, active_resume=None
    ) -> dict:
        """
        Apply natural-language modifications to a resume using LLM.
        Target resume priority: params['resume_id'] > active_resume > most recent.
        """
        # Resolve target resume
        resume = self._resolve_resume(user, params)
        if not resume and active_resume:
            resume = active_resume
        if not resume:
            resume = Resume.objects.filter(user=user).order_by("-updated_at").first()
        if not resume:
            msg = {
                "en": "You don't have any resumes yet.",
                "tr": "Henuz hic resume'unuz yok.",
            }.get(lang, "No resumes found.")
            return {"type": "chat", "message": msg}

        resume_json = json.dumps(resume.content, ensure_ascii=False, indent=2)
        # An empty resume shows no field names, and keys the model invents
        # ("name" for full_name) are dropped on save — so say what they are.
        schema_json = json.dumps(resume_content.CONTENT_SCHEMA, ensure_ascii=False)
        experiences = resume.content.get("experience", [])
        last_exp_note = ""
        if experiences:
            last_exp = experiences[0]
            last_exp_note = (
                f"NOTE: 'Son deneyim' / 'last experience' = first item in experience array: "
                f"'{last_exp.get('title')}' at '{last_exp.get('company')}'."
            )

        system_prompt = f"""You are modifying a JSON resume. Apply ALL changes the user requests in one pass.

CURRENT RESUME:
{resume_json}

{last_exp_note}

THE SHAPE (use exactly these keys; anything else is dropped when saved):
{schema_json}

RULES:
- Return the COMPLETE modified resume JSON (all fields, even unchanged ones).
- 'son deneyim' / 'last experience' = the FIRST item in the experience array (most recent).
- experience[].description must ALWAYS be an array of strings (one bullet per element).
- Dates must be in YYYY-MM format (e.g. 2024-03). null means present/current.
- When optimizing for a role (e.g. 'DevOps Engineer'), rewrite ALL experience descriptions
  with strong action verbs and keywords relevant to that role. Keep factual content accurate.
- When adding new experience/education/project, APPEND to the appropriate array.
- When asked to improve wording or make it ATS-friendly: start each bullet with
  a strong past-tense action verb, name the tools, systems and scope already
  stated, use the standard terms recruiters search for (job titles, skills),
  one idea per bullet, 1–2 lines. You may restructure and expand what is
  stated (how, with what, for whom) — that is the improvement the user wants.
- Write only what the user said or what is already in the resume. Never invent
  achievements, results, metrics, tools or responsibilities. A short, plain bullet
  in the user's own terms is better than an impressive one they did not claim.
- Detect user language from their message and reply in that language.

Respond ONLY with valid JSON:
{{
  "modified_resume": {{ ...complete resume JSON... }},
  "changes_summary": "Brief list of what changed",
  "response_message": "Friendly confirmation to show the user"
}}"""

        result = send_openai_message(
            user_message=user_message,
            meta_prompt=system_prompt,
            is_json=True,
            temperature=0.3,
            max_tokens=4000,
        )

        validated = self._validate_modify_result(result)
        if validated:
            revision_service.snapshot(
                resume,
                source=ResumeRevision.SOURCE_AGENT,
                tool_name="modify_resume",
                summary=validated.get("changes_summary", ""),
            )
            # Normalise before storing: the model returns skills as a string
            # often enough, and content the form editor cannot re-open is worse
            # than a rejected edit.
            resume.content = resume_content.normalize(validated["modified_resume"])
            resume.save(update_fields=["content", "updated_at"])
            quick_replies = (
                ["Preview changes", "More changes", "Download PDF"]
                if lang == "en"
                else ["Değişiklikleri önizle", "Daha fazla değişiklik", "PDF indir"]
            )
            return {
                "type": "modify_resume",
                "resume_id": resume.id,
                "resume_name": resume.display_name,
                "message": validated.get("response_message", "Changes applied."),
                "changes_summary": validated.get("changes_summary", ""),
                "quick_replies": quick_replies,
            }

        # Retry once with simpler prompt
        logger.info("modify_resume: first attempt failed, retrying with simpler prompt")
        retry_prompt = f"""Modify this resume JSON as instructed. Return ONLY valid JSON with key "modified_resume" containing the full resume.
Shape: {schema_json}
Current resume: {resume_json}
User request: {user_message}"""
        retry_result = send_openai_message(
            user_message=retry_prompt,
            meta_prompt="Return valid JSON with modified_resume key.",
            is_json=True,
            temperature=0.2,
            max_tokens=4000,
        )
        validated = self._validate_modify_result(retry_result)
        if validated:
            revision_service.snapshot(
                resume,
                source=ResumeRevision.SOURCE_AGENT,
                tool_name="modify_resume",
                summary=validated.get("changes_summary", ""),
            )
            # Normalise before storing: the model returns skills as a string
            # often enough, and content the form editor cannot re-open is worse
            # than a rejected edit.
            resume.content = resume_content.normalize(validated["modified_resume"])
            resume.save(update_fields=["content", "updated_at"])
            quick_replies = (
                ["Preview changes", "More changes", "Download PDF"]
                if lang == "en"
                else ["Değişiklikleri önizle", "Daha fazla değişiklik", "PDF indir"]
            )
            return {
                "type": "modify_resume",
                "resume_id": resume.id,
                "resume_name": resume.display_name,
                "message": validated.get("response_message", "Changes applied."),
                "changes_summary": validated.get("changes_summary", ""),
                "quick_replies": quick_replies,
            }

        msg = {
            "en": "Sorry, I couldn't apply those changes. Please try rephrasing.",
            "tr": "Uzgunum, degisiklikleri uygulayamadim. Lutfen farkli sekilde ifade etmeyi deneyin.",
        }.get(lang, "Could not apply changes.")
        return {"type": "chat", "message": msg}

    # Available templates map (key → display name)
    # Spellings a user types that are not the key itself. Every key in the
    # catalogue resolves to itself; this only covers the shorthands.
    TEMPLATE_ALIASES = {
        "faang": "faangpath-simple",
        "faangpath": "faangpath-simple",
        "simple": "faangpath-simple",
        "klasik": "faangpath-simple",
        "classic": "faangpath-simple",
        "modern": "modern-sidebar",
        "sidebar": "modern-sidebar",
        "kenar": "modern-sidebar",
        "compact": "compact-ats",
        "ats": "compact-ats",
        "engineering": "engineering-classic",
        "muhendis": "engineering-classic",
        "ivy": "ivy-serif",
        "harvard": "ivy-serif",
        "executive": "executive-serif",
        "yonetici": "executive-serif",
        "timeline": "timeline-rail",
        "zaman": "timeline-rail",
        "mono": "dev-mono",
        "developer": "dev-mono",
        "banner": "accent-banner",
        "split": "split-column",
        "gutter": "label-gutter",
        "label": "label-gutter",
        "grid": "header-grid",
        "izgara": "header-grid",
        "editorial": "centered-editorial",
        "centered": "centered-editorial",
        "ortali": "centered-editorial",
        "rail": "right-rail",
        "sag": "right-rail",
        "right": "right-rail",
    }

    def _template_key(self, requested: str):
        """Resolve what the user typed to a catalogue key, or None."""
        requested = (requested or "").lower().strip()
        if not requested:
            return None
        if requested in resume_templates.BY_KEY:
            return requested
        if requested in self.TEMPLATE_ALIASES:
            return self.TEMPLATE_ALIASES[requested]
        # "Ivy Serif", "dev mono" — match on the display name too.
        for design in resume_templates.catalog():
            if requested == design.name.lower():
                return design.key
        return None

    def _exec_switch_template(
        self, user, params: dict, lang: str, active_resume=None
    ) -> dict:
        """Switch the template of a resume and refresh the preview."""
        resume = self._resolve_resume(user, params)
        if not resume and active_resume:
            resume = active_resume
        if not resume:
            resume = Resume.objects.filter(user=user).order_by("-updated_at").first()
        if not resume:
            return self._resume_not_found(lang, params)

        # Resolve template key from params
        template_key = self._template_key(params.get("template"))

        if not template_key:
            msg = {
                "tr": "Hangi şablonu kullanmak istersiniz?",
                "en": "Which template would you like?",
            }.get(lang, "Pick a template:")
            return {
                "type": "template_picker",
                "resume_id": resume.id if resume else None,
                "message": msg,
                "templates": [
                    {
                        "key": design.key,
                        "name": design.name,
                        "description": design.description,
                    }
                    for design in resume_templates.catalog()
                ],
            }

        revision_service.snapshot(
            resume,
            source=ResumeRevision.SOURCE_AGENT,
            tool_name="switch_template",
            summary=f"Before switching to {template_key}",
        )
        resume.template_selector = template_key
        resume.save(update_fields=["template_selector", "updated_at"])

        display = resume_templates.get(template_key).name

        msg = {
            "tr": f"Sablon **{display}** olarak degistirildi. Onizleme guncelleniyor...",
            "en": f"Template switched to **{display}**. Refreshing preview...",
        }.get(lang, f"Template changed to {display}.")

        return {
            "type": "switch_template",
            "resume_id": resume.id,
            "resume_name": resume.display_name,
            "template": template_key,
            "message": msg,
        }

    def _exec_translate_resume(self, user, params: dict, lang: str, active_resume=None) -> dict:
        """Translate resume content to a target language using LLM."""
        resume = self._resolve_resume(user, params) or active_resume
        if not resume:
            resume = Resume.objects.filter(user=user).order_by("-updated_at").first()
        if not resume:
            return self._resume_not_found(lang, params)

        target_raw = (params.get("target_language") or "").lower().strip()
        target_map = {
            "turkish": "Turkish", "türkçe": "Turkish", "turkce": "Turkish", "tr": "Turkish",
            "english": "English", "ingilizce": "English", "en": "English",
        }
        target_language = target_map.get(target_raw, "")

        if not target_language:
            msg = {
                "tr": "Hangi dile çevirmek istersiniz? **Türkçe** veya **İngilizce** diyebilirsiniz.",
                "en": "Which language would you like to translate to? Say **Turkish** or **English**.",
            }.get(lang, "Specify target language: Turkish or English.")
            return {"type": "chat", "message": msg}

        content = resume.content or {}
        content_json = json.dumps(content, ensure_ascii=False, indent=2)

        prompt = (
            f"Translate all user-visible text in this resume JSON to {target_language}.\n"
            "Translate: full_name, job titles, company names, descriptions, school names, "
            "degrees, field_of_study, project names and descriptions, skills.\n"
            "Keep emails, URLs, phone numbers, dates, numeric values, and all JSON keys unchanged.\n"
            "Return ONLY the complete translated JSON, no extra text.\n\n"
            f"Resume JSON:\n{content_json}"
        )
        meta = f"You are a professional resume translator. Translate all text content to {target_language}. Return valid JSON only."

        result_str = send_openai_message(prompt, meta, is_json=True, temperature=0.2, max_tokens=4000)

        try:
            translated = json.loads(result_str)
            if not isinstance(translated, dict) or "user_info" not in translated:
                raise ValueError("Invalid structure")
            revision_service.snapshot(
                resume,
                source=ResumeRevision.SOURCE_AGENT,
                tool_name="translate_resume",
                summary=f"Before translating to {target_language}",
            )
            resume.content = resume_content.normalize(translated)
            resume.language = Resume.normalize_language(
                target_language, resume.language
            )
            resume.save(update_fields=["content", "language", "updated_at"])
            msg = {
                "tr": f"**{resume.display_name}** {target_language} diline çevrildi.",
                "en": f"**{resume.display_name}** translated to {target_language}.",
            }.get(lang, f"Translated to {target_language}.")
            return {
                "type": "modify_resume",
                "resume_id": resume.id,
                "resume_name": resume.display_name,
                "message": msg,
                "changes_summary": f"Translated to {target_language}",
                "quick_replies": (
                    ["Preview", "Download PDF", "Edit in editor"]
                    if lang == "en"
                    else ["Önizle", "PDF İndir", "Editörde düzenle"]
                ),
            }
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("translate_resume parse error: %s", e)
            msg = {
                "tr": "Çeviri tamamlanamadı. Lütfen tekrar deneyin.",
                "en": "Translation couldn't be completed. Please try again.",
            }.get(lang, "Translation failed.")
            return {"type": "chat", "message": msg}

    def _validate_modify_result(self, result: str):
        """Validate and normalize LLM modify result. Returns parsed dict or None."""
        try:
            parsed = json.loads(result)
            modified = parsed.get("modified_resume")
            if not modified or not isinstance(modified, dict):
                logger.warning("modify_resume: missing or invalid modified_resume key")
                return None
            if "user_info" not in modified:
                logger.warning("modify_resume: missing user_info in modified resume")
                return None
            # Normalize experience descriptions: string → list
            for exp in modified.get("experience", []):
                desc = exp.get("description")
                if isinstance(desc, str):
                    exp["description"] = [
                        d.strip() for d in desc.split("\n") if d.strip()
                    ]
            return parsed
        except (ValueError, TypeError) as e:
            # Length only: the result is the user's rewritten resume.
            logger.warning(
                "modify_resume parse error: %s (%d chars)",
                type(e).__name__,
                len(result or ""),
            )
            return None

    def _resolve_resume(self, user, params: dict):
        resume_id = params.get("resume_id")
        if not resume_id:
            return None
        try:
            return Resume.objects.get(pk=resume_id, user=user)
        except Resume.DoesNotExist:
            return None

    def _resume_not_found(self, lang: str, params: dict) -> dict:
        resume_id = params.get("resume_id")
        if resume_id:
            msg = {
                "en": f"I couldn't find resume ID {resume_id}. Type 'list resumes' to see your resumes.",
                "tr": f"ID {resume_id} ile resume bulunamadi. 'listele' yazarak resume'larinizi gorebilirsiniz.",
            }.get(lang, f"Resume {resume_id} not found.")
        else:
            msg = {
                "en": "Which resume do you mean? Type 'list resumes' to see them.",
                "tr": "Hangi resume'yi kastediyorsunuz? 'listele' yazarak gorebilirsiniz.",
            }.get(lang, "Which resume?")
        return {"type": "chat", "message": msg}



agent_service = AgentService()
