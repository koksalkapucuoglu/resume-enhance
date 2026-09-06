# ResuStack — Ürün Durumu ve Yol Haritası

> Kaynak: kod tabanı uçtan uca taraması (2026-09-06) + `django_enhance_resume.md` fikir defteri + `.claude/AGENTIC.md`.
> Amaç: **bugün ne var**, **ne yarım kaldı**, **ne gelebilir** — üçünü tek yerde tutmak.

---

## 1. Ürünün Hikâyesi (fikir defterinden çıkan çizgi)

| Dönem | Karar | Sonuç |
|---|---|---|
| Eki 2024 | "CV template + AI iyileştirme tek uygulamada" fikri | Proje başladı |
| 2024–25 | LaTeX (pdflatex) ile PDF üretimi | Docker imajı ~5GB, debug zor |
| Haz 2025 | **TeX çıkarıldı**, HTML → PDF (WeasyPrint) | İmaj küçüldü, bakım kolaylaştı; `latex_renderer/` ölü kod olarak kaldı |
| Şub 2026 | MVP: manuel / PDF import / LinkedIn import + auth + tasarım | Canlıya çıktı (resustackapp.com) |
| Mar 2026 | Multi-template, canlı önizleme, kota sistemi, TR/EN dil | Ürünleşme |
| Mar 2026 (Faz 1.1–2) | **Agentic mode** — LLM-only intent classification | Sohbetle resume yönetimi |

**Bugünkü tanım:** AI destekli resume builder. İki arayüz modu (Standard form editörü / Agentic sohbet), iki şablon, PDF export, free/pro kota sistemi.

---

## 2. Mevcut Özellikler

### 2.1 Resume Oluşturma
| Özellik | Nerede | Not |
|---|---|---|
| Boş resume (manuel) | `ResumeFormView` | **Kota kontrolü yok** (bilinçli boşluk) |
| PDF'ten import | `upload_cv` → `extract_resume_data` | PyPDF2 text extraction + gpt-4o-mini JSON mode |
| LinkedIn PDF'ten import | `upload_linkedin_cv` → `extract_linkedin_resume_data` | Ayrı prompt |
| Duplicate | `duplicate_resume`, API `/duplicate/` | `content.copy()` |
| Delete | `delete_resume` | POST + confirm modal |

### 2.2 Editör (`resume_form.html`, ~1350 satır)
- 60/40 split-pane: sol form, sağ canlı önizleme iframe'i
- 700ms debounce + `AbortController` ile önizleme fetch
- Django formset'ler: education / experience / projects — JS ile dinamik add/remove
- Şablon seçici: sağ panelde açılır-kapanır accordion, seçim `Resume.template_selector`'a kaydedilir
- Mobil: tek kolon + FAB → preview modal

### 2.3 AI Yetenekleri
| Fonksiyon | Model | Ayar |
|---|---|---|
| `enhance_resume_experience` | gpt-4o-mini | temp 0.7, max 1500, STAR formatı, otomatik bullet split |
| `enhance_project_description` | gpt-4o-mini | temp 0.7, max 1500 |
| `extract_resume_data` | gpt-4o-mini | temp 0, JSON mode, max 6000, çok kolonlu PDF reconstruction talimatı |
| `extract_linkedin_resume_data` | gpt-4o-mini | temp 0, JSON mode, max 6000 |
| Agent intent classification | gpt-4o-mini | temp 0, JSON mode, ~$0.0003/mesaj |
| `modify_resume` / `analyze_resume` / `compare_resumes` / `translate_resume` | gpt-4o-mini | AgentService içinde |

Enhance butonları HTMX ile `<textarea>` fragment döndürür — sayfa reload yok.

### 2.4 Agentic Mode
- `UserProfile.ui_mode` ile Standard ↔ Agentic geçişi (`/agent/toggle-mode/`)
- **LLM-only intent classification** — keyword map tamamen kaldırıldı; `TOOL_CATALOG` LLM'e verilir
- 18 intent: `list_resumes`, `get_resume_details`, `preview_resume`, `download_resume`, `create_blank_resume`, `conversational_build`, `upload_resume`, `upload_linkedin`, `check_quota`, `delete_resume`, `duplicate_resume`, `edit_resume`, `modify_resume`, `switch_template`, `analyze_resume`, `find_resume`, `compare_resumes`, `translate_resume`, `help`, `clarify`
- Response protokolü 10 tip: `chat | preview | download | redirect | confirm | create_choice | multi_step | switch_template | modify_resume | analyze_resume`
- UI: 60/40 chat + context panel, quick reply chip'leri, typing indicator, hata mesajında retry butonu, timestamp, localStorage'da son 50 tur
- Rate limit: 20 req / 60s (cache tabanlı, HTTP 429)

### 2.5 PDF & Şablonlar
- WeasyPrint, in-process, temp dosya yok
- 2 şablon: `faangpath-simple` (tek kolon), `modern-sidebar` (iki kolon)
- `TEMPLATE_SELECTOR_HTML_MAP` üzerinden eklenir — view'da hardcode yok
- `scaleToFit()` JS: iframe içindeyken A4'ü panel genişliğine ölçekler
- `@media print, screen` ile preview/PDF tutarlılığı

### 2.6 Kota & Tier
`FREE_TIER_LIMITS`: import 2/ay, enhance 10/ay, download 5/ay, agent_message 10/ay, resume 3 (toplam).
`reset_if_new_month()` ile aylık sıfırlama. Pro tier tüm kontrolleri bypass eder.
**Pro'ya geçiş yolu yok** — ödeme entegrasyonu yapılmadı, tier sadece admin'den elle değişir.

### 2.7 Diğer
- Auth: Django built-in + custom `SignupView` (email zorunlu), password reset (Gmail SMTP)
- Profil sayfası: şifre değiştirme, kota progress bar'ları, dashboard mode kartı
- Dil: `resume/i18n.py` içinde elle yazılmış TR/EN sözlük + context processor (Django gettext **değil**)
- REST API: `/api/v1/resumes/` CRUD + duplicate, `/api/v1/feedback/` — session auth
- Feedback modal (rating + mesaj), `Feedback` modeli
- Deploy: Docker Compose + Gunicorn + Caddy (veya Dokploy), `entrypoint.sh` migrate + collectstatic
- Test: `test_pdf_service.py` (WeasyPrint mocked) + `test_agent_service.py` (47 case)

---

## 3. Yarım Kalan / Bilinen Sorunlar

### Fikir defterinde ✅ olmayanlar
- [ ] Admin'den yeni şablon ekleme (kodda `TEMPLATE_SELECTOR_HTML_MAP` sabit dict)
- [ ] "PDF Upload & Parsing İyileştirme — section detection, otomatik alan eşleme"
- [ ] Şablon önerisi alınan form
- [ ] Mobile responsibility maddeleri (sol-sağ kayma, feedback butonu konumu, save butonu overlap, choose template alan kaplaması, preview butonu görünmüyor)
- [ ] "Dil kurallarına göre tüm sayfaları dolaşıp eksikleri ilet" — i18n coverage denetimi

### AGENTIC.md'de açık kalanlar
- [ ] Streaming response (karakter karakter yazma)
- [ ] **Agentic modda "yeni cv oluştur" step-by-step çalışmıyor**
- [ ] **Agentic modda upload PDF çalışmıyor**
- [ ] Faz 3 — MCP Server (token auth, `/api/v1/quota/`, FastMCP tool'ları) hiç başlanmadı

### Kod taramasında çıkanlar
| Konu | Detay | Risk |
|---|---|---|
| **Sızmış API key** | `django_enhance_resume.md` içinde plaintext `sk-proj-...` (dosya git'e eklenmemiş ama diskte duruyor) | Yüksek — key'i OpenAI panelinden iptal et |
| **LocMemCache + rate limit** | `CACHES` locmem; Gunicorn multi-worker'da her worker ayrı sayaç tutar → limit worker sayısı kadar gevşer | Orta — Redis'e geç |
| **Senkron OpenAI** | Import 5–30s, Gunicorn worker'ı bloklar | Orta — Celery/Redis ya da en azından worker timeout ayarı |
| **Ölü kod** | `latex_renderer/` paketi (`services.py`, `utils.py`, `template_handler.py`) hiçbir yerden import edilmiyor; `faangpath_simple_template_preview.html` de kullanılmıyor | Düşük — sil |
| **Hata sözleşmesi tutarsızlığı** | `openai_engine` exception fırlatmak yerine hata **string'i** döndürüyor; çağıranlar prefix kontrol ediyor | Orta — typed exception'a geç |
| **`upload_cv` GET'te sessiz redirect** | POST dışı istek `resume:index`'e düşüyor, `@require_http_methods` yok | Düşük |
| **Kota bypass** | Boş resume oluşturmada `can_create_resume()` çağrılmıyor — kullanıcı sınırsız boş resume açabilir | Orta |
| **Test boşluğu** | `views.py` (1548 satır) ve `openai_engine.py` için sıfır test | Orta |
| **`views.py` şişkin** | 1548 satır; upload/enhance/preview/download hepsi tek dosyada, `services/` katmanı sadece PDF ve agent için var | Orta — refactor |
| **Şablon dosyaları dev** | `resume_form.html` 1352, `dashboard_agentic.html` 1195 satır — inline JS + inline CSS | Orta |
| **Tailwind CDN** | Prod'da CDN script; FOUC + boyut + offline risk | Düşük |
| **Ödeme yok** | Pro tier ulaşılamaz → kota sistemi gelir üretmiyor | Ürün açısından yüksek |

---

## 4. Gelebilecek Özellikler

Etki/efor kabaca: 🟢 küçük, 🟡 orta, 🔴 büyük.

### 4.1 Çekirdek Ürün Değeri (en yüksek getiri)
| Özellik | Neden | Efor |
|---|---|---|
| **Job Description Matching** — JD yapıştır, resume'u o ilana göre uyarla, eşleşme skoru + eksik keyword listesi | Rakiplerin (Teal, Rezi) ana satış argümanı; mevcut `analyze_resume` altyapısı üzerine oturur | 🟡 |
| **ATS skoru + rapor** | `analyze_resume` zaten 5 kategori skorluyor; ATS'i ayrı, indirilebilir rapora çevir | 🟢 |
| **Cover letter üretimi** | Resume + JD → ön yazı. Aynı LLM pipeline, yeni prompt + yeni model/tablo | 🟡 |
| **Resume versiyonlama** | Her kayıtta snapshot; "3 gün önceki haline dön". `JSONField` sayesinde ucuz | 🟡 |
| **Tailored resume varyantları** | Bir base resume + N ilana özel türev; dashboard'da grupla | 🟡 |
| **Daha çok şablon (3–6 adet)** | Fikir defterinde de var; şablon çeşitliliği dönüşüm oranını doğrudan etkiler | 🟡 |

### 4.2 Para Kazanma
| Özellik | Not | Efor |
|---|---|---|
| **Ödeme entegrasyonu** (LemonSqueezy / Paddle / Iyzico) | `UserProfile.tier` hazır, sadece webhook + checkout gerek. Karar defterde "sonra plug-in ederiz" diye ertelenmişti | 🟡 |
| Fatura/abonelik yönetim sayfası | Profil sayfasına sekme | 🟢 |
| Tek seferlik satın alma (1 PDF export credit) | Free→Pro geçişi ağır gelenler için | 🟢 |

### 4.3 Agentic Mode Derinleştirme
| Özellik | Not | Efor |
|---|---|---|
| **Streaming yanıt** (SSE) | Zaten backlog'da; algılanan hızı ciddi artırır | 🟡 |
| **Step-by-step builder + chat'ten PDF upload fix** | İkisi de şu an bozuk — önce bunlar | 🟢 |
| **Gerçek tool-calling'e geçiş** | Şu an: LLM intent seçer → Python elle execute eder. OpenAI function calling ile tek turda çoklu tool zinciri (`"CV'mi analiz et, sonra modern şablona geçir, sonra indir"`) | 🔴 |
| **Semantic router / cache** | Her mesaj LLM'e gidiyor; sık intent'ler için embedding cache maliyeti ve gecikmeyi düşürür (AGENTIC.md'de not düşülmüş) | 🟡 |
| **Agent'in diff göstermesi** | `modify_resume` sonrası "önce/sonra" farkı, onayla/geri al | 🟡 |
| **MCP Server (Faz 3)** | Kullanıcı kendi Claude/ChatGPT'sinden resume'una erişsin. Ürün diferansiyatörü, defterde planı hazır | 🔴 |

### 4.4 Import / Export Genişletme
| Özellik | Not | Efor |
|---|---|---|
| DOCX export | Recruiter'lar sık ister | 🟡 |
| DOCX/TXT import | Şu an sadece PDF | 🟢 |
| Taranmış PDF için OCR | `< 50 karakter` guard'ı şu an sadece hata veriyor | 🟡 |
| Public paylaşım linki (`/r/<slug>`) | Read-only HTML resume, SEO + viral kanal | 🟡 |
| LinkedIn URL'den çekme | Defterde vardı, PDF'e indirgendi. Scraping/API riskleri var | 🔴 |
| **TeX export'un geri dönüşü** (opsiyonel) | Kaldırılma sebebi imaj boyutuydu; ayrı bir microservice/lambda olarak dönebilir. Teknik pozisyonlar için niş talep | 🔴 |

### 4.5 Teknik Borç (feature değil ama kilitleyici)
| İş | Neden şimdi | Efor |
|---|---|---|
| **Sızmış OpenAI key'i iptal et** | Güvenlik | 🟢 |
| **Redis cache** (rate limit + LLM response cache) | Multi-worker'da rate limit şu an etkisiz | 🟢 |
| **Celery + Redis** (import ve toplu enhance async'e) | Timeout riski; 30sn'lik istek Gunicorn worker'ı yiyor | 🔴 |
| `views.py` → `services/` bölünmesi (`import_service`, `enhance_service`) | 1548 satır tek dosya | 🟡 |
| `openai_engine` typed exception'lara geçiş | CLAUDE.md'de zaten "gelecekte düzeltilmeli" diye işaretli | 🟢 |
| `latex_renderer/` + kullanılmayan preview template silinmesi | Ölü kod | 🟢 |
| Boş resume oluşturmaya kota eklenmesi | Kota bypass | 🟢 |
| `views.py` + `openai_engine.py` testleri | Sıfır coverage | 🟡 |
| Tailwind CDN → PostCSS build | Prod performansı | 🟡 |
| Django `gettext`'e geçiş (custom `i18n.py` yerine) | Yeni dil eklemek şu an elle sözlük demek | 🟡 |
| Sentry / error tracking | Prod'da hata görünürlüğü yok | 🟢 |
| Mobile responsive düzeltmeleri | Defterdeki 5 madde hâlâ açık | 🟡 |

### 4.6 Büyüme / Analytics
- Kullanım analitiği (hangi intent kaç kez, hangi şablon seçiliyor, funnel drop-off)
- Onboarding turu (ilk giriş, agentic mode tanıtımı)
- Örnek/demo resume ile giriş yapmadan deneme
- E-posta: "resume'un 30 gündür güncellenmedi" hatırlatması

---

## 5. Önerilen Sıra

**Şimdi (1–2 hafta):** sızmış key iptali → agentic'teki iki bozuk akış (builder, upload) → Redis + rate limit → boş resume kota → ölü kod temizliği.

**Sonra (1 ay):** Job Description Matching + ATS raporu (ürünün en büyük eksik değeri) → ödeme entegrasyonu (kota sistemini gelire bağlar) → streaming yanıt.

**Ardından:** Celery, resume versiyonlama, DOCX export, MCP server.

---

*Bu doküman kod tabanı değiştikçe güncellenmeli. Mimari detay için `.claude/CLAUDE.md`, agentic detay için `.claude/AGENTIC.md`.*
