# UI smoke test — bir kullanıcının yapabildiği her şey

Kapsam: `presales-cleanup` dalı (prod'da henüz yok) → **http://localhost:8000** üzerinde test et.
İşaretler: 💳 = 1 AI kredisi harcar · 📄 = 1 PDF indirme hakkı harcar · ⚠️ = analizde bulunan, bilinen tutarsızlık (gözlemle, hata sayma) · 🔒 = onay kartı çıkar.

Hazırlık: iki tarayıcı profili (ya da biri gizli pencere), elinde bir CV PDF'i, bir LinkedIn "Save to PDF" çıktısı, metin içermeyen (taranmış) bir PDF, bir iş ilanı metni.

---

## A. Ziyaretçi (giriş yapmadan)

- [ ] A1 `/` landing açılıyor; "Pricing" menüsü fiyat sayfasına gidiyor (girişe değil)
- [ ] A2 Landing'deki 14 tasarım kartı görünüyor; footer'da Privacy, Terms, Refund linkleri çalışıyor
- [ ] A3 `/pricing/`: Free / Pro 3 ay $9 / Pro 12 ay $24 kartları, limit tablosu (30 AI kredisi, 5 PDF, 3 CV, 3 ilan kopyası), SSS açılıp kapanıyor
- [ ] A4 Pro kartında "Create a free account to buy" → kayıt sayfası (`?next=/pricing/`)
- [ ] A5 `/terms/`, `/refunds/`, `/privacy/`, `/gizlilik/` açılıyor
- [ ] A6 Olmayan bir adres (`/xyz/`) → hata sayfası (lokalde DEBUG açıksa Django sayfası çıkar; `DEBUG=False` ile "We couldn't find that page")

## B. Hesap

- [ ] B1 E-posta ile kayıt (kullanıcı adı, e-posta, 2 şifre, rıza kutusu); rıza kutusu boşken kayıt reddediliyor
- [ ] B2 Kayıt sonrası `?next=` varsa oraya dönüyor
- [ ] B3 Yeni hesap **agentic** dashboard'a düşüyor; üstte "Confirm your email" bandı var
- [ ] B4 ⚠️ "Doğrulama e-postası gönderildi" bildirimi agentic'te görünmüyor; standarda geçince çıkıyor
- [ ] B5 Banddan "Resend link" (dakikada bir)
- [ ] B6 Doğrulama linki → AI özellikleri açılıyor (lokalde link: `.claude/integrations/google-auth.md`'deki komut)
- [ ] B7 Doğrulamadan **ilk PDF importu çalışıyor**; ikinci AI işlemi (import/sohbet/✨) "confirm your email" ile reddediliyor
- [ ] B8 Çıkış / giriş; "Forgot Password" e-postası (SMTP lokalde çalışmayabilir)
- [ ] B9 Google ile kayıt/giriş (Google anahtarları tanımlıysa); Profil'den Google bağlama

## C. Standart mod — dashboard

- [ ] C1 Header'da Standard/Agentic anahtarı; Standard'a geçiş kartlı listeyi açıyor
- [ ] C2 Boş durumda "Create New Resume" → `/start/` (Start from Scratch / Import PDF)
- [ ] C3 Kart: üzerine gelince "Edit Document"; isim, sahibi, oluşturma/düzenleme tarihi
- [ ] C4 Kopyala (onay modalı) → kopya oluşup editörde açılıyor; 3 CV sınırında reddediliyor
- [ ] C5 📄 Kartta PDF indir; dosya adı "Ad_Soyad" biçiminde (Türkçe karakter ASCII'ye çevrilmiş)
- [ ] C6 Sil (onay modalı) → kart gidiyor, bildirim çıkıyor
- [ ] C7 İlan kopyası olan CV'nin kartında "For job postings" altında kopya + puan; CV ilandan sonra değiştiyse puan "?" ile soluk
- [ ] C8 İlan kopyasına tıkla → editörde açılıyor; kopyayı sil
- [ ] C9 Sağ alttaki Feedback → yıldız + mesaj gönder
- [ ] C10 ⚠️ Dil sürümleri ayrı, birbirinden bağımsız kartlar gibi görünüyor

## D. CV oluşturma / içe aktarma (standart)

- [ ] D1 💳 `/start/` → Import PDF → normal CV → editör açılıyor, alanlar dolu
- [ ] D2 💳 LinkedIn "Save to PDF" çıktısı aynı karttan → otomatik tanınıyor (başlık "LinkedIn — …")
- [ ] D3 PDF'te olmayan değer varsa editörde sarı "import review" bandı; bayrağa tıklayınca ilgili alana gidiyor; değeri düzenleyince bayrak kayboluyor; "Dismiss"
- [ ] D4 Taranmış/metinsiz PDF → 422 "Could not extract enough text"
- [ ] D5 PDF olmayan dosya / 5 MB üstü → anlaşılır hata
- [ ] D6 Start from Scratch → boş editör; kaydedince yeni CV oluşuyor (CV sınırı burada kontrol edilmez)

## E. Editör (iki modda da aynı sayfa)

- [ ] E1 Sol: Details / Template sekmeleri; sağ: canlı önizleme (yazdıktan ~0,7 sn sonra güncelleniyor)
- [ ] E2 Personal: ad, e-posta, telefon, LinkedIn, GitHub, yetenekler; "https://" olmayan link otomatik tamamlanıyor
- [ ] E3 Experience / Education / Projects: ekle, sil, alanları düzenle
- [ ] E4 💳 Deneyim ve projede ✨ (AI iyileştir) → metin yeniden yazılıyor, önizleme güncelleniyor
- [ ] E5 Kredi bitince ✨ → kırmızı bildirim + "See plans" linki; bildirim siz kapatana kadar kalıyor
- [ ] E6 "What I'm working on": kutu işaretli/işaretsiz; işaret kalkınca satırlar silinmiyor, sadece gizleniyor
- [ ] E7 Template sekmesi: aile filtre çipleri, 14 kart, ok tuşlarıyla gezinme, seçince önizleme değişiyor
- [ ] E8 Başlık (resume title) değiştir, Save → "unsaved changes" göstergesi kayboluyor
- [ ] E9 Kaydetmeden sayfadan çıkmaya çalış → tarayıcı uyarısı
- [ ] E10 📄 Download PDF: önizlemeyle aynı tasarım; çok sayfalı CV'de sayfa kırılımları önizlemedeki gibi
- [ ] E11 ⋯ menü → History: sürüm listesi, kelime bazlı diff, "Restore"
- [ ] E12 Mobil genişlikte sağ alttaki "Preview" düğmesi → önizleme modalı
- [ ] E13 ⚠️ Agentic hesapta editör header'ı "Standard"ı seçili gösteriyor; "Dashboard" linki sohbete götürüyor
- [ ] E14 ⚠️ Editörde CV'nin dilini (EN/TR) değiştirecek alan yok

## F. Agentic mod — ekran ve düğmeler

- [ ] F1 Header'dan Agentic'e geç; açık CV varsa sohbette o CV aktif geliyor
- [ ] F2 Boş hesapta sağ panelde "Create a resume / Upload a PDF"
- [ ] F3 💳 ⚠️ Alt çipler: List, My Limits, New Resume, Upload PDF, Help — her biri sohbet mesajı olarak gidiyor ve kredi harcıyor
- [ ] F4 💳 "List" → sohbette CV kartları + sağda liste; karta tıklayınca CV aktif oluyor (kredisiz)
- [ ] F5 Aktif CV çubuğu: "Editing: …", History, × (bırak)
- [ ] F6 Önizleme başlığı: History, Edit (⚠️ yeni sekmede editör), 📄 Download, Refresh
- [ ] F7 Chat / Choose Template sekmeleri: tasarım seç → önizleme yenileniyor (kredisiz); "What I'm working on" anahtarı
- [ ] F8 Sohbet geçmişi sayfa yenileyince duruyor; **başka hesapla girince önceki kişinin sohbeti görünmüyor**
- [ ] F9 Mobil genişlikte Chat / Preview sekmeleri

## G. Agentic mod — sohbetle yapılabilenler (her mesaj 💳)

- [ ] G1 "CV'lerimi listele" → liste
- [ ] G2 "X CV'sinin detaylarını göster" → yetenekler + bölüm sayıları
- [ ] G3 "Önizle" → sağda önizleme
- [ ] G4 📄 "PDF indir" → indirme başlıyor
- [ ] G5 "Formda düzenle" → editör (⚠️ yeni sekme)
- [ ] G6 "Sıfırdan CV hazırlamak istiyorum. Adım …, … şirketinde …" → yeni CV oluşuyor ve **tek adımda** dolduruluyor, onay sorulmuyor; söylemediğin başarı/rakam eklenmiyor
- [ ] G7 "PDF yükle" → dosya seçici kartı → seçince import (LinkedIn otomatik tanınır) — ⚠️ import bayrakları sohbette gösterilmiyor
- [ ] G8 🔒 "Son deneyimime şu maddeyi ekle …" → onay kartı → onayla → önizleme güncelleniyor, "Son değişikliği gör / Geri yükle" çubuğu
- [ ] G9 Aynı istekte "Hayır, iptal" → hiçbir şey değişmiyor
- [ ] G10 🔒 "Tasarımı Modern Sidebar yap" → ⚠️ şablon değişikliği için de onay soruyor
- [ ] G11 "CV'yi kopyala" → kopya oluşuyor
- [ ] G12 🔒 "Bu CV'nin Türkçe sürümünü oluştur" → bağlı dil sürümü; "dil sürümlerini göster"
- [ ] G13 🔒 "Bu CV'yi İngilizceye çevir" (yerinde çeviri) → geçmişten geri alınabilir
- [ ] G14 🔒 "Son değişikliği geri al"
- [ ] G15 🔒 "X CV'sini sil" → onaydan sonra siliniyor; aktifse panel temizleniyor
- [ ] G16 "Limitlerim" → 4 satırlık kullanım paneli (AI kredisi, PDF, CV, ilan kopyası)
- [ ] G17 Kredi bitince mesaj → "Bu ayki 30 AI kredinizi kullandınız…" (LLM'e gitmeden)
- [ ] G18 Profil'de "confirm destructive" kapalıyken G8 onaysız çalışıyor; şüpheli istekte yine de soruyor

## H. İlan değerlendirme (yalnızca agentic)

- [ ] H1 Aktif CV ile "Application Score" → ilan metnini yapıştır → "kopya (önerilen) / ana CV" kartı
- [ ] H2 İlan metnini doğrudan sohbete yapıştır → "ilan gibi görünüyor" teklif çubuğu → değerlendir
- [ ] H3 Değerlendirme paneli: puan, gereksinim satırları (covered / partial / missing), kanıtın yeri
- [ ] H4 Bağlam çubuğu: "CV › kopya · İlan … · puan"
- [ ] H5 Aynı ilanı farklı boşluk/biçimle tekrar yapıştır → aynı kayıt, aynı puan
- [ ] H6 💳 Eksikleri seç → "Improve selected / all" → soru kartı ("bunu nerede yaptın?", "Bunu yapmadım") → taslak kartı (kelime diff, işaretle/düzenle; desteklenmeyen satır işaretsiz gelir) → Apply → puan değişimi ("Kafka: partial → covered")
- [ ] H7 CV'yi değiştirdikten sonra puan otomatik yeniden ölçülüyor
- [ ] H8 Kopyada "Promote" → ana CV kopyanın içeriğiyle değişiyor (ana CV geçmişinde geri yükleme noktası var), kopya kalıyor
- [ ] H9 4. ilan kopyası (ücretsiz plan) → sınır mesajı
- [ ] H10 Standarda geç → kopyalar ana kartın altında puanlarıyla görünüyor (C7)
- [ ] H11 İlan kopyasının dil sürümü istenince reddediliyor

## I. Profil

- [ ] I1 Kullanıcı adı, e-posta, üyelik tarihi
- [ ] I2 "Your plan": Free / Pro (Pro ise bitiş tarihi); "See what Pro includes" → fiyat sayfası
- [ ] I3 "Free plan usage": 4 satır, sayılar G16 ile aynı
- [ ] I4 Arayüz dili EN/TR → tüm arayüz metinleri değişiyor
- [ ] I5 API token: oluştur (bir kez gösterilir), yenile (eskisi geçersiz), iptal
- [ ] I6 Dashboard modu kartı (Standard / Agentic) ve "confirm destructive" anahtarı
- [ ] I7 Şifre değiştir
- [ ] I8 Google hesabı bağla / bağlantıyı gör
- [ ] I9 Hesabı sil (şifreyle) → tüm CV'ler gidiyor, çıkış yapılıyor (en sona bırak)

## J. Fiyat ve ödeme

- [ ] J1 Giriş yapmışken `/pricing/` (ödeme kapalı): "Pro is not on sale yet" + "Tell me when it's ready" → teşekkür bildirimi
- [ ] J2 `/pricing/?paid=1` → "Payment received" bandı
- [ ] J3 (Paddle sandbox kurulunca) Satın al → Paddle penceresi → test kartı → webhook → Pro aktif, Profil'de bitiş tarihi, limitler kalkıyor

## K. Kotalar (ücretsiz plan)

- [ ] K1 💳 30 AI kredisi import + ✨ + sohbet + iyileştirme taslağı arasında ortak düşüyor
- [ ] K2 📄 5. indirmeden sonra 6. reddediliyor (kart, editör, sohbet)
- [ ] K3 3 CV'den sonra import/kopyala reddediliyor; dil sürümleri ve ilan kopyaları sayılmıyor
- [ ] K4 Reddedilen her işlemde mesaj ne olduğunu ve fiyat sayfasını söylüyor

## L. Claude'dan (MCP) — isteğe bağlı

`claude mcp add --transport http resustack http://localhost:8000/mcp --header "Authorization: Bearer <token>"`

- [ ] L1 list_resumes, get_resume, list_templates, check_quota
- [ ] L2 create_resume, update_resume, set_template, set_focus_areas → her birinde preview_url
- [ ] L3 📄 render_pdf → tek kullanımlık link; ikinci açılışta "already been used"
- [ ] L4 evaluate_posting (target=branch/base), list_evaluations
- [ ] L5 Silme aracı yok; iptal edilen token 401 alıyor
