# ResuStack — Architecture & Development Guide

> This document is written for AI agents and new contributors. Read it before making any change.

## Quick Facts

| | |
|---|---|
| **App Name** | ResuStack (formerly resume-enhance) |
| **Tech Stack** | Django 4.2, DRF 3.15, HTMX, TailwindCSS (CDN), Vanilla JS |
| **Database** | PostgreSQL 15 |
| **AI** | OpenAI `gpt-4o-mini` via `resume/openai_engine.py` (writes text); TypeSafe Jev via `resume/typesafe_engine.py` (typed judgments) |
| **PDF Engine** | WeasyPrint (HTML/CSS → PDF) |
| **Auth** | Django built-in auth + custom `SignupView`, `ProfileView` |
| **Deployment** | Dokploy (Dockerfile build, Traefik); every push to `main` deploys |
| **Main Purpose** | AI-powered resume builder: create manually or import from PDF, enhance with AI, export as PDF |

---

## Project Structure

```
resume-enhance/
├── core/                   # Django project (settings, root URLs, auth views)
│   ├── settings.py         # All config: DB, email, logging, template maps, OpenAI key
│   ├── urls.py             # Root router: includes resume.urls + auth + api/v1/
│   └── views.py            # SignupView, ProfileView (auth)
│
├── resume/                 # Main application (all business logic lives here)
│   ├── models.py           # Resume (JSONField content, template_selector), Feedback, UserProfile (quota system)
│   ├── views.py            # All web views: dashboard, form editor, upload, AI enhance, preview, quota checks
│   ├── forms.py            # UserInfoForm, EducationForm, ExperienceForm, ProjectForm + custom fields
│   ├── openai_engine.py    # All OpenAI calls: extract_resume_data, enhance_resume_experience, etc.
│   ├── urls.py             # resume app URL patterns (app_name='resume')
│   ├── api_views.py        # DRF ViewSets: ResumeViewSet + submit_feedback
│   ├── api_urls.py         # DRF router for /api/v1/
│   ├── serializers.py      # DRF serializers: List, Detail, Create
│   ├── services/
│   │   └── pdf_service.py  # HtmlToPdfConverter, ResumePdfService, PdfGenerationError
│   ├── templates/          # App-level templates (resume form, PDF templates, dashboard)
│   └── tests/
│       └── test_pdf_service.py
│
├── templates/              # Global templates (auth: login, signup, profile, password reset)
│   └── registration/
│
├── latex_renderer/         # LaTeX export (hidden/advanced feature, not in main flow)
├── static/                 # Static assets (CSS, JS)
├── Dockerfile              # Multi-stage build, non-root user
├── docker-compose.yml      # Dev: web + db
├── docker-compose.prod.yml # Legacy self-hosted path (prod runs on Dokploy)
└── Caddyfile               # Legacy: used only by docker-compose.prod.yml
```

---

## Architecture Overview

**Monolithic Django MVC.** No microservices, no task queues (yet). Everything runs synchronously in a single Gunicorn process.

### Layers

```
Browser (HTMX/JS)
      │
      ▼
Django Views (resume/views.py)          ← HTTP request handling, form validation
      │
      ├── Forms / FormSets              ← Input validation layer
      │         (resume/forms.py)
      │
      ├── Services                      ← Business logic (PDF generation)
      │         (resume/services/)
      │
      ├── OpenAI Engine                 ← External AI calls (isolated module)
      │         (resume/openai_engine.py)
      │
      └── ORM (Resume model)            ← Data access (resume/models.py)
                  │
                  ▼
            PostgreSQL
```

**Key design choice:** Resume content is a single `JSONField`, not normalized tables. This makes AI parsing output easier to store and reduces migration churn.

---

## Code Conventions

### Naming

- **Python:** `snake_case` for functions, variables, and file names.
- **Constants / Settings keys:** `UPPER_SNAKE_CASE` (e.g., `TEMPLATE_SELECTOR_HTML_MAP`).
- **Class names:** `PascalCase` (e.g., `ResumeFormView`, `ResumePdfService`).
- **URL names:** `snake_case` under `app_name='resume'` namespace (e.g., `resume:dashboard`).
- **Template names:** `snake_case.html` (e.g., `faangpath_simple_template_pdf.html`).

### File Organization

- One concern per file/module.
- All OpenAI calls → `resume/openai_engine.py` (never scatter API calls across views).
- All TypeSafe (Jev) calls → `resume/typesafe_engine.ask()`.
- All PDF logic → `resume/services/pdf_service.py`.
- Auth views → `core/views.py` (keep `resume/views.py` resume-focused).
- New services go in `resume/services/<service_name>.py`.

---

## Design Patterns Used

### 1. Service Object Pattern (PDF)

Business logic is extracted from views into a service class. The view only orchestrates.

```python
# In pdf_service.py
class ResumePdfService:
    def generate_resume_pdf(self, resume_data, template_selector, request) -> bytes:
        template_html_name = settings.TEMPLATE_SELECTOR_HTML_MAP.get(template_selector)
        pdf_bytes = self.pdf_converter.convert_template_to_pdf(...)
        return pdf_bytes

# In views.py — view delegates to service
resume_pdf_service = ResumePdfService()
pdf_bytes = resume_pdf_service.generate_resume_pdf(context, template_selector, request)
```

### 2. Helper / Utility Functions

Small, reusable functions used across views live at module level (not in classes).

```python
def clean_data_for_json(data):
    """Recursively converts date/datetime objects to strings for JSON serialization."""
    if isinstance(data, dict):
        return {k: clean_data_for_json(v) for k, v in data.items()}
    ...
```

### 3. HTMX for Partial Updates (AI Enhance)

AI enhancement buttons POST to dedicated views and return raw HTML (a `<textarea>` fragment). HTMX swaps it in-place — no full page reload.

```python
@login_required
@require_http_methods(["POST"])
def enhance_experience(request):
    return enhance_field(
        request, prefix="experience", field="description",
        enhance_function=enhance_resume_experience,
    )
# Returns: HttpResponse(<textarea ...>enhanced text</textarea>)
```

### 7. Split-Pane Form Editor Layout

`resume_form.html` uses a two-pane layout on desktop (`lg+`):

- **Left pane (60%):** Resume form (`w-full lg:w-3/5`)
- **Right pane (40%):** Live preview sidebar (`hidden lg:flex w-full lg:w-2/5`) — desktop only
- **Mobile:** Single column + floating "Preview" FAB (`lg:hidden fixed bottom-24 right-6`)

**Live Preview (Vanilla JS — not HTMX):**
- Debounce: 700ms after last `input`/`change` event
- `AbortController` cancels in-flight requests on new keystrokes
- Renders to `<iframe id="preview-iframe">` in the right sidebar
- `htmx:afterSwap` event also triggers preview (e.g., after AI enhance)
- Silent on 400 errors (validation failures leave preview unchanged)

**Mobile Preview Modal:**
- Triggered by FAB button (`triggerMobilePreview()` JS function)
- Renders to `<iframe id="preview-iframe-modal">` (separate from desktop iframe)
- Uses the same `preview_resume_form` endpoint

**Template Selector:**
- Moved to the right pane as a **collapsible accordion** (hidden by default)
- `toggleTemplateSection()` toggles visibility + chevron rotation
- Only visible on desktop (inside the `lg:flex` aside)

**Important ID conventions:**
- `preview-iframe` — desktop sidebar iframe
- `preview-iframe-modal` — modal iframe (mobile + legacy)
- `preview-modal` — the modal overlay container
- `selected-template` — hidden input holding the active template key (sent as `template` POST param)

### 4. Template Map Pattern

New templates are added via a settings dict, not hardcoded in views.

```python
# settings.py
TEMPLATE_SELECTOR_HTML_MAP = {
    'faangpath-simple': 'faangpath_simple_template_pdf.html',
    'modern-sidebar': 'modern_sidebar_template_pdf.html',
}

# Usage in service
template_html_name = settings.TEMPLATE_SELECTOR_HTML_MAP.get(template_selector)
if not template_html_name:
    raise PdfGenerationError(f"Invalid template selector: {template_selector}")
```

**Available templates:** fourteen, defined in `resume/resume_templates.py` — the single catalogue the PDF service, the editor's picker and the MCP `list_templates` tool all read. A design is a *layout* plus a *token* dict (type, colour, density, section-header treatment, date placement). Adding one is normally a new entry in `TEMPLATES`, not a new HTML file.

Layouts (`resume/templates/resume_templates/layout_*.html`): `single`, `banner`, `gutter` (headings in a left gutter, one reading order), `grid` (compact grid header, skills grid) — all flow layouts — and `sidebar`, `rail` (two-column; `ResumeTemplate.side_column` is true).

Keys: `faangpath-simple` (default), `compact-ats`, `engineering-classic`, `ivy-serif`, `executive-serif`, `timeline-rail`, `dev-mono`, `centered-editorial`, `label-gutter`, `header-grid`, `accent-banner`, `modern-sidebar`, `split-column`, `right-rail`.

**Optional "what I'm working on" section** (`focus_areas`): stored as `{"include": bool, "items": [str]}` in `Resume.content`, normalized by `resume_content.normalize_focus_areas`. The lines are kept even when `include` is false, so turning the section off never loses them. `build_context` passes an empty `focus_areas` list when it is off, so no template knows about the flag. Position is fixed: above Education (above Experience on sidebar layouts, where Education sits in the aside). Toggled by a checkbox in the editor's Details pane and in the agentic Template pane.

Rules when touching templates:
- `settings.TEMPLATE_SELECTOR_HTML_MAP` is **derived** from the catalogue; never edit it by hand.
- `AgentService._template_key()` resolves what a user typed in chat (key, alias or display name) — add shorthands there, not another map.
- Panes hidden with the `hidden` attribute need `[hidden] { display: none !important; }` on the page: Tailwind's `flex` on the same element otherwise wins.
- **Two-column designs print differently from how they preview.** On screen the body is a flex row that the preview runtime cuts into sheets. WeasyPrint cannot split a flex container across pages (a tall body was pushed whole to page two, leaving page one blank), so under `@media print` the main column is a plain block that flows, the side column is `position: absolute` on page one, and the coloured band is a `linear-gradient` on `@page` (with `.pdf-container` transparent so it shows). A right rail must come *before* the main column in the markup — WeasyPrint places an absolute box on the page where it would otherwise have fallen — and `row-reverse` puts it back on the right on screen. Verify print changes with a real multi-page render, not only the browser preview.
- Two layouts marked `side_column` are exempt from main-flow ordering rules (e.g. focus areas above Education), because their Education sits in the side column.

### Public landing page (`resume/templates/index.html`)

- The template showcase is rendered from `resume_templates.catalog()` and the "Use from Claude" tool chips from the MCP registry, both passed by `landing_page`. Never hardcode design names, counts or tool names there.
- Only claims the product backs up: no company logos, model names, scores or user counts. `resume/tests/test_landing.py` fails on the phrases the old page used and on `href="#"` dead links.
- Screenshots live in `static/screenshots/` (landing) and `screenshots/` (README) as the same files.

### Privacy policy and MCP Registry listing

- `resume/templates/privacy.html` (`/privacy/`) and its Turkish KVKK version `privacy_tr.html` (`/gizlilik/`) state what is collected and **every processor that receives user data** — keep the two in step. When a data flow changes — a new AI call, a new third-party script or CDN, a new provider, a new stored field — update the policy in the same change; `resume/tests/test_privacy_and_registry.py` checks the named processors.
- **Logs hold technical information, never user content.** Do not log AI prompts or responses, resume content, chat messages, job descriptions or email addresses — log ids, lengths and error types instead. The privacy policy states this, so a log line with content makes it untrue.
- The signup page links to both policies (KVKK expects the notice where data is collected) and requires explicit consent to transfers abroad (`SignupForm.privacy_consent`). Consent is recorded on `UserProfile.privacy_consent_at` / `privacy_consent_version`; bump `settings.PRIVACY_POLICY_VERSION` together with the policies' "Last updated" date. Accounts created before 2026-09-13 have no consent record.
- Account deletion (`core.views.delete_account`, POST with password) relies on `on_delete=CASCADE` from `User`; feedback is `SET_NULL`. Adding a model with user data means deciding its `on_delete` and updating both policies and `resume/tests/test_account_deletion.py`.
- `server.json` (repo root) is the listing for the official MCP Registry as `com.resustackapp/resustack`. Keep `version` equal to `SERVER_INFO["version"]` and `description` within 100 characters.
- `/.well-known/mcp-registry-auth` serves `settings.MCP_REGISTRY_AUTH` (a public-key proof record from the environment) and refuses anything not starting with `v=MCPv1;`. The private key is never committed (`key.pem` is ignored).

### Sign in with Google and email verification

- **django-allauth is used only for Google.** ResuStack's own `login`, `SignupView` and password views keep their paths; `include("allauth.urls")` is the last entry in `core/urls.py` so ours answer first. Two authentication backends are configured, so any `login(request, user)` call must pass `backend=`.
- **No matching on email.** `SOCIALACCOUNT_EMAIL_AUTHENTICATION` and `..._AUTO_CONNECT` stay `False`: local addresses were never verified, so matching on them enables account takeover. Google is linked to an existing account only from the Profile page (`process="connect"`). `SOCIALACCOUNT_AUTO_SIGNUP = False` forces `core.forms.GoogleSignupForm`, which asks for the transfer consent and locks the email to Google's; `core.adapters.SocialAccountAdapter` refuses unverified Google emails.
- **Soft email verification** (`core/email_verification.py`): email sign-ups get `UserProfile.email_verification_required = True` and a signed, expiring link. While it is set, every AI entry point refuses with `_ai_locked_message` — enhance (HTML 403), imports (JSON 403), agent chat/approve (chat-shaped JSON, like the quota answer). A new AI entry point must check `_email_unverified(request)` first. Google and pre-existing accounts are never set.
- The Google button renders only when `GOOGLE_LOGIN_ENABLED` (both credentials set); tests that render it must configure `SOCIALACCOUNT_PROVIDERS["google"]["APP"]`. Without credentials, `core.middleware.GoogleLoginAvailabilityMiddleware` answers 404 for `/accounts/google/...` — allauth otherwise raises `SocialApp.DoesNotExist`, a 500.
- **One host.** `core.middleware.CanonicalHostMiddleware` redirects `www.` to the bare domain (301, 308 for non-GET). Absolute URLs follow the request host, so a sign-in started on www sent Google an unregistered callback (`redirect_uri_mismatch`). The only registered redirect URI is `https://resustackapp.com/accounts/google/login/callback/`.
- allauth pages we keep are restyled in `templates/socialaccount/` on `_frame.html` (sign-up step, continue page, connections). Failed or cancelled Google sign-ins never show allauth's error page: `SocialAccountAdapter.on_authentication_error` redirects to login (or profile when signed in) with a message.
- Account email settings (`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`) come from the environment with Gmail defaults. Production must have real credentials: email verification locks AI features until the link arrives.
- Every render path goes through `resume_templates.design_context(key, context)` so the tokens reach the template. A template rendered without it has no styling.
- Only fonts installed in the image may be named (see the font packages in `Dockerfile`). A missing family falls back silently and the design stops matching its preview.
- `_preview_runtime.html` paginates the screen preview using `data-` attributes (`data-paginate`, `data-column`, `data-header`, `data-section`, `data-item`), not class names. New layouts must mark themselves up the same way.
- Token values are interpolated into CSS inside `{% autoescape off %}`; they come from the catalogue, never from user content.
- **Words a template prints itself** (section headings, "Present", the degree joiner) come from `resume_templates.SECTION_LABELS[language]` via `{{ labels.* }}` — never hardcode English in a partial. `language` is the language the resume is *written* in (`Resume.language`), not the interface language; the editor posts it as the hidden `resume_language` field because its preview works on unsaved data. Render inside `resume_templates.rendering_language(language)` so Django's `date` filter prints month names in that language ("Ağu 2022"). Labels are autoescaped like any variable, so tests must compare against `escape(...)` for text with apostrophes or ampersands.

**Template persistence:** Each `Resume` has a `template_selector` CharField (default `'faangpath-simple'`). The chosen template is:
- Sent via hidden input `<input name="template" id="selected-template">` in the form — the pickers write to it, it posts
- Saved to `resume.template_selector` on every save
- Loaded back into the picker via the `selected` context variable
- Used by `preview_resume_form`, `ResumeFormView.post` (PDF export), and `download_resume_pdf`

**Template selector UI behavior** (`resume/templates/resume/_template_picker.html`):
- Data-driven from the catalogue; included once per page with a `group` prefix for ids (`editor` in the form, `agent` in the agentic dashboard)
- **Standard mode:** the left pane is a two-tab switch — Details (the form) and Template (the picker); the right pane is only the preview. Both panes stay mounted: the form still posts while the picker shows.
- **Agentic mode:** the chat panel has the same switch (Chat / Template). Selecting a template POSTs to `resume:set_resume_appearance` for the active resume and refreshes the preview iframe; with no active resume the picker is disabled.
- Cards use `auto-fill, minmax(8.5rem, 1fr)` — a fixed column count made cards viewport-tall on wide screens
- Native radios carry the state: keyboard arrows move between cards, and selection is a CSS sibling rule, not JS class juggling
- `applyTemplate()` writes the hidden input, mirrors the other picker and calls `schedulePreviewUpdate()`
- Filter chips narrow the grid by family; thumbnails are drawn from each design's own tokens (`_template_thumb.html`)

### 5. DRF Action-Based Permissions

API views use `get_queryset` to filter by user + a custom `IsOwnerOrReadOnly` permission. Never expose data across users.

```python
def get_queryset(self):
    return Resume.objects.filter(user=self.request.user).order_by('-updated_at')
```

### 6. Quota Check Pattern (Subscription Tiers)

Free users face monthly quotas; Pro users are unlimited. Check quota **before any DB writes**.

**Two response modes:**
```python
# For non-AJAX (form submissions, direct redirects)
@login_required
def upload_cv(request):
    profile = request.user.profile
    if not profile.can_create_resume():
        messages.error(request, "Resume limit reached...")
        return redirect("resume:dashboard")  # Redirect if quota fails
    # ... AI processing
    resume = Resume.objects.create(...)
    profile.import_count += 1
    profile.save()

# For AJAX (fetch requests)
def post(self, request):
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        if not profile.can_download():
            return JsonResponse({"error": "Limit reached"}, status=403)  # JSON for AJAX
```

**Key points:**
- Check quota **at the start, before any DB operations**.
- **Order matters:** Check quota → validate → save data → increment counter.
- For quota failures: **AJAX** → return `JsonResponse(status=403)`, **non-AJAX** → redirect with message.
- Increment counter **after success** (don't count failed attempts).
- Pro users bypass all checks via `profile.is_pro()`.
- Quotas reset monthly via `reset_if_new_month()` in `UserProfile` model.

---

## Error Handling

### Custom Exceptions

```python
# resume/services/pdf_service.py
class PdfGenerationError(Exception):
    """Raised when WeasyPrint fails. Caught in the view layer."""
    pass
```

### View-Level Error Pattern

Catch service/ORM exceptions, emit Django messages, redirect or re-render.

```python
try:
    resume = Resume.objects.get(pk=pk, user=request.user)
except Resume.DoesNotExist:
    messages.error(request, "Resume not found.")
    return redirect("resume:dashboard")
```

### OpenAI Error Handling

`send_openai_message()` catches all OpenAI exceptions and **returns an error string** (never raises). Callers must check for `"OpenAI API"` or `"Error:"` prefix:

```python
if extracted_json_string.startswith("OpenAI API") or extracted_json_string.startswith("Error:"):
    return JsonResponse({"error": f"AI Service Error: {extracted_json_string}"}, status=503)
```

> ⚠️ **Known inconsistency:** `openai_engine.py` returns error strings instead of raising exceptions. This is a deliberate trade-off for simplicity, but future refactoring should raise typed exceptions instead.

---

## Database & Data Access

### Resume Content Schema (JSONField)

The `Resume.content` field stores the full resume as structured JSON:

```json
{
  "language": "English",
  "user_info": {
    "full_name": "Jane Doe",
    "email": "jane@example.com",
    "phone": "+1 555 000 0000",
    "github": "https://github.com/janedoe",
    "linkedin": "https://linkedin.com/in/janedoe",
    "skills": ["Django", "Python", "Docker"]
  },
  "experience": [
    {
      "title": "Software Engineer",
      "company": "Acme Corp",
      "start_date": "2022-03",
      "end_date": null,
      "current_role": true,
      "description": ["Developed X using Y", "Led Z initiative"]
    }
  ],
  "education": [
    { "school": "MIT", "degree": "Bachelor", "field_of_study": "CS", "start_year": 2018, "end_year": 2022 }
  ],
  "projects_and_publications": [
    { "name": "My Tool", "description": "Built a tool...", "link": "https://github.com/..." }
  ]
}
```

### ORM Patterns

- **Always filter by user:** `Resume.objects.get(pk=pk, user=request.user)` (prevents IDOR).
- **No raw SQL.** Use Django ORM for all queries.
- **Use `.copy()` for JSON duplication:** `content=original_resume.content.copy()`.

### UserProfile Model (Subscription Tiers)

OneToOneField to User, auto-created via post_save signal. Stores tier (free/pro) + monthly quota counters.

```python
profile = request.user.profile
profile.is_pro()              # → True if tier='pro'
profile.can_import()           # → True if pro OR import_count < limit
profile.reset_if_new_month()   # Auto-resets counters if month changed
```

**Quota limits defined in `settings.FREE_TIER_LIMITS`:**
- `import_count`: 2 (PDF/LinkedIn imports per month) — checked in `upload_cv()`, `upload_linkedin_cv()`
- `enhance_count`: 10 (AI text enhancements per month) — checked in `enhance_experience()`, `enhance_project()`
- `download_count`: 5 (PDF exports per month) — checked in `download_resume_pdf()`, `ResumeFormView.post(export_format='pdf')`
- `resume_count`: 3 (total resumes, not monthly) — checked in `upload_cv()`, `upload_linkedin_cv()`, `duplicate_resume()`, `ResumeViewSet.create()` — **NOT checked in blank resume creation** (`ResumeFormView.post(pk=None, form_action='save_only')`)

---

## Testing Strategy

- **Framework:** `django.test.TestCase` (standard Django).
- **Test location:** `resume/tests/` directory (not in `tests.py` root file).
- **Current coverage:** Focused on `pdf_service.py` — unit tests for `HtmlToPdfConverter` and `ResumePdfService` with WeasyPrint mocked.
- **Run tests:**
  ```bash
  python manage.py test resume
  ```
- **Mock external calls:** WeasyPrint and OpenAI must always be mocked in tests. Never make real API calls in tests.
- **Gap:** OpenAI engine and views have no test coverage yet. New features should include tests.

---

## Common Operations

### Adding a New View

1. Write the view function/class in `resume/views.py`.
2. Add `@login_required` if authentication is required.
3. Add `@require_http_methods(["POST"])` for mutation endpoints.
4. Register the URL in `resume/urls.py`.
5. Use `messages.success/error(request, "...")` for user feedback.

### Adding a New AI Enhancement

1. Add a new function to `resume/openai_engine.py` following the `send_openai_message()` pattern.
2. Create a view in `resume/views.py` calling `enhance_field()` with your new function.
3. Wire up the HTMX button in the template to POST to the new URL.

### Adding a New Resume Template

1. Add a `ResumeTemplate(...)` entry to `TEMPLATES` in `resume/resume_templates.py`: key, name, one-line description (a model reads it over MCP), an existing `layout`, a `family`, and the tokens that differ from `BASE_TOKENS`.
2. That is usually all: the settings map, the picker card, the thumbnail and `list_templates` all derive from it.
3. Only write a new `layout_*.html` when the structure itself is new. Mark it up with the `data-` attributes the preview runtime looks for.
4. Include the `scaleToFit()` script inside the template (already in `faangpath_simple_template_pdf.html` as reference). This script detects when the template is rendered inside an `<iframe>` (live preview context vs. PDF generation) and applies `body.style.zoom` to fit the container width. Without it, the A4 preview overflows the sidebar.
5. Use `@media print, screen { }` for styles that must match between preview and PDF (e.g., font sizes, spacing). This ensures page breaks align correctly in the live preview. Pure PDF-only styles (e.g., `@page` rules) go in `@media print { }` alone.

### Adding Quota Checks to New Features

**Order (critical for correctness):**
1. Check quota **before form validation** (or early in POST)
2. Validate forms/input
3. Check quota again for dependent operations (e.g., export_format-specific limits)
4. Perform DB writes (create/update/delete)
5. Increment counter **after success**

**Response handling:**
- **AJAX requests** (headers contain `X-Requested-With: XMLHttpRequest`): return `JsonResponse({"error": "..."}, status=403)`
- **Form submissions**: return `redirect()` with `messages.error()`

Example: New feature that costs quota.
```python
@login_required
def new_feature(request):
    # 1. Check quota early
    profile = request.user.profile
    if not profile.can_xyz():
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({"error": "Monthly limit reached"}, status=403)
        messages.error(request, "Monthly limit reached.")
        return redirect('resume:dashboard')

    # 2. Validate input
    form = MyForm(request.POST)
    if not form.is_valid():
        # ... re-render form

    # 3. DB writes
    MyModel.objects.create(...)

    # 4. Increment counter
    profile.xyz_count += 1
    profile.save()
```

**Special cases:**
- Pro users: No quota checks needed (all `can_xyz()` methods bypass checks for `tier='pro'`)
- Non-DB operations (preview/review): No quota check required
- Expensive operations (AI calls): Check quota before, not after (don't waste tokens on users over limit)

---

## Performance Considerations

- **OpenAI calls are synchronous** and can take 5–30s. Upload and enhance endpoints may timeout under load. Future fix: move to Celery + Redis background workers.
- **No caching yet.** Identical AI requests hit the API every time. Future fix: cache by input hash.
- **No N+1 risk** currently — only one model and queries are simple filters. Quota checks use `request.user.profile` (OneToOne field, no extra queries). User count checks use `user.resumes.count()` (indexed, fast).
- **PDF generation** is CPU/memory intensive. WeasyPrint runs in-process. Keep an eye on memory under concurrent load.
- **File uploads** are validated: PDF only, max 5MB, minimum text length guard before calling OpenAI.
- **Quota checks are per-request, not per-action-batch** — each view independently checks and increments. Loading states on buttons prevent user-driven request spam.

---

## Security Notes

- **IDOR prevention:** Always filter ORM queries by `user=request.user`. Never `Resume.objects.get(pk=pk)` alone.
- **File upload:** PDF only, 5MB max, validated before OpenAI call.
- **Preview endpoint auth:** `preview_resume_form` requires `@login_required` — unauthenticated users get a redirect to login, not an HTML preview.
- **CSRF:** Django middleware enforces it. `@csrf_exempt` is not used except where HTMX handles it via header.
- **Environment secrets:** `SECRET_KEY`, `OPENAI_API_KEY`, DB creds via `.env` (python-dotenv). Never hardcode.
- **Production HTTPS:** Enforced via `SECURE_SSL_REDIRECT = True` and `SECURE_PROXY_SSL_HEADER` when `DEBUG=False`.
- **URL normalization:** `_normalize_url()` in `forms.py` prepends `https://` to bare URLs.

---

## External Integrations

### OpenAI (`resume/openai_engine.py`)

- **Model:** `gpt-4o-mini` (cost-optimized; switched from `gpt-4o` for ~95% cost reduction).
- **Timeout:** 90 seconds per request.
- **JSON mode:** `is_json=True` enforces `response_format: json_object` for structured parsing.
- **Deterministic output:** `temperature=0` for parsing, `temperature=0.7` for enhancement.
- **Error pattern:** Returns error strings, not exceptions. Callers must check prefix.

```python
# Parsing PDF content — deterministic, JSON mode
result = send_openai_message(user_message, meta_prompt, is_json=True, temperature=0, max_tokens=6000)

# Enhancing text — creative, free-form
result = send_openai_message(user_message, meta_prompt, temperature=0.7, max_tokens=1500)
```

### TypeSafe Jev (`resume/typesafe_engine.py`)

- **Division of labour:** OpenAI generates text; Jev answers typed questions about text (`Noul` = probability of yes, `Choice` = one of a set, `Score` = position on ordered levels) with probabilities. A decision the product acts on, or a number shown to the user, should come from Jev; prose stays with OpenAI.
- **`ask(state, questions, purpose=...)` never raises.** It returns `Answers` or `None` (no key, timeout, API error). Every caller must have a path for `None`: fall back, skip the check, or let the user through. Nothing may depend on Jev being up.
- **Model is pinned** (`settings.TYPESAFE_MODEL`, e.g. `jev-1.13.0`, not `jev-latest`) so stored scores do not drift. Bump it only after `python manage.py jev_eval` before/after.
- **`jev_eval`** runs labelled, synthetic cases against the real API (paid; never part of `manage.py test`). A feature that adds Jev judgments adds a suite to `SUITES` and uses it to choose thresholds.
- **Tests:** `TYPESAFE_API_KEY` is blanked when `manage.py test` runs, so an unmocked call returns `None` instead of reaching the API. Mock `typesafe_engine.ask` (or `_get_client`).
- **Logs:** the SDK logs request/response bodies at DEBUG; the `typesafe_sdk` logger is pinned to WARNING in `LOGGING`. `ask` logs purpose, counts, latency and tokens only.
- One request handled 200 questions in <1s; `ask` splits batches above `MAX_QUESTIONS_PER_REQUEST`. Questions in one call are independent — use a second call only when an answer is needed to build the next question.
- TypeSafe is a named processor in both privacy policies and in the sign-up consent text.

### Import check (`resume/services/import_check.py`)

- Both PDF and LinkedIn imports go through `views._save_import` → `import_check.run(extracted, source_text)` before anything is stored.
- **Shape:** `resume_content.conform` holds content to `resume_content.CONTENT_SCHEMA` (the same schema MCP publishes — `mcp_server.tools.CONTENT_SCHEMA` is an alias). Unknown keys are dropped and reported by *name* in `import_review["dropped"]`; wrong types are coerced.
- **Evidence:** contact details (email, phone, URLs) are checked verbatim by code; everything else is one Jev `Noul` per value ("does the document support this?"). Below `SUPPORT_THRESHOLD` a value is flagged, never removed. A wrong contact value is replaced only when Jev picks the right one from regex candidates in the text (`status: "corrected"`).
- Stored on `Resume.import_review`; the editor shows `open_flags()` — a flag disappears once its value is edited, so there is no resolved state to maintain. `dismiss_import_review` clears it.
- Flag paths are `experience[0].description[2]`-style and map to editor input ids via `_field_id` (`id_experience-0-description`). A new editor field for an imported value needs a mapping there.
- Threshold chosen with `manage.py jev_eval import` (`resume/evals/import_cases.py`); add a case there when a false flag or a miss is reported.

### Agent tool-call guardrail (`resume/services/agent_guard.py`)

- In `agent_loop._run_events`, every call to a tool in `GUARDED_TOOLS` (anything that writes or spends quota) is judged by Jev before it runs: did the user ask for it, do the arguments match, which resume do they mean. One request, ~0.3s.
- Verdicts: **block** → the tool does not run; the model gets a `guardrail_blocked` tool result and asks the user. **warn** → a destructive tool asks for approval *even when the user turned confirmations off*, and the card shows a warning. **allow** → as before.
- The approval card now names the target resume (`copy.target`, "Name (language)" — language versions share a title).
- Fail-open: Jev unavailable → allow. An approved (parked) call is not re-checked.
- A new tool that writes or spends quota belongs in `GUARDED_TOOLS`. Thresholds come from `manage.py jev_eval guard` (`resume/evals/guard_cases.py`) — add the conversation there when a wrong block or a miss is reported.

### Job match scoring (`resume/services/job_match.py`)

- `job_service._analyze` tries Jev first and falls back to the single OpenAI call (`_analyze_llm`) when Jev is unavailable. `scoring_version` (`"jev-1"` / `"llm-v1"`) is stored on `JobPosting` and in each `score_history` entry; `record_score` does not report a "previous score" across versions.
- Pipeline: code splits the posting into lines → Jev classifies each line (requirement / responsibility / heading / about / other), P(required), and P(instruction aimed at an AI) → Jev scores each requirement against the resume as numbered lines (`evidence_lines`), a 4-level `Score` plus a `Choice` of the evidence line → code computes `composite()` (requirement weight 1 + P(required), responsibility 1) → OpenAI writes only title, company, verdict, suggestions and short labels from that table.
- Lines judged as instructions to an AI screener are dropped before scoring and before the OpenAI prompt; the result carries `notices: ["instructions_removed"]` and the agent is told to mention it.
- `JobPosting.requirements` holds the table: `{id, text, label, kind, must_have, level, status (covered/partial/missing), confidence, uncertain, evidence}`. Evidence is a quoted resume line, never generated.
- Skills listed only in the skills section score "partial" by design — the useful advice is to show them in a bullet.
- Scores repeat within ±1 for the same input. Thresholds come from `manage.py jev_eval match` (`resume/evals/match_cases.py`).
- **Agentic UI:** "Application Score" (first quick-action chip) opens a modal — posting + resume picker — and sends the posting to the assistant as an ordinary message (`UI.app_score_message`), so the similar-posting question, history and follow-ups keep working. Pasting ≥200 chars into the chat input asks `detect_job_posting` (a Jev `Noul`); if it is a posting, an offer bar appears — nothing is sent on its own.
- The match panel (`renderJobMatch`) groups requirements into Required / Nice to have, each expandable to the posting line and the quoted resume evidence; estimated (`llm-v1`) matches fall back to keyword chips. Follow-up chips ("Apply the suggestions", "Tailor a copy") send messages back through the assistant, so approval and the guardrail apply.
- Job panel wording lives only in `job_service.JOB_COPY` (both languages, same keys — a test checks); the dashboard seeds `JOB_LABELS` from it and each chat turn replaces it with the conversation language's.
- Quick chips accept `{label, message}` and use listeners, not inline `onclick` strings: a Turkish apostrophe ("CV'yi") ended the JS string.
- **One recorder:** `job_service.match_and_record` (same-text re-measure, similar-posting question, application cap, snapshot, score) serves both the agent's `match_job` and MCP's. Change the rules there, not in either caller.
- **MCP** (`mcp_server/tools.py`): `match_job` (needs `title` and `company` from the client, `prose=False` — no OpenAI call, the client's model writes the advice), `list_jobs`, `get_job`, `update_job`. Same application cap and email-verification lock as the web. The surface test in `mcp_server/tests/test_tools.py` lists every tool on purpose; a new tool updates it, bumps `SERVER_INFO["version"]` and `server.json` together.

### WeasyPrint (PDF Generation)

- **No extra stylesheet.** Each layout carries its own `<style>` (`resume_templates/_styles.html`); `ResumePdfService` passes no CSS to WeasyPrint so the download matches the live preview of the same template. Fonts are always named explicitly — renderer defaults differ between WeasyPrint and the browser.
- **Template → PDF flow:** Django `render_to_string()` → WeasyPrint HTML → PDF bytes → `HttpResponse`.
- **No temp files.** PDF bytes are written directly to the HTTP response.

---

## Known Issues & Workarounds

| Issue | Workaround |
|---|---|
| OpenAI returns markdown-wrapped JSON (`\`\`\`json ... \`\`\``) | Strip code block markers in `upload_cv` view before `json.loads()` |
| Multi-column PDFs produce garbled text | OpenAI prompt explicitly instructs reconstruction of reading order |
| Education dates come as `"2020-05"` strings from AI | `extract_year()` helper in views splits on `-` and parses the year |
| Experience descriptions may be `list` or `str` from AI | Normalize to `\n\n`.join(list) before populating formset |
| WeasyPrint + pydyf version incompatibility | Pin `WeasyPrint>=62.0` and `pydyf>=0.11.0` in requirements.txt |

---

## Decision Rationale

| Decision | Reason |
|---|---|
| `JSONField` for resume content | Flexible schema for AI output; avoids migrations for every resume section change |
| `gpt-4o-mini` over `gpt-4o` | ~95% cost reduction, same quality for resume tasks |
| WeasyPrint over LaTeX for PDF | LaTeX requires TeX Live (~500MB Docker image bloat); WeasyPrint is good enough for standard resumes |
| HTMX for AI enhance buttons | Avoids full React/Vue complexity; Django server-rendered HTML fragments are sufficient |
| TailwindCSS via CDN | Zero build step; acceptable for MVP scale; move to PostCSS build if bundle size becomes an issue |
| No async/Celery (yet) | Accepted synchronous timeout risk for MVP; deferred to Phase 4 roadmap |
| Template selector persisted per resume | Users can switch templates and have the choice saved; preview/export/download all respect it |
| In-house quota system, no payment yet | Decoupled quota logic from payment; easy to plug in LemonSqueezy/Paddle webhook later |
| Per-view quota checks | Simple, explicit, no middleware magic. Each sensitive action independently validates before writing. |

---

## API Design (DRF)

- **Base path:** `/api/v1/`
- **Auth:** Session authentication (same Django session cookie).
- **Versioning:** Path-based (`/api/v1/`).
- **Serializer strategy:** Different serializers per action (`List`, `Detail`, `Create`) to keep payloads lean.
- **Permission:** `IsAuthenticated` + `IsOwnerOrReadOnly` (users only see their own resumes).

```
GET    /api/v1/resumes/          → list (owned by user)
POST   /api/v1/resumes/          → create
GET    /api/v1/resumes/{id}/     → retrieve
PUT    /api/v1/resumes/{id}/     → full update
PATCH  /api/v1/resumes/{id}/     → partial update
DELETE /api/v1/resumes/{id}/     → destroy
POST   /api/v1/resumes/{id}/duplicate/ → custom action
```

Important: Run project with docker.