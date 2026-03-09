"""
AgentService — Intent classification and execution for the agentic dashboard.

LLM-first approach:
1. Every user message is classified by gpt-4o-mini (JSON mode, ~500ms)
2. The LLM receives full context: user's resumes, quota, active resume
3. It returns {intent, params, message} — no keyword heuristics needed
4. Active-resume context: once a resume is selected, subsequent messages
   are treated as modification instructions for that resume.

Why LLM-only (no keyword map)?
- Context-aware: "kaldır" in "deneyimimi kaldır" → modify, not delete
- Multilingual for free: Turkish, English, mixed — all handled naturally
- Zero maintenance: new intents = update TOOL_CATALOG, no regex/keyword lists
- Cost: ~$0.0003/message with gpt-4o-mini — negligible at current scale
"""
import json
import logging

from django.conf import settings
from django.urls import reverse

from resume.models import Resume
from resume.openai_engine import send_openai_message

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool catalog — the LLM uses this to pick the right intent
# ---------------------------------------------------------------------------
TOOL_CATALOG = [
    {"intent": "list_resumes", "description": "List all resumes of the user"},
    {"intent": "get_resume_details", "description": "Get details of a specific resume by ID or name", "params": ["resume_id"]},
    {"intent": "preview_resume", "description": "Show a preview of a resume in the context panel", "params": ["resume_id"]},
    {"intent": "download_resume", "description": "Download a resume as PDF", "params": ["resume_id"]},
    {"intent": "create_blank_resume", "description": "Create a new blank resume (redirects to editor)"},
    {"intent": "conversational_build", "description": "Start interactive Q&A to build a resume from scratch"},
    {"intent": "upload_resume", "description": "Upload an existing PDF resume for AI extraction"},
    {"intent": "upload_linkedin", "description": "Upload a LinkedIn PDF profile"},
    {"intent": "check_quota", "description": "Show current usage limits and remaining quota"},
    {"intent": "delete_resume", "description": "Delete an ENTIRE resume permanently (asks for confirmation). Do NOT use for removing individual entries like experiences or skills — use modify_resume for that.", "params": ["resume_id"]},
    {"intent": "duplicate_resume", "description": "Duplicate a resume", "params": ["resume_id"]},
    {"intent": "edit_resume", "description": "Open the resume editor for a resume", "params": ["resume_id"]},
    {"intent": "modify_resume", "description": "Apply natural-language edits to resume content: update fields, add/remove entries (experience, education, skills, projects), rewrite descriptions, optimize for a role, etc.", "params": ["resume_id"]},
    {"intent": "switch_template", "description": "Switch the resume template/layout (faangpath-simple or modern-sidebar)", "params": ["resume_id", "template"]},
    {"intent": "help", "description": "Show available commands and how to use the assistant"},
    {"intent": "clarify", "description": "Ask the user to clarify their request"},
]

BUILDER_STEPS = [
    ('ask_name',       'full_name',   None),
    ('ask_email',      'email',       None),
    ('ask_phone',      'phone',       True),
    ('ask_linkedin',   'linkedin',    True),
    ('ask_github',     'github',      True),
    ('ask_skills',     'skills',      True),
    ('ask_title',      'job_title',   True),
    ('confirm',        None,          None),
]

BUILDER_PROMPTS = {
    'en': {
        'ask_name':     "Let's build your resume! What is your full name?",
        'ask_email':    "Great! What is your email address?",
        'ask_phone':    "What is your phone number? (type 'skip' to skip)",
        'ask_linkedin': "What is your LinkedIn URL? (type 'skip' to skip)",
        'ask_github':   "What is your GitHub URL? (type 'skip' to skip)",
        'ask_skills':   "List your key skills, separated by commas. (type 'skip' to skip)",
        'ask_title':    "What title for this resume? e.g. 'Software Engineer CV' (type 'skip' to skip)",
        'confirm':      "Your resume is ready! Opening the editor now...",
    },
    'tr': {
        'ask_name':     "Harika, sifirdan resume olusturuyoruz! Adiniz ve soyadiniz nedir?",
        'ask_email':    "E-posta adresiniz nedir?",
        'ask_phone':    "Telefon numaraniz? ('atla' yazarak gecebilirsiniz)",
        'ask_linkedin': "LinkedIn URL'iniz? ('atla' yazarak gecebilirsiniz)",
        'ask_github':   "GitHub URL'iniz? ('atla' yazarak gecebilirsiniz)",
        'ask_skills':   "Yetenekleriniz neler? (virgülle ayirin) ('atla' yazarak gecebilirsiniz)",
        'ask_title':    "Bu resume icin bir baslik belirleyin. (ornek: 'Yazilim Muhendisi CV') ('atla' yazarak gecebilirsiniz)",
        'confirm':      "Resume hazir! Editoru aciyorum...",
    },
}


class AgentService:

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify_intent(self, message: str, context: dict, active_resume=None) -> dict:
        """
        Send every message to LLM for intent classification.
        Returns: {intent, params, lang, llm_message?}
        """
        lang = self._detect_language(message)
        result = self._llm_classify(message, context, lang, active_resume=active_resume)
        result['lang'] = lang
        return result

    def execute_intent(self, intent: str, params: dict, user, lang: str = 'en',
                       builder_state: dict = None, active_resume=None,
                       user_message: str = '') -> dict:
        handler_map = {
            'list_resumes': lambda: self._exec_list_resumes(user, lang),
            'get_resume_details': lambda: self._exec_get_resume_details(user, params, lang),
            'preview_resume': lambda: self._exec_preview_resume(user, params, lang, active_resume),
            'download_resume': lambda: self._exec_download_resume(user, params, lang),
            'create_blank_resume': lambda: self._exec_create_blank(lang),
            'conversational_build': lambda: self._exec_builder_start(lang, builder_state),
            'upload_resume': lambda: self._exec_upload_resume(lang),
            'upload_linkedin': lambda: self._exec_upload_linkedin(lang),
            'check_quota': lambda: self._exec_check_quota(user, lang),
            'delete_resume': lambda: self._exec_delete_resume(user, params, lang),
            'duplicate_resume': lambda: self._exec_duplicate_resume(user, params, lang),
            'edit_resume': lambda: self._exec_edit_resume(user, params, lang),
            'modify_resume': lambda: self._exec_modify_resume(user, params, lang, user_message, active_resume),
            'switch_template': lambda: self._exec_switch_template(user, params, lang, active_resume),
            'help': lambda: self._exec_help(lang),
        }
        handler = handler_map.get(intent)
        if handler:
            return handler()
        return self._exec_clarify(lang)

    def handle_builder_step(self, message: str, builder_state: dict, user) -> dict:
        lang = builder_state.get('lang', 'en')
        step = builder_state.get('step', 'ask_name')
        collected = builder_state.get('collected', {})
        prompts = BUILDER_PROMPTS.get(lang, BUILDER_PROMPTS['en'])

        skip_words = {'skip', 'atla', 'gec', '-', 'pas'}
        is_skip = message.strip().lower() in skip_words

        step_index = next((i for i, s in enumerate(BUILDER_STEPS) if s[0] == step), None)
        if step_index is not None and step != 'confirm':
            field = BUILDER_STEPS[step_index][1]
            if not is_skip and message.strip():
                if field == 'skills':
                    collected[field] = [s.strip() for s in message.split(',') if s.strip()]
                else:
                    collected[field] = message.strip()

        next_index = (step_index or 0) + 1
        if next_index >= len(BUILDER_STEPS):
            next_index = len(BUILDER_STEPS) - 1
        next_step = BUILDER_STEPS[next_index][0]

        if next_step == 'confirm':
            resume = self._create_resume_from_builder(user, collected)
            return {
                'type': 'redirect',
                'url': reverse('resume:resume_form_edit', args=[resume.pk]),
                'message': prompts['confirm'],
            }

        return {
            'type': 'multi_step',
            'step': next_step,
            'message': prompts[next_step],
            'session_data': {
                'mode': 'build',
                'step': next_step,
                'collected': collected,
                'lang': lang,
            },
        }

    # ------------------------------------------------------------------
    # Private: language detection
    # ------------------------------------------------------------------

    def _detect_language(self, message: str) -> str:
        tr_chars_raw = set('çğıöşüÇĞİÖŞÜ')
        tr_words = {'ve', 'ile', 'bir', 'bu', 'de', 'da', 'mi', 'mu',
                    'ne', 'icin', 'olan', 'var', 'yok', 'evet', 'tamam',
                    'merhaba', 'selam', 'nasil', 'lutfen', 'al', 'son', 'kac', 'tane'}
        if any(c in tr_chars_raw for c in message):
            return 'tr'
        words = set(message.lower().split())
        if words & tr_words:
            return 'tr'
        return 'en'

    # ------------------------------------------------------------------
    # Private: LLM classification
    # ------------------------------------------------------------------

    def _llm_classify(self, message: str, context: dict, lang: str,
                      active_resume=None) -> dict:
        resumes_json = json.dumps(context.get('resumes', []), ensure_ascii=False)
        quota_json = json.dumps(context.get('quota', {}), ensure_ascii=False)
        tools_json = json.dumps(TOOL_CATALOG, ensure_ascii=False)

        active_section = ''
        if active_resume:
            experiences = active_resume.content.get('experience', [])
            exp_summary = ', '.join(
                f"'{e.get('title', '?')}' at '{e.get('company', '?')}'"
                for e in experiences[:5]
            ) if experiences else 'none'

            active_section = f"""
ACTIVE RESUME (the user is currently working on this one):
  ID: {active_resume.id}
  Name: {active_resume.display_name}
  Experiences ({len(experiences)} entries): {exp_summary}

IMPORTANT — active resume rules:
- If the user wants to MODIFY content (add/remove/update experiences, skills, education,
  projects, descriptions, rewrite for a role, etc.) → use "modify_resume" with resume_id={active_resume.id}.
- "kaldır", "remove", "sil" referring to a PART of the resume (an experience, a skill, etc.)
  → use "modify_resume", NOT "delete_resume".
- "delete_resume" is ONLY for deleting the ENTIRE resume permanently.
- If the message is a system command (list, download, quota, preview, upload, etc.)
  → use the appropriate system intent.
- For actions that need a resume_id and none is specified, default to {active_resume.id}.
"""

        system_prompt = f"""You are ResuStack, an AI assistant for a resume management app.
Analyze the user message and choose the best tool.

USER'S RESUMES (rank=1 is the most recently updated):
{resumes_json}

QUOTA STATUS:
{quota_json}
{active_section}
AVAILABLE TOOLS:
{tools_json}

Rules:
- Respond ONLY with valid JSON: {{"intent": "tool_name", "params": {{}}, "message": "reply to user"}}
- Detect message language and reply in the SAME language.
- For resume count questions ("how many resumes", "kac tane resume") → use list_resumes.
- For "last resume" / "son resume" / "en son" without active resume → use the ID where rank=1.
- Resolve resume references (name, "son", "last", "1.", "#2", positional) to their numeric ID.
- If you cannot determine which resume → use "clarify".
- For greetings / general help → use "help".
- Never invent resume IDs not present in USER'S RESUMES.
- For template switching, set params.template to one of: "faangpath-simple" or "modern-sidebar".
  Aliases: faang/klasik/classic/simple → faangpath-simple, modern/sidebar → modern-sidebar.
"""

        result = send_openai_message(
            user_message=message,
            meta_prompt=system_prompt,
            is_json=True,
            temperature=0,
            max_tokens=400,
        )

        try:
            parsed = json.loads(result)
            return {
                'intent': parsed.get('intent', 'clarify'),
                'params': parsed.get('params', {}),
                'llm_message': parsed.get('message', ''),
            }
        except (ValueError, TypeError):
            logger.warning("LLM classification failed to parse: %s", result)
            return {'intent': 'clarify', 'params': {}, 'llm_message': ''}

    # ------------------------------------------------------------------
    # Private: intent executors
    # ------------------------------------------------------------------

    def _exec_list_resumes(self, user, lang: str) -> dict:
        resume_objs = list(Resume.objects.filter(user=user).order_by('-updated_at'))
        data = [
            {
                'id': r.id,
                'display_name': r.display_name,
                'owner_name': r.owner_name,
                'updated_at': r.updated_at.strftime('%Y-%m-%d'),
                'template_selector': r.template_selector,
            }
            for r in resume_objs
        ]
        if not data:
            msg = {
                'en': "You don't have any resumes yet. Say 'create new resume' to get started!",
                'tr': "Henüz hic resume'unuz yok. Baslamak icin 'yeni resume olustur' diyebilirsiniz!",
            }.get(lang, "No resumes yet.")
        else:
            count = len(data)
            msg = (f"{count} resume'unuz var:" if lang == 'tr'
                   else f"You have {count} resume{'s' if count != 1 else ''}:")
        return {'type': 'chat', 'message': msg, 'data': data, 'data_type': 'resume_list'}

    def _exec_get_resume_details(self, user, params: dict, lang: str) -> dict:
        resume = self._resolve_resume(user, params)
        if not resume:
            return self._resume_not_found(lang, params)
        content = resume.content or {}
        user_info = content.get('user_info', {})
        skills = user_info.get('skills', [])
        exp_count = len(content.get('experience', []))
        edu_count = len(content.get('education', []))
        proj_count = len(content.get('projects_and_publications', []))
        if lang == 'tr':
            msg = (f"**{resume.display_name}** (ID: {resume.id})\n"
                   f"- Deneyim: {exp_count} kayit\n- Egitim: {edu_count} kayit\n"
                   f"- Projeler: {proj_count} kayit\n"
                   f"- Yetenekler: {', '.join(skills[:5]) if skills else 'Belirtilmemis'}\n"
                   f"- Sablon: {resume.template_selector}\n"
                   f"- Son guncelleme: {resume.updated_at.strftime('%Y-%m-%d')}")
        else:
            msg = (f"**{resume.display_name}** (ID: {resume.id})\n"
                   f"- Experience: {exp_count} entries\n- Education: {edu_count} entries\n"
                   f"- Projects: {proj_count} entries\n"
                   f"- Skills: {', '.join(skills[:5]) if skills else 'None listed'}\n"
                   f"- Template: {resume.template_selector}\n"
                   f"- Last updated: {resume.updated_at.strftime('%Y-%m-%d')}")
        return {'type': 'chat', 'message': msg, 'data': {'id': resume.id}}

    def _exec_preview_resume(self, user, params: dict, lang: str, active_resume=None) -> dict:
        resume = self._resolve_resume(user, params) or active_resume
        if not resume:
            return self._resume_not_found(lang, params)
        msg = {
            'en': f"Showing preview of **{resume.display_name}**.",
            'tr': f"**{resume.display_name}** onizlemesi sagda gosteriliyor.",
        }.get(lang, f"Previewing resume {resume.id}.")
        return {
            'type': 'preview',
            'resume_id': resume.id,
            'resume_name': resume.display_name,
            'message': msg,
        }

    def _exec_download_resume(self, user, params: dict, lang: str) -> dict:
        resume = self._resolve_resume(user, params)
        if not resume:
            return self._resume_not_found(lang, params)
        url = reverse('resume:download_resume_pdf', args=[resume.id])
        safe_name = (resume.owner_name or 'resume').replace(' ', '_')
        filename = f"{safe_name}_{resume.pk}.pdf"
        msg = {
            'en': f"Downloading **{resume.display_name}** as PDF...",
            'tr': f"**{resume.display_name}** PDF olarak indiriliyor...",
        }.get(lang, f"Downloading resume {resume.id}.")
        return {'type': 'download', 'download_url': url, 'filename': filename, 'message': msg}

    def _exec_create_blank(self, lang: str) -> dict:
        msg = {
            'en': "Would you like to fill in details step by step, or go straight to the editor?",
            'tr': "Adim adim bilgilerinizi doldurmami ister misiniz, yoksa dogrudan editore mi gidelim?",
        }.get(lang, "How would you like to create your resume?")
        return {
            'type': 'create_choice',
            'message': msg,
            'choices': [
                {'label': 'Step by step' if lang == 'en' else 'Adim adim',
                 'action': 'conversational_build'},
                {'label': 'Open editor' if lang == 'en' else 'Editoru ac',
                 'action': 'redirect', 'url': reverse('resume:resume_form')},
            ],
        }

    def _exec_builder_start(self, lang: str, builder_state: dict = None) -> dict:
        prompts = BUILDER_PROMPTS.get(lang, BUILDER_PROMPTS['en'])
        return {
            'type': 'multi_step',
            'step': 'ask_name',
            'message': prompts['ask_name'],
            'session_data': {'mode': 'build', 'step': 'ask_name', 'collected': {}, 'lang': lang},
        }

    def _exec_upload_resume(self, lang: str) -> dict:
        msg = {'en': "Redirecting to PDF upload...", 'tr': "PDF yukleme sayfasina yonlendiriliyorsunuz..."}.get(lang, "Redirecting.")
        return {'type': 'redirect', 'url': reverse('resume:upload_cv'), 'message': msg}

    def _exec_upload_linkedin(self, lang: str) -> dict:
        msg = {'en': "Redirecting to LinkedIn upload...", 'tr': "LinkedIn yukleme sayfasina yonlendiriliyorsunuz..."}.get(lang, "Redirecting.")
        return {'type': 'redirect', 'url': reverse('resume:upload_linkedin_cv'), 'message': msg}

    def _exec_check_quota(self, user, lang: str) -> dict:
        profile = user.profile
        profile.reset_if_new_month()
        limits = settings.FREE_TIER_LIMITS
        resume_count = user.resumes.count()

        if profile.is_pro():
            msg = {
                'en': "You are on the **Pro plan** — unlimited usage!",
                'tr': "**Pro plan**dasiniz — tum ozelliklerde sinirsiz kullanim!",
            }.get(lang, "Pro plan — unlimited.")
            return {'type': 'chat', 'message': msg, 'data': {'tier': 'pro'}}

        import_rem = max(0, limits['import_count'] - profile.import_count)
        enhance_rem = max(0, limits['enhance_count'] - profile.enhance_count)
        download_rem = max(0, limits['download_count'] - profile.download_count)
        resume_rem = max(0, limits['resume_count'] - resume_count)
        agent_msg_rem = max(0, limits['agent_message_count'] - profile.agent_message_count)

        if lang == 'tr':
            msg = ("**Kullanim Durumunuz (Ucretsiz Plan):**\n"
                   f"- PDF Aktarim: {import_rem}/{limits['import_count']} kaldi\n"
                   f"- AI Iyilestirme: {enhance_rem}/{limits['enhance_count']} kaldi\n"
                   f"- PDF Indirme: {download_rem}/{limits['download_count']} kaldi\n"
                   f"- Sohbet Mesaji: {agent_msg_rem}/{limits['agent_message_count']} kaldi\n"
                   f"- Resume Sayisi: {resume_count}/{limits['resume_count']}\n\n"
                   "Kotaniz her ayin basinda sifirlanir.")
        else:
            msg = ("**Your Usage (Free Plan):**\n"
                   f"- PDF Imports: {import_rem}/{limits['import_count']} remaining\n"
                   f"- AI Enhancements: {enhance_rem}/{limits['enhance_count']} remaining\n"
                   f"- PDF Downloads: {download_rem}/{limits['download_count']} remaining\n"
                   f"- Chat Messages: {agent_msg_rem}/{limits['agent_message_count']} remaining\n"
                   f"- Resumes: {resume_count}/{limits['resume_count']}\n\n"
                   "Quotas reset each month.")
        data = {
            'tier': 'free',
            'import_remaining': import_rem, 'enhance_remaining': enhance_rem,
            'download_remaining': download_rem, 'resume_remaining': resume_rem,
            'agent_message_remaining': agent_msg_rem,
        }
        return {'type': 'chat', 'message': msg, 'data': data, 'data_type': 'quota'}

    def _exec_delete_resume(self, user, params: dict, lang: str) -> dict:
        resume = self._resolve_resume(user, params)
        if not resume:
            return self._resume_not_found(lang, params)
        msg = {
            'en': f"Are you sure you want to permanently delete **{resume.display_name}**? This cannot be undone.",
            'tr': f"**{resume.display_name}**'i kalici olarak silmek istediginizden emin misiniz? Bu islem geri alinamaz.",
        }.get(lang, f"Delete {resume.display_name}?")
        return {
            'type': 'confirm',
            'action': 'delete_resume',
            'params': {'resume_id': resume.id},
            'resume_name': resume.display_name,
            'message': msg,
        }

    def _exec_duplicate_resume(self, user, params: dict, lang: str) -> dict:
        resume = self._resolve_resume(user, params)
        if not resume:
            return self._resume_not_found(lang, params)
        profile = user.profile
        if not profile.can_create_resume():
            limit = settings.FREE_TIER_LIMITS['resume_count']
            msg = {
                'en': f"Resume limit reached ({limit}). Upgrade to Pro for unlimited.",
                'tr': f"Resume limiti doldu ({limit}). Sinirsiz icin Pro'ya gecin.",
            }.get(lang, "Resume limit reached.")
            return {'type': 'chat', 'message': msg}
        new_resume = Resume.objects.create(
            user=user, title=f"{resume.title} (Copy)", content=resume.content.copy())
        url = reverse('resume:resume_form_edit', args=[new_resume.pk])
        msg = {
            'en': f"**{resume.display_name}** duplicated! Opening copy in editor...",
            'tr': f"**{resume.display_name}** kopyalandi! Kopya editorde aciliyor...",
        }.get(lang, "Duplicated.")
        return {'type': 'redirect', 'url': url, 'message': msg}

    def _exec_edit_resume(self, user, params: dict, lang: str) -> dict:
        resume = self._resolve_resume(user, params)
        if not resume:
            return self._resume_not_found(lang, params)
        url = reverse('resume:resume_form_edit', args=[resume.id])
        msg = {
            'en': f"Opening **{resume.display_name}** in the editor...",
            'tr': f"**{resume.display_name}** editorde aciliyor...",
        }.get(lang, f"Opening editor for resume {resume.id}.")
        return {'type': 'redirect', 'url': url, 'message': msg}

    def _exec_modify_resume(self, user, params: dict, lang: str,
                            user_message: str, active_resume=None) -> dict:
        """
        Apply natural-language modifications to a resume using LLM.
        Target resume priority: params['resume_id'] > active_resume > most recent.
        """
        # Resolve target resume
        resume = self._resolve_resume(user, params)
        if not resume and active_resume:
            resume = active_resume
        if not resume:
            resume = Resume.objects.filter(user=user).order_by('-updated_at').first()
        if not resume:
            msg = {
                'en': "You don't have any resumes yet.",
                'tr': "Henuz hic resume'unuz yok.",
            }.get(lang, "No resumes found.")
            return {'type': 'chat', 'message': msg}

        resume_json = json.dumps(resume.content, ensure_ascii=False, indent=2)
        experiences = resume.content.get('experience', [])
        last_exp_note = ''
        if experiences:
            last_exp = experiences[0]
            last_exp_note = (f"NOTE: 'Son deneyim' / 'last experience' = first item in experience array: "
                             f"'{last_exp.get('title')}' at '{last_exp.get('company')}'.")

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

        try:
            parsed = json.loads(result)
            modified = parsed.get('modified_resume')
            if modified and isinstance(modified, dict):
                resume.content = modified
                resume.save(update_fields=['content', 'updated_at'])
                return {
                    'type': 'modify_resume',
                    'resume_id': resume.id,
                    'resume_name': resume.display_name,
                    'message': parsed.get('response_message', 'Changes applied.'),
                    'changes_summary': parsed.get('changes_summary', ''),
                }
        except (ValueError, TypeError) as e:
            logger.warning("modify_resume LLM parse error: %s | result: %s", e, result[:200])

        msg = {
            'en': "Sorry, I couldn't apply those changes. Please try rephrasing.",
            'tr': "Uzgunum, degisiklikleri uygulayamadim. Lutfen farkli sekilde ifade etmeyi deneyin.",
        }.get(lang, "Could not apply changes.")
        return {'type': 'chat', 'message': msg}

    # Available templates map (key → display name)
    TEMPLATE_ALIASES = {
        'faang': 'faangpath-simple',
        'faangpath': 'faangpath-simple',
        'simple': 'faangpath-simple',
        'klasik': 'faangpath-simple',
        'classic': 'faangpath-simple',
        'modern': 'modern-sidebar',
        'sidebar': 'modern-sidebar',
        'modern-sidebar': 'modern-sidebar',
        'faangpath-simple': 'faangpath-simple',
    }

    def _exec_switch_template(self, user, params: dict, lang: str, active_resume=None) -> dict:
        """Switch the template of a resume and refresh the preview."""
        resume = self._resolve_resume(user, params)
        if not resume and active_resume:
            resume = active_resume
        if not resume:
            resume = Resume.objects.filter(user=user).order_by('-updated_at').first()
        if not resume:
            return self._resume_not_found(lang, params)

        # Resolve template key from params
        requested = (params.get('template') or '').lower().strip()
        template_key = self.TEMPLATE_ALIASES.get(requested)

        if not template_key:
            opts_tr = "Hangi sablonu kullanmak istersiniz?\n- **faangpath-simple** (klasik, tek sutun)\n- **modern-sidebar** (modern, iki sutun)"
            opts_en = "Which template would you like?\n- **faangpath-simple** (classic, single column)\n- **modern-sidebar** (modern, two columns)"
            return {
                'type': 'chat',
                'message': opts_tr if lang == 'tr' else opts_en,
            }

        resume.template_selector = template_key
        resume.save(update_fields=['template_selector', 'updated_at'])

        names = {'faangpath-simple': 'FAANGPath Simple', 'modern-sidebar': 'Modern Sidebar'}
        display = names.get(template_key, template_key)

        msg = {
            'tr': f"Sablon **{display}** olarak degistirildi. Onizleme guncelleniyor...",
            'en': f"Template switched to **{display}**. Refreshing preview...",
        }.get(lang, f"Template changed to {display}.")

        return {
            'type': 'switch_template',
            'resume_id': resume.id,
            'resume_name': resume.display_name,
            'template': template_key,
            'message': msg,
        }

    def _exec_help(self, lang: str) -> dict:
        if lang == 'tr':
            msg = ("Merhaba! ResuStack asistaninim. Sunlari yapabilirim:\n\n"
                   "- **Resume'larimi listele** — tum resume'larinizi gorun\n"
                   "- **Resume X'i onizle** — sagda onizleme gosterin\n"
                   "- **Resume X'i indir** — PDF olarak indirin\n"
                   "- **Resume X'i sil** — resume'u silin\n"
                   "- **Resume X'i kopyala** — cogaltin\n"
                   "- **Resume X'i duzenle** — editoru acin\n"
                   "- **Yeni resume olustur** — sifirdan baslayin\n"
                   "- **PDF yukle** — mevcut CV'nizi yukleyin\n"
                   "- **Limitlerimi goster** — kota durumunuzu gorun\n\n"
                   "**Resume duzenlemek icin:** Herhangi bir resume'u secin (onizle veya yukle), "
                   "ardindan dogal dilde degisiklik isteyin:\n"
                   "_'Son tecrubemi guncelle', 'AWS deneyimi ekle', 'DevOps rolleri icin yeniden yaz'_\n\n"
                   "X yerine resume numarasi veya adini yazabilirsiniz.")
        else:
            msg = ("Hi! I'm your ResuStack assistant. Here's what I can do:\n\n"
                   "- **List my resumes** — see all your resumes\n"
                   "- **Preview resume X** — show a preview\n"
                   "- **Download resume X** — get the PDF\n"
                   "- **Delete resume X** — remove it\n"
                   "- **Duplicate resume X** — make a copy\n"
                   "- **Edit resume X** — open in editor\n"
                   "- **Create new resume** — start from scratch\n"
                   "- **Upload PDF** — import an existing CV\n"
                   "- **Show my limits** — check your quota\n\n"
                   "**To edit a resume:** Select one (preview/load), then give instructions in plain language:\n"
                   "_'Update my last experience', 'Add AWS experience', 'Rewrite for DevOps roles'_\n\n"
                   "Replace X with a resume number or name.")
        return {'type': 'chat', 'message': msg}

    def _exec_clarify(self, lang: str) -> dict:
        msg = {
            'en': "I didn't quite understand that. Could you rephrase? Type 'help' to see what I can do.",
            'tr': "Tam olarak anlayamadim. Tekrar ifade edebilir misiniz? 'yardim' yazarak neler yapabilecegiimi gorebilirsiniz.",
        }.get(lang, "Could you rephrase that?")
        return {'type': 'chat', 'message': msg}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_resume(self, user, params: dict):
        resume_id = params.get('resume_id')
        if not resume_id:
            return None
        try:
            return Resume.objects.get(pk=resume_id, user=user)
        except Resume.DoesNotExist:
            return None

    def _resume_not_found(self, lang: str, params: dict) -> dict:
        resume_id = params.get('resume_id')
        if resume_id:
            msg = {
                'en': f"I couldn't find resume ID {resume_id}. Type 'list resumes' to see your resumes.",
                'tr': f"ID {resume_id} ile resume bulunamadi. 'listele' yazarak resume'larinizi gorebilirsiniz.",
            }.get(lang, f"Resume {resume_id} not found.")
        else:
            msg = {
                'en': "Which resume do you mean? Type 'list resumes' to see them.",
                'tr': "Hangi resume'yi kastediyorsunuz? 'listele' yazarak gorebilirsiniz.",
            }.get(lang, "Which resume?")
        return {'type': 'chat', 'message': msg}

    def _create_resume_from_builder(self, user, collected: dict):
        skills_raw = collected.get('skills', [])
        if isinstance(skills_raw, str):
            skills_raw = [s.strip() for s in skills_raw.split(',') if s.strip()]
        content = {
            'language': 'English',
            'user_info': {
                'full_name': collected.get('full_name', ''),
                'email': collected.get('email', ''),
                'phone': collected.get('phone', ''),
                'linkedin': collected.get('linkedin', ''),
                'github': collected.get('github', ''),
                'skills': skills_raw,
            },
            'experience': [],
            'education': [],
            'projects_and_publications': [],
        }
        title = collected.get('job_title') or collected.get('full_name') or 'My Resume'
        return Resume.objects.create(user=user, title=title, content=content)


agent_service = AgentService()
