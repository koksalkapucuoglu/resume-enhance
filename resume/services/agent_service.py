"""
Resume operations that the agent's tools delegate to.

Intent classification used to live here: one LLM call picked a single intent and
a handler map ran it. That is the agent loop's job now (`agent_loop.py`), which
lets the model call several tools and read their results. What remains is the
domain logic the tools in `agent_tools.py` wrap, the guided builder — a fixed
question sequence rather than a model decision — and language detection, used to
localize confirmations.
"""

import json
import logging

from django.conf import settings
from django.urls import reverse

from resume.models import Resume, ResumeRevision
from resume.services import revision_service
from resume.openai_engine import send_openai_message

logger = logging.getLogger(__name__)

BUILDER_STEPS = [
    ("ask_name", "full_name", None),
    ("ask_email", "email", None),
    ("ask_phone", "phone", True),
    ("ask_linkedin", "linkedin", True),
    ("ask_github", "github", True),
    ("ask_skills", "skills", True),
    ("ask_title", "job_title", True),
    ("confirm", None, None),
]

BUILDER_PROMPTS = {
    "en": {
        "ask_name": "Let's build your resume! What is your full name?",
        "ask_email": "Great! What is your email address?",
        "ask_phone": "What is your phone number? (type 'skip' to skip)",
        "ask_linkedin": "What is your LinkedIn URL? (type 'skip' to skip)",
        "ask_github": "What is your GitHub URL? (type 'skip' to skip)",
        "ask_skills": "List your key skills, separated by commas. (type 'skip' to skip)",
        "ask_title": "What title for this resume? e.g. 'Software Engineer CV' (type 'skip' to skip)",
        "confirm": "Your resume is ready! Opening the editor now...",
    },
    "tr": {
        "ask_name": "Harika, sifirdan resume olusturuyoruz! Adiniz ve soyadiniz nedir?",
        "ask_email": "E-posta adresiniz nedir?",
        "ask_phone": "Telefon numaraniz? ('atla' yazarak gecebilirsiniz)",
        "ask_linkedin": "LinkedIn URL'iniz? ('atla' yazarak gecebilirsiniz)",
        "ask_github": "GitHub URL'iniz? ('atla' yazarak gecebilirsiniz)",
        "ask_skills": "Yetenekleriniz neler? (virgülle ayirin) ('atla' yazarak gecebilirsiniz)",
        "ask_title": "Bu resume icin bir baslik belirleyin. (ornek: 'Yazilim Muhendisi CV') ('atla' yazarak gecebilirsiniz)",
        "confirm": "Resume hazir! Editoru aciyorum...",
    },
}


class AgentService:
    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def handle_builder_step(self, message: str, builder_state: dict, user) -> dict:
        lang = builder_state.get("lang", "en")
        step = builder_state.get("step", "ask_name")
        collected = builder_state.get("collected", {})
        prompts = BUILDER_PROMPTS.get(lang, BUILDER_PROMPTS["en"])

        skip_words = {"skip", "atla", "gec", "-", "pas"}
        is_skip = message.strip().lower() in skip_words

        step_index = next(
            (i for i, s in enumerate(BUILDER_STEPS) if s[0] == step), None
        )
        if step_index is not None and step != "confirm":
            field = BUILDER_STEPS[step_index][1]
            if not is_skip and message.strip():
                if field == "skills":
                    collected[field] = [
                        s.strip() for s in message.split(",") if s.strip()
                    ]
                else:
                    collected[field] = message.strip()

        next_index = (step_index or 0) + 1
        if next_index >= len(BUILDER_STEPS):
            next_index = len(BUILDER_STEPS) - 1
        next_step = BUILDER_STEPS[next_index][0]

        if next_step == "confirm":
            resume = self._create_resume_from_builder(user, collected)
            return {
                "type": "redirect",
                "url": reverse("resume:resume_form_edit", args=[resume.pk]),
                "message": prompts["confirm"],
            }

        return {
            "type": "multi_step",
            "step": next_step,
            "message": prompts[next_step],
            "session_data": {
                "mode": "build",
                "step": next_step,
                "collected": collected,
                "lang": lang,
            },
        }

    # ------------------------------------------------------------------
    # Private: language detection
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
            ["Preview first resume", "Analyze resume", "Create new"]
            if lang == "en"
            else ["İlk resume'u önizle", "Resume'u analiz et", "Yeni oluştur"]
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
            ["Analyze this resume", "Download PDF", "Edit in form"]
            if lang == "en"
            else ["Bu resume'u analiz et", "PDF indir", "Formda düzenle"]
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

    def _exec_create_blank(self, lang: str) -> dict:
        msg = {
            "en": "Would you like to fill in details step by step, or go straight to the editor?",
            "tr": "Adim adim bilgilerinizi doldurmami ister misiniz, yoksa dogrudan editore mi gidelim?",
        }.get(lang, "How would you like to create your resume?")
        return {
            "type": "create_choice",
            "message": msg,
            "choices": [
                {
                    "label": "Step by step" if lang == "en" else "Adim adim",
                    "action": "conversational_build",
                },
                {
                    "label": "Open editor" if lang == "en" else "Editoru ac",
                    "action": "redirect",
                    "url": reverse("resume:resume_form"),
                },
            ],
        }

    def _exec_builder_start(self, lang: str, builder_state: dict = None) -> dict:
        prompts = BUILDER_PROMPTS.get(lang, BUILDER_PROMPTS["en"])
        return {
            "type": "multi_step",
            "step": "ask_name",
            "message": prompts["ask_name"],
            "session_data": {
                "mode": "build",
                "step": "ask_name",
                "collected": {},
                "lang": lang,
            },
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

    def _exec_upload_linkedin(self, lang: str) -> dict:
        msg = {
            "en": "Choose your LinkedIn profile PDF.",
            "tr": "LinkedIn profil PDF'inizi secin.",
        }.get(lang, "Choose a LinkedIn PDF.")
        return {
            "type": "request_upload",
            "source": "linkedin",
            "upload_url": reverse("resume:upload_linkedin_cv"),
            "message": msg,
        }

    def _exec_check_quota(self, user, lang: str) -> dict:
        profile = user.profile
        profile.reset_if_new_month()
        limits = settings.FREE_TIER_LIMITS
        resume_count = user.resumes.count()

        if profile.is_pro():
            msg = {
                "en": "You are on the **Pro plan** — unlimited usage!",
                "tr": "**Pro plan**dasiniz — tum ozelliklerde sinirsiz kullanim!",
            }.get(lang, "Pro plan — unlimited.")
            return {"type": "chat", "message": msg, "data": {"tier": "pro"}}

        import_rem = max(0, limits["import_count"] - profile.import_count)
        enhance_rem = max(0, limits["enhance_count"] - profile.enhance_count)
        download_rem = max(0, limits["download_count"] - profile.download_count)
        resume_rem = max(0, limits["resume_count"] - resume_count)
        agent_msg_rem = max(
            0, limits["agent_message_count"] - profile.agent_message_count
        )

        if lang == "tr":
            msg = (
                "**Kullanim Durumunuz (Ucretsiz Plan):**\n"
                f"- PDF Aktarim: {import_rem}/{limits['import_count']} kaldi\n"
                f"- AI Iyilestirme: {enhance_rem}/{limits['enhance_count']} kaldi\n"
                f"- PDF Indirme: {download_rem}/{limits['download_count']} kaldi\n"
                f"- Sohbet Mesaji: {agent_msg_rem}/{limits['agent_message_count']} kaldi\n"
                f"- Resume Sayisi: {resume_count}/{limits['resume_count']}\n\n"
                "Kotaniz her ayin basinda sifirlanir."
            )
        else:
            msg = (
                "**Your Usage (Free Plan):**\n"
                f"- PDF Imports: {import_rem}/{limits['import_count']} remaining\n"
                f"- AI Enhancements: {enhance_rem}/{limits['enhance_count']} remaining\n"
                f"- PDF Downloads: {download_rem}/{limits['download_count']} remaining\n"
                f"- Chat Messages: {agent_msg_rem}/{limits['agent_message_count']} remaining\n"
                f"- Resumes: {resume_count}/{limits['resume_count']}\n\n"
                "Quotas reset each month."
            )
        data = {
            "tier": "free",
            "import_remaining": import_rem,
            "enhance_remaining": enhance_rem,
            "download_remaining": download_rem,
            "resume_remaining": resume_rem,
            "agent_message_remaining": agent_msg_rem,
        }
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

RULES:
- Return the COMPLETE modified resume JSON (all fields, even unchanged ones).
- 'son deneyim' / 'last experience' = the FIRST item in the experience array (most recent).
- experience[].description must ALWAYS be an array of strings (one bullet per element).
- Dates must be in YYYY-MM format (e.g. 2024-03). null means present/current.
- When optimizing for a role (e.g. 'DevOps Engineer'), rewrite ALL experience descriptions
  with strong action verbs and keywords relevant to that role. Keep factual content accurate.
- When adding new experience/education/project, APPEND to the appropriate array.
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
            resume.content = validated["modified_resume"]
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
            resume.content = validated["modified_resume"]
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
    TEMPLATE_ALIASES = {
        "faang": "faangpath-simple",
        "faangpath": "faangpath-simple",
        "simple": "faangpath-simple",
        "klasik": "faangpath-simple",
        "classic": "faangpath-simple",
        "modern": "modern-sidebar",
        "sidebar": "modern-sidebar",
        "modern-sidebar": "modern-sidebar",
        "faangpath-simple": "faangpath-simple",
    }

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
        requested = (params.get("template") or "").lower().strip()
        template_key = self.TEMPLATE_ALIASES.get(requested)

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
                        "key": "faangpath-simple",
                        "name": "FAANGPath Simple",
                        "description": "Classic single-column layout"
                        if lang == "en"
                        else "Klasik tek sütun düzeni",
                    },
                    {
                        "key": "modern-sidebar",
                        "name": "Modern Sidebar",
                        "description": "Two-column layout with sidebar"
                        if lang == "en"
                        else "Kenar çubuklu iki sütun düzeni",
                    },
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

        names = {
            "faangpath-simple": "FAANGPath Simple",
            "modern-sidebar": "Modern Sidebar",
        }
        display = names.get(template_key, template_key)

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

    def _exec_analyze_resume(
        self, user, params: dict, lang: str, active_resume=None
    ) -> dict:
        """Analyze and score a resume's strength with feedback and suggestions."""
        resume = self._resolve_resume(user, params) or active_resume
        if not resume:
            resume = Resume.objects.filter(user=user).order_by("-updated_at").first()
        if not resume:
            return self._resume_not_found(lang, params)

        resume_json = json.dumps(resume.content, ensure_ascii=False, indent=2)
        system_prompt = f"""You are a professional resume reviewer. Analyze the following resume JSON and provide a detailed scoring.

RESUME:
{resume_json}

Respond ONLY with valid JSON in this exact format:
{{
  "overall_score": <number 0-100>,
  "categories": [
    {{"name": "Content Completeness", "score": <0-20>, "max": 20, "feedback": "..."}},
    {{"name": "Impact Language", "score": <0-20>, "max": 20, "feedback": "..."}},
    {{"name": "Formatting & Structure", "score": <0-20>, "max": 20, "feedback": "..."}},
    {{"name": "Skills Coverage", "score": <0-20>, "max": 20, "feedback": "..."}},
    {{"name": "ATS Friendliness", "score": <0-20>, "max": 20, "feedback": "..."}}
  ],
  "top_suggestions": ["suggestion 1", "suggestion 2", "suggestion 3"],
  "response_message": "Brief summary message {"in Turkish" if lang == "tr" else "in English"}"
}}

Rules:
- Be honest but constructive in feedback
- Respond in {"Turkish" if lang == "tr" else "English"}
- overall_score should equal sum of category scores
- Each category feedback should be 1-2 sentences"""

        result = send_openai_message(
            user_message="Analyze this resume",
            meta_prompt=system_prompt,
            is_json=True,
            temperature=0.3,
            max_tokens=2000,
        )

        try:
            parsed = json.loads(result)
            analysis = {
                "overall_score": parsed.get("overall_score", 0),
                "categories": parsed.get("categories", []),
                "top_suggestions": parsed.get("top_suggestions", []),
            }
            msg = parsed.get("response_message", "Analysis complete.")
            quick_replies = (
                ["Improve this resume", "Switch template", "Download PDF"]
                if lang == "en"
                else ["Bu resume'u iyileştir", "Şablon değiştir", "PDF indir"]
            )
            return {
                "type": "analyze_resume",
                "resume_id": resume.id,
                "resume_name": resume.display_name,
                "message": msg,
                "analysis": analysis,
                "quick_replies": quick_replies,
            }
        except (ValueError, TypeError) as e:
            logger.warning("analyze_resume LLM parse error: %s", e)
            msg = {
                "en": "Sorry, I couldn't analyze this resume. Please try again.",
                "tr": "Üzgünüm, bu resume'u analiz edemedim. Lütfen tekrar deneyin.",
            }.get(lang, "Analysis failed.")
            return {"type": "chat", "message": msg}

    def _exec_find_resume(self, user, params: dict, lang: str) -> dict:
        """Search resumes by content — skills, companies, job titles, keywords."""
        query = (params.get("query") or "").lower().strip()
        if not query:
            msg = {
                "en": "What would you like to search for? (e.g., 'Python', 'Google', 'Senior Engineer')",
                "tr": "Ne aramak istersiniz? (örn. 'Python', 'Google', 'Kıdemli Mühendis')",
            }.get(lang, "What to search?")
            return {"type": "chat", "message": msg}

        resumes = list(Resume.objects.filter(user=user).order_by("-updated_at"))
        matches = []

        for r in resumes:
            content = r.content or {}
            searchable_parts = []

            # user_info fields
            user_info = content.get("user_info", {})
            searchable_parts.extend(
                [str(s).lower() for s in user_info.get("skills", [])]
            )
            searchable_parts.append(user_info.get("full_name", "").lower())

            # experience fields
            for exp in content.get("experience", []):
                searchable_parts.append(exp.get("company", "").lower())
                searchable_parts.append(exp.get("title", "").lower())
                desc = exp.get("description", [])
                if isinstance(desc, list):
                    searchable_parts.extend(
                        [d.lower() for d in desc if isinstance(d, str)]
                    )
                elif isinstance(desc, str):
                    searchable_parts.append(desc.lower())

            # education fields
            for edu in content.get("education", []):
                searchable_parts.append(edu.get("school", "").lower())
                searchable_parts.append(edu.get("field_of_study", "").lower())
                searchable_parts.append(edu.get("degree", "").lower())

            # projects
            for proj in content.get("projects_and_publications", []):
                searchable_parts.append(proj.get("name", "").lower())
                searchable_parts.append(proj.get("description", "").lower())

            combined = " ".join(searchable_parts)
            if query in combined:
                matches.append(
                    {
                        "id": r.id,
                        "display_name": r.display_name,
                        "owner_name": r.owner_name,
                        "updated_at": r.updated_at.strftime("%Y-%m-%d"),
                        "template_selector": r.template_selector,
                    }
                )

        if not matches:
            msg = {
                "en": f"No resumes found matching '{query}'.",
                "tr": f"'{query}' ile eşleşen resume bulunamadı.",
            }.get(lang, "No matches.")
            return {"type": "chat", "message": msg}

        count = len(matches)
        msg = {
            "en": f"Found {count} resume{'s' if count != 1 else ''} matching '{query}':",
            "tr": f"'{query}' ile eşleşen {count} resume bulundu:",
        }.get(lang, f"Found {count}.")
        return {
            "type": "chat",
            "message": msg,
            "data": matches,
            "data_type": "resume_list",
        }

    def _exec_compare_resumes(self, user, params: dict, lang: str) -> dict:
        """Compare two resumes side by side."""
        rid1 = params.get("resume_id_1")
        rid2 = params.get("resume_id_2")

        if not rid1 or not rid2:
            msg = {
                "en": "I need two resumes to compare. Please specify both, e.g., 'Compare resume 1 and resume 2'.",
                "tr": "Karşılaştırma için iki resume gerekiyor. Lütfen ikisini de belirtin, örn. 'Resume 1 ve resume 2'yi karşılaştır'.",
            }.get(lang, "Specify two resumes.")
            return {"type": "chat", "message": msg}

        try:
            resume1 = Resume.objects.get(pk=rid1, user=user)
            resume2 = Resume.objects.get(pk=rid2, user=user)
        except Resume.DoesNotExist:
            msg = {
                "en": "One or both resumes not found. Please check the IDs.",
                "tr": "Resume'lardan biri veya her ikisi bulunamadı. Lütfen ID'leri kontrol edin.",
            }.get(lang, "Resume not found.")
            return {"type": "chat", "message": msg}

        r1_json = json.dumps(resume1.content, ensure_ascii=False, indent=2)
        r2_json = json.dumps(resume2.content, ensure_ascii=False, indent=2)

        system_prompt = f"""You are a professional resume reviewer. Compare these two resumes and provide analysis.

RESUME 1 (ID: {resume1.id}, "{resume1.display_name}"):
{r1_json}

RESUME 2 (ID: {resume2.id}, "{resume2.display_name}"):
{r2_json}

Respond ONLY with valid JSON:
{{
  "comparison_summary": "Brief 1-2 sentence overall comparison",
  "resume_1_strengths": ["strength 1", "strength 2"],
  "resume_2_strengths": ["strength 1", "strength 2"],
  "key_differences": ["difference 1", "difference 2", "difference 3"],
  "recommendation": "Which is stronger and why"
}}

Respond in {"Turkish" if lang == "tr" else "English"}."""

        result = send_openai_message(
            user_message="Compare these two resumes",
            meta_prompt=system_prompt,
            is_json=True,
            temperature=0.3,
            max_tokens=2000,
        )

        try:
            parsed = json.loads(result)
            name1 = resume1.display_name
            name2 = resume2.display_name

            lines = [f"**{parsed.get('comparison_summary', '')}**\n"]

            r1_strengths = parsed.get("resume_1_strengths", [])
            if r1_strengths:
                lines.append(f"**{name1} — Strengths:**")
                for s in r1_strengths:
                    lines.append(f"- {s}")
                lines.append("")

            r2_strengths = parsed.get("resume_2_strengths", [])
            if r2_strengths:
                lines.append(f"**{name2} — Strengths:**")
                for s in r2_strengths:
                    lines.append(f"- {s}")
                lines.append("")

            diffs = parsed.get("key_differences", [])
            if diffs:
                lines.append(
                    "**Key Differences:**" if lang == "en" else "**Temel Farklar:**"
                )
                for d in diffs:
                    lines.append(f"- {d}")
                lines.append("")

            rec = parsed.get("recommendation", "")
            if rec:
                label = "Recommendation" if lang == "en" else "Öneri"
                lines.append(f"**{label}:** {rec}")

            return {"type": "chat", "message": "\n".join(lines)}
        except (ValueError, TypeError) as e:
            logger.warning("compare_resumes LLM parse error: %s", e)
            msg = {
                "en": "Sorry, I couldn't compare these resumes. Please try again.",
                "tr": "Üzgünüm, resume'ları karşılaştıramadım. Lütfen tekrar deneyin.",
            }.get(lang, "Comparison failed.")
            return {"type": "chat", "message": msg}

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
            resume.content = translated
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
            logger.warning(
                "modify_resume parse error: %s | result: %s", e, result[:200]
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

    def _create_resume_from_builder(self, user, collected: dict):
        skills_raw = collected.get("skills", [])
        if isinstance(skills_raw, str):
            skills_raw = [s.strip() for s in skills_raw.split(",") if s.strip()]
        content = {
            "language": "English",
            "user_info": {
                "full_name": collected.get("full_name", ""),
                "email": collected.get("email", ""),
                "phone": collected.get("phone", ""),
                "linkedin": collected.get("linkedin", ""),
                "github": collected.get("github", ""),
                "skills": skills_raw,
            },
            "experience": [],
            "education": [],
            "projects_and_publications": [],
        }
        title = collected.get("job_title") or collected.get("full_name") or "My Resume"
        return Resume.objects.create(user=user, title=title, content=content)


agent_service = AgentService()
