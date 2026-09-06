# Agent Loop — Faz 3 Tasarım Notu

> Karar tarihi: 2026-09-06. `agent_service.py`'ın tek atımlı intent router'dan
> gerçek tool-calling döngüsüne geçişi. Faz sırası için `ROADMAP.md`.

## Sorun

Bugünkü akış tek atım:

```
mesaj → LLM tek intent seçer → Python handler çalışır → cevap döner. BİTTİ.
```

Model tool sonucunu hiç görmez. Sonuçları:

- Çok adımlı iş imkânsız: *"analiz et, düzelt, şablonu değiştir, indir"* → 4 mesaj
- Takip cümleleri çalışmaz: `history` frontend'den geliyor ama **hiç kullanılmıyor**
- Bir tool patlarsa model bilmez, sessizce yanlış cevap verir
- `TOOL_CATALOG` prompt'a JSON string olarak gömülü → parse hatası riski, `max_tokens=400` tavanı

## Hedef döngü

```python
messages = [system, *history, user_msg]      # sabit kısım başta → prompt cache
for step in range(MAX_STEPS):                # 5
    resp = llm(messages, tools=TOOL_SCHEMAS) # native function calling, strict:true
    if not resp.tool_calls:
        return finish(resp.content)          # model bitti dedi
    messages.append(resp)
    for call in resp.tool_calls:
        if TOOLS[call.name].destructive and not approved(call):
            return pause_for_approval(call, messages)
        result = TOOLS[call.name].run(user, **call.args)
        messages.append(tool_message(call.id, result.data))   # ← bugün eksik olan
        ui_effects.extend(result.ui)
return budget_exceeded()
```

---

## Karar 1 — Tool şeması

**Tool = saf fonksiyon.** Girdisi JSON Schema, çıktısı modele geri verilecek veri.

### Ayrım: `data` vs `ui`

Bugünkü `_exec_*` fonksiyonları UI cevabı döndürüyor (`{"type": "preview", "resume_id": 23}`).
Döngüde bu modele geri gidecek ve model `type: preview` ile ne yapacağını bilemez.
İkisi ayrılır:

```python
@dataclass
class ToolResult:
    data: dict          # modelin bağlamına girer — bir sonraki adımda kullanabilir
    ui: list = ()       # frontend'e giden efektler; model bunu hiç görmez
```

```python
@tool(
    name="switch_template",
    description="Change which template a resume renders with",
    destructive=True,
    params={
        "resume_id": {"type": "integer"},
        "template": {"type": "string", "enum": ["faangpath-simple", "modern-sidebar"]},
    },
)
def switch_template(user, resume_id, template) -> ToolResult:
    ...
    return ToolResult(
        data={"ok": True, "resume_id": resume_id, "template": template},
        ui=[{"effect": "refresh_preview", "resume_id": resume_id}],
    )
```

`ui` efektleri döngü boyunca toplanır, yanıtın sonunda tek listede frontend'e gider.

### Diğer kararlar

| Konu | Karar | Gerekçe |
|---|---|---|
| Şema zorlama | `strict: true` structured outputs | Model bozuk arg üretemez; `modify_resume`'daki retry hack'i kalkar |
| Kayıt | Decorator + registry | Elle `handler_map` yok; tool eklemek = fonksiyon yazmak |
| Tool sayısı | 19 → ~15 | Sağlayıcı rehberleri 20 üstünde seçim doğruluğunun düştüğünü söylüyor |
| Birleşecekler | `upload_resume` + `upload_linkedin` | Tek tool + `source` parametresi |
| Tool olmaktan çıkacaklar | `help`, `clarify` | Bunlar döngünün doğal çıkışı — model tool çağırmadan cevap verir |
| Hata | Tool exception fırlatmaz, `ToolResult(data={"error": ...})` döner | Model hatayı görür ve toparlar |

---

## Karar 2 — Onay sözleşmesi

**Interrupt-and-resume, baştan başlatma yok.** Döngü durur, bekleyen tool call saklanır,
onay gelince o call çalışır ve döngü aynı mesaj listesiyle kaldığı yerden devam eder.

```
… → model tool_call(delete_resume, {id: 23}) üretir
    → destructive, onay yok → DUR
    → {type: "confirm", token: "<nonce>", tool: "delete_resume", args: {...}} döner
    → kullanıcı onaylar → POST /agent/approve/ {token}
    → cache'ten mesaj listesi + pending call okunur
    → call çalışır, tool_message eklenir, döngü devam eder
```

| Konu | Karar | Gerekçe |
|---|---|---|
| **Bekleyen state nerede** | Sunucu cache, `user_id + nonce` anahtarı, 5 dk TTL | Client'ta **olamaz** — kullanıcı `destructive` bayrağını veya arg'ları değiştirebilir. Bugün `history` client'tan geliyor; bekleyen tool call asla gelemez |
| **Reddedilirse** | Sentetik tool result: `{"denied_by_user": true}` | Her `tool_call`'ın eşleşen `tool` mesajı olmalı; atlarsan mesaj listesi geçersiz. Böylece model reddi görür, alternatif önerir |
| **Çift tıklama** | Onay token'ı tek kullanımlık | Yoksa iki kere siler |
| **Destructive tool'lar** | `delete_resume`, `modify_resume`, `switch_template`, `translate_resume`, `revert` | Hepsi Faz 2'de snapshot yazıyor, yani onay + geri alma iki katmanlı koruma |

> Not: `history` client'tan gelmeye devam edecek (chat transcript'i) ama **veri** olarak
> ele alınacak — içindeki hiçbir şey tool çalıştırma yetkisi taşımaz.

---

## Karar 3 — Kota ve maliyet

**İki katman: kullanıcıya mesaj, sisteme token.**

### Kullanıcıya görünen
1 kullanıcı mesajı = 1 birim, döngü kaç adım sürerse sürsün.
`FREE_TIER_LIMITS["agent_message_count"]` olduğu gibi kalır.

> **Önceki "tur başına sayım" önerisi iptal.** Kullanıcı kaç adım süreceğini tahmin
> edemez; ajan verimsiz çalıştığında cezayı o çeker. Maliyet problemini kullanıcıya
> yıkmak kötü teşvik.

### Sisteme görünen (kullanıcıya gösterilmez)
- `MAX_STEPS = 5`
- İstek başına tool call tavanı
- İstek başına token bütçesi

Aşılırsa döngü nazikçe kesilir: *"Bu isteği tamamlayamadım, daha küçük parçalara böler misin?"*

### Ölçüm
`response.usage` zaten loglanıyor. Tur başına toplanıp DB'ye yazılacak —
fiyatlandırma tahminle değil gerçek veriyle yapılacak.

### Prompt caching
System prompt + tool tanımları sabit. Mesaj listesi **sabit kısım başta** olacak
şekilde kurulacak; sağlayıcı otomatik cache'ler, input maliyeti düşer.
Eşik ve indirim oranı hesap üzerinden doğrulanmalı.

---

## Kapsam

**Girenler**
- [ ] Tool registry + decorator + `ToolResult`
- [ ] `_exec_*` → tool'a taşıma (iç mantık aynı, imza/dönüş standartlaşır)
- [ ] Döngü + `MAX_STEPS` + token bütçesi
- [ ] `history` gerçekten kullanılsın
- [ ] Onay akışı: `POST /agent/approve/`, cache'li pending state, tek kullanımlık token
- [ ] Streaming (SSE): token akışı + "şu an X yapıyorum" adım göstergesi
- [ ] Açık bug'lar: agentic step-by-step builder, chat'ten PDF upload
- [ ] Editör header'ının toparlanması (Faz 3'ün UI işiyle birlikte — bkz. ROADMAP notu)
- [ ] Testlerin yeni sözleşmeye uyarlanması

**Girmeyenler**
- Pro retention tavanı → Faz 5
- JD matching tool'ları → Faz 4
- MCP server → sonrası

## Dokunulacak dosyalar

| Dosya | Değişiklik |
|---|---|
| `resume/services/agent_service.py` | 1290 satır → döngü + ince orkestrasyon (~400) |
| `resume/services/agent_tools.py` | **yeni** — registry, decorator, `ToolResult`, tool'lar |
| `resume/views.py` | `agent_chat` (SSE), yeni `agent_approve` |
| `resume/urls.py` | `agent/approve/` |
| `resume/templates/resume/dashboard_agentic.html` | Streaming, adım göstergesi, onay diyaloğu, `ui` efekt uygulayıcı |
| `resume/tests/test_agent_service.py` | Yeni sözleşme |
