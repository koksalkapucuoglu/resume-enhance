# ResuStack — Yol Haritası ve Yetenek Takibi

> **Canlı doküman.** Her faz bitiminde "Kullanıcı Ne Yapabiliyor" bölümü güncellenir.
> Karar gerekçeleri: fiyatlandırma → tek seferlik ödeme, paywall iş akışında (bkz. `PRODUCT.md`).
> Son güncelleme: 2026-09-06 — **Faz 1 ve Faz 2 tamamlandı.**

---

## Faz Sırası

| # | Faz | Amaç | Ücret ilişkisi |
|---|---|---|---|
| 1 | ✅ Navigasyon & mod bütünlüğü | İki modu tek ürün gibi hissettirmek | — |
| 2 | ✅ Revizyon: diff / geri alma | AI'ya güven | **Free** |
| 3 | Agent loop | Çok adımlı işi tek mesajda bitirmek | — (retention) |
| 4 | JD matching + ilan↔CV mapping | İş arama iş akışı | **Paywall burada** |
| 5 | Tek seferlik ödeme | Gelir | — |
| — | TR nişi | Ertelendi, 1-5 sonrası konuşulacak | — |

---

## Faz 1 — Navigasyon & Mod Bütünlüğü

**Sorun (kodda doğrulandı):**
- `resume_form.html` içinde tek bir agentic referansı yok → editöre giren kullanıcı agentic'e dönemiyor
- Agentic'ten Standard'a geçişte `active_resume` kayboluyor, düz listeye düşülüyor
- `UserProfile.ui_mode` default `standard`; hedef: seçim yapılmamışsa **agentic**

**İş kalemleri:**
- [x] `ui_mode` üç durumlu: `null` (seçilmemiş → `UI_MODE_DEFAULT` = agentic) / `standard` / `agentic`. `resolved_ui_mode` property'si; migration `0009_ui_mode_nullable` açık seçimleri korur
- [x] Editör header'ına mod toggle'ı — dashboard'daki bileşenin aynısı
- [x] Mod geçişinde bağlam taşı: `toggle_agent_mode` artık `resume_id` alıp `redirect_url` döndürüyor (IDOR'a karşı `user=request.user` filtreli)
- [x] Agentic dashboard deep-link: `/dashboard/?resume=<pk>` → `INITIAL_RESUME`, localStorage'ı ezer
- [x] Boş durum: `context-empty` paneli — "No resumes yet" + Create / Upload butonları; ilk render sunucu tarafında seçiliyor (flash yok)
- [x] `FREE_TIER_LIMITS["resume_count"]` = 3 teyit edildi (değişiklik gerekmedi)

**Doğrulama:** 79 test geçiyor. Ek olarak elle doğrulandı — yeni kullanıcı agentic açılıyor; mevcut `standard` seçimi korunuyor; editör→agentic→editör round trip aynı belgede kalıyor (`/form/33/` ↔ `/dashboard/?resume=33`); başkasının resume id'si sessizce yok sayılıyor; resume'u olmayan kullanıcıda boş durum paneli render ediliyor.

**Dokunulacak:** `resume/models.py`, yeni migration, `resume/views.py` (`DashboardView`, `ResumeFormView`, `toggle_agent_mode`), `resume_form.html`, `dashboard.html`, `dashboard_agentic.html`

### ✅ Faz 1 sonunda kullanıcı ne yapabiliyor *(canlı — 2026-09-06)*
- Yeni kullanıcı ilk girişte **agentic modda** açılır; istediği an sayfa üzerinden standarda geçer, seçimi hatırlanır
- **Her sayfadan** (dashboard *ve* editör) iki mod arasında geçebilir
- Mod değiştirdiğinde **üzerinde çalıştığı resume kaybolmaz** — aynı belge diğer görünümde açılır
- Hiç resume'u yokken agentic modda kafası karışmaz: boş önizleme + ne yapacağını söyleyen çipler
- Serbest planda **3** resume tutar

---

## Faz 2 — Revizyon: Değişikliği Gör / Karşılaştır / Geri Al

**Neden 3'ten önce:** Agent loop tek turda birden çok değişiklik yapacak. Kullanıcının "ne yaptı bu" diyebilmesi loop'un ön koşulu.

```python
class ResumeRevision(models.Model):
    resume     = FK(Resume, related_name="revisions")
    content    = JSONField()   # değişiklikten ÖNCEKİ snapshot
    source     = CharField()   # 'agent' | 'ai_enhance' | 'manual' | 'import'
    summary    = TextField()   # "3 skill eklendi, son deneyim yeniden yazıldı"
    tool_name  = CharField(blank=True)   # Faz 3'te dolar
    created_at = DateTimeField(auto_now_add=True)
```

**İş kalemleri:**
- [x] `ResumeRevision` modeli + migration `0010_resumerevision` (content **ve** `template_selector` snapshot'lanır)
- [x] `services/revision_service.py` — `snapshot()` / `prune()` / `restore()` / `history()`
- [x] `services/diff_service.py` — alan bazlı önce/sonra; liste bölümleri konuma göre değil **kimliğe göre** eşleşiyor (sıralama değişikliği fark sayılmıyor)
- [x] Beş yazma noktasına snapshot: `ResumeFormView.post` (manual), `modify_resume` ×2 (ilk deneme + retry), `translate_resume`, `switch_template`
- [x] 4 endpoint: `revisions/`, `revisions/<id>/diff/`, `revisions/latest/diff/`, `revert/<id>/`
- [x] Standart mod: editör header'ında "Geçmiş" → modal, her revizyonda "Değişiklikleri gör" (açılır diff) + "Geri yükle"
- [x] Agentic mod: modify/switch_template yanıtının altında "Son değişikliği gör" + "Geri yükle" çipleri; sağ panelde diff görünümü
- [x] Geri alma kendisi de revizyon yazıyor → geri almanın geri alınması çalışıyor
- [x] Saklama: `FREE_TIER_LIMITS["revision_history"] = 5`, Pro sınırsız

**Not:** `enhance_experience` / `enhance_project` DB'ye yazmıyor — sadece HTMX fragment döndürüyor, içerik ancak form kaydedilince kalıcı oluyor. Bu yüzden snapshot manuel kayıt yolunda alınıyor, enhance view'larında değil.

**Doğrulama:** 105 test geçiyor (26'sı yeni: `test_revisions.py` — servis, diff, endpoint, IDOR, retention). Tarayıcıda uçtan uca: editörde geçmiş modalı → diff ("1 added, 2 changed", Full Name / Skills / Experience Description satırları) → Geri yükle → form geri yüklenmiş içerikle açılıyor; agentic panelde aynı diff + geri yükleme, sonrasında yeni geri-alma noktası oluşuyor.

**Dokunulacak:** `resume/models.py`, yeni migration, `resume/services/diff_service.py`, `agent_service.py`, `views.py`, iki dashboard + editör template'i

### ✅ Faz 2 sonunda kullanıcı ne yapabiliyor *(canlı — 2026-09-06)*
- Faz 1'in tamamı, **artı:**
- Her AI değişikliğinden sonra **tam olarak neyin değiştiğini** alan bazlı önce/sonra olarak görür
- Beğenmediği değişikliği **tek tıkla geri alır** — hem agentic hem standart modda
- Geçmişteki herhangi bir sürüme döner (free: son 5)
- AI'a artık "denesin, beğenmezsem geri alırım" diyerek güvenle iş verir

---

## Faz 3 — Agent Loop (2026 mimarisi)

**Bugünkü durum:** `classify_intent` → tek intent → tek Python handler → bitti. LLM tool sonucunu **hiç görmüyor**; frontend'in gönderdiği `history` **hiç kullanılmıyor**.

**Hedef:**
```python
messages = [system, *history, user_msg]
for _ in range(MAX_STEPS):              # 5
    resp = llm(messages, tools=TOOLS)   # native function calling
    if not resp.tool_calls:
        return resp.content
    for call in resp.tool_calls:
        if TOOLS[call.name].destructive and not approved(call):
            return {"type": "confirm", ...}
        result = TOOLS[call.name].run(user, **call.args)
        messages.append(tool_result(call.id, result))   # ← bugün eksik olan
```

**İş kalemleri:**
- [ ] `TOOL_CATALOG` → native function-calling şemasına (JSON Schema params, `destructive` bayrağı)
- [ ] `_exec_*` fonksiyonları tool registry'ye — **iç mantık değişmiyor**, sadece imza + dönüş tipi standartlaşıyor
- [ ] Döngü + `MAX_STEPS` + adım/token bütçesi
- [ ] `history` gerçekten kullanılsın → "onu da sil", "bir tane daha ekle" çalışsın
- [ ] Yıkıcı tool'larda onay akışı (`delete_resume`, `modify_resume`, `switch_template`, `revert`)
- [ ] Streaming (SSE) — hem token hem "şu an X yapıyorum" adım göstergesi
- [ ] Açık bug'lar: agentic'te step-by-step builder + chat'ten PDF upload
- [ ] Kota: mesaj başına değil **tur başına** sayım (loop token'ı ~3-4x'liyor)
- [ ] Testleri yeni sözleşmeye uyarla

**Dokunulacak:** `agent_service.py` (1290 → ~400 satır + tool modülleri), `views.py:agent_chat`, `dashboard_agentic.html`, `test_agent_service.py`

### ✅ Faz 3 sonunda kullanıcı ne yapabiliyor
- Faz 1-2'nin tamamı, **artı:**
- **Tek cümlede çok adımlı iş:** *"CV'mi analiz et, zayıf maddeleri düzelt, modern şablona geçir ve indir"* → 4 mesaj değil, 1 mesaj
- Takip cümleleri çalışır: *"onu da kaldır"*, *"bir tane daha ekle"*, *"az önceki gibi ama daha kısa"*
- Cevabın yazılışını canlı görür; agent'ın hangi adımda olduğunu takip eder
- Geri alınamaz işlemlerden önce **onay sorulur**
- Agentic modda step-by-step yeni CV oluşturma ve **chat'ten PDF yükleme** çalışır
- Bir tool patlarsa agent bunu görüp toparlar / açıklar (bugün sessizce yanlış cevap veriyor)

---

## Faz 4 — JD Matching + İlan↔CV Mapping *(paywall burada)*

```python
class JobPosting(models.Model):
    user        = FK(User, related_name="job_postings")
    title       = CharField()          # "Senior Python Developer"
    company     = CharField(blank=True)
    url         = URLField(blank=True)
    description = TextField()          # yapıştırılan ilan metni
    tags        = JSONField(default=list)   # ["python","django"] — gruplama
    resume      = FK(Resume, null=True, related_name="job_postings")  # hangi CV ile başvuruldu
    status      = CharField()          # saved | applied | interview | rejected | offer
    match_score = IntegerField(null=True)
    created_at  = DateTimeField(auto_now_add=True)
```

**İş kalemleri:**
- [ ] `JobPosting` modeli + migration
- [ ] Yeni tool'lar: `match_job` (skor + eksik keyword), `tailor_resume_for_job` (ilana özel varyant üret), `list_jobs`, `link_resume_to_job`
- [ ] "CV grupları" görünümü: hangi CV hangi etiketlerdeki ilanlarda kullanılıyor — *Python işleri → CV1, C++ işleri → CV2*
- [ ] Agentic'te ilan yapıştır → skor + eksikler + "bu ilana özel varyant üret" çipi
- [ ] Standart modda ilan takip tablosu
- [ ] Premium kapısı: JD matching + tailoring + ilan takibi

### ✅ Faz 4 sonunda kullanıcı ne yapabiliyor
- Faz 1-3'ün tamamı, **artı:**
- İlan metnini yapıştırıp **eşleşme skoru + eksik keyword listesi** alır
- *"Bu ilana özel bir varyant üret"* der; base CV'sinden türev CV çıkar, base bozulmaz
- Başvurduğu ilanları kaydeder, durumunu takip eder (kaydedildi → başvuruldu → mülakat → …)
- **Hangi ilan için hangi CV'yi kullandığını** görür; agentic'e *"C++ işleri için hangi CV'yi kullanıyorum"* diye sorar
- CV'lerini ilan etiketlerine göre gruplanmış görür

---

## Faz 5 — Tek Seferlik Ödeme

**Model:** abonelik **yok**. Tek seferlik paket (ör. "3 ay sınırsız" ya da kredi paketi).

**İş kalemleri:**
- [ ] Sağlayıcı seçimi (LemonSqueezy / Paddle / Iyzico) — TR kartı desteği belirleyici
- [ ] Checkout + webhook → `UserProfile.tier` + bitiş tarihi
- [ ] `UserProfile`: `premium_until` alanı; `is_pro()` tarihe baksın
- [ ] Fiyatlandırma sayfası + profilde satın alma durumu
- [ ] Kota aşımında premium çağrısı (kaba değil, bağlamsal)

### ✅ Faz 5 sonunda kullanıcı ne yapabiliyor
- Faz 1-4'ün tamamı, **artı:**
- **Tek seferlik ödemeyle** premium açar: sınırsız CV / import / enhance / indirme, sınırsız revizyon geçmişi, JD matching + ilan takibi
- Aboneliğe hapsolmaz — süre biter, veri kalır, tekrar ödemeye zorlanmaz

---

## Hedef Freemium Tablosu (Faz 5 sonunda)

| Yetenek | Free | Premium |
|---|---|---|
| Resume tutma | 3 adet | Sınırsız |
| PDF / LinkedIn import | 2/ay | Sınırsız |
| AI enhance | 10/ay | Sınırsız |
| PDF indirme | 5/ay | Sınırsız |
| Agent mesajı | 10/ay | Sınırsız |
| İki mod (standart + agentic) | ✅ | ✅ |
| Revizyon: diff + geri alma | ✅ son 5 | ✅ sınırsız |
| Feedback | ✅ | ✅ |
| **JD matching + tailoring** | ❌ | ✅ |
| **İlan takibi + CV grupları** | ❌ | ✅ |

---

## Açık Kararlar

| Konu | Seçenekler | Tavsiyem |
|---|---|---|
| ~~Free resume limiti~~ | ~~3 / 5~~ | ✅ **3** seçildi (2026-09-06) |
| ~~Revizyon saklama (free)~~ | ~~son 3 / 5 / süresiz~~ | ✅ **Son 5** (`FREE_TIER_LIMITS["revision_history"]`) |
| ~~`ui_mode` mevcut kullanıcılar~~ | ~~hepsi agentic'e / açık seçim korunsun~~ | ✅ Açık seçim korunuyor; `null` = seçilmemiş → agentic |
| Premium süresi | 1 ay / 3 ay / 6 ay | **3 ay.** İş arama döngüsünün ortalama uzunluğu. |
