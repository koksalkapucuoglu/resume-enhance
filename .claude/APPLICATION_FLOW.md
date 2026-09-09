# Başvuru Akışı — Tasarım Notu

> Karar tarihi: 2026-09-06. Faz 4'te kurulan ilan eşleştirme akışının yeniden
> temellendirilmesi. Önceki fazlar için `ROADMAP.md`.

## Sorun

Faz 4'te her eylem yeni bir kayıt üretiyordu:

| Eylem | Sonuç | Olması gereken |
|---|---|---|
| Aynı ilanı ikinci kez yapıştır | 2. `JobPosting` | Mevcut olan güncellenir |
| Aynı ilan için ikinci kez "düzelt" | 2. `Resume`, başlık birikir | Mevcut varyant güncellenir |
| Düzeltme sonrası "iyileşti mi?" | Cevap yok | Puan geçmişi: 72 → 85 |

Gerçek kullanımda tek bir ilan için 7 başvuru kaydı ve bir grupta 4-5 CV
oluştu; başlık `Main — Senior Python Developer — Senior Python Developer`
şeklinde üst üste bindi. Kullanıcı ekrana bakıp ne olduğunu anlayamıyor.

Kota da tutarsızdı: çeviri kopyası hak yemiyordu ama ilana özel varyant
normal CV sayılıyordu.

## Model (2026-09-09 revizyonu — snapshot)

```
Temel CV                    ← kullanıcının 3 hakkı bunlar
 └── dil sürümü  (tr/en)    ← türev, hak yemez

Başvuru (ilan)
 ├── ilan metni + parmak izi
 ├── CV SNAPSHOT            ← gönderilen belgenin dondurulmuş hali
 │                             salt okunur, CV listesinde görünmez, hak yemez
 ├── puan + puan geçmişi
 └── kaynak temel CV (bağ)  ← gruplama bunun üzerinden yapılır
```

**Neden snapshot.** Bir başvurunun cevaplaması gereken soru *"onlara ne
gönderdim"*. Canlı bir CV referansı bunu cevaplayamaz: ikinci ilana göre CV'yi
düzeltince birinci başvurunun kaydı artık göndermediğin bir belgeyi işaret eder.
Fatura satırlarının o günkü fiyatı dondurması gibi.

**`derived_kind="tailored"` kaldırıldı.** İlana özel sürüm artık bir `Resume`
satırı değil, başvurunun içindeki snapshot. CV listende yalnızca temel CV'ler ve
dil sürümleri var — liste ilan sayısıyla şişmiyor.

**Snapshot düzenlenemez.** "Bu CV'yi düzenle" denince salt okunur olduğu söylenir
ve klonlama için onay istenir. Klon **yeni bir temel CV**'dir ve kotaya sayılır:
serbestçe düzenlenebilen her belge kotaya girmeli, yoksa klonlama limiti delme
yolu olur. Onay hem standart hem agentic modda sorulur.

**Etiketler kaldırıldı.** "Hangi rol için hangi CV" sorusu kaynak temel CV
üzerinden cevaplanıyor — bu bir tahmin değil, olgu. LLM'in ürettiği etiketler
çoğu ilanda jenerikti (`senior`, `remote`) ve tek bir ilan aynı CV'yi beş ayrı
grupta gösteriyordu.

## Üç kural

### 1. Aynı ilan hep aynı kayıt

İlan metninin normalize edilmiş halinden bir parmak izi üretilir
(`content_hash`). Aynı parmak izi + aynı kullanıcı → mevcut başvuru
güncellenir, yenisi açılmaz.

Parmak izi metinden üretilir çünkü kullanıcı ilanı kopyalarken başlık/şirket
alanları değişebilir ama gövde aynıdır.

**Yakın kopya: sessizce karar verme, sor.** Metin farklı ama aynı şirkette aynı
unvanlı bir başvuru varsa iki ihtimal vardır — kullanıcı aynı ilanı biraz farklı
yapıştırmıştır, ya da gerçekten ikinci bir açılış vardır. Her iki tahmin de bir
şey kaybettirir, o yüzden `match_job` kaydetmeden sorar ve `apply_to` ile geri
çağrılır (`"new"` veya güncellenecek başvurunun id'si). Analiz kısa süre
cache'lenir, böylece kullanıcının cevabı ikinci bir LLM çağrısına mal olmaz —
ama cache **yalnızca** cevabı uygularken okunur, yeniden ölçümde asla.

### 2. Aynı ilan için hep aynı snapshot

`tailor_resume_for_job` bir başvuru için ikinci kez çalıştığında **yeni CV
açmaz**: o başvurunun snapshot'ını yeniden yazar. Kaynak her zaman **temel
CV**'dir, önceki snapshot değil — başlığın birikmesinin sebebi buydu.

### 3. Puan bir ölçüm, bir özellik değil

`JobPosting.score_history`, `[{at, score, resume_id}]` listesi tutar.
`match_score` en son ölçümdür. Düzeltmeden sonra yeniden puanlama, önceki
ölçümle karşılaştırmalı gösterilir: **72 → 85**.

Bu, "yaptığım değişiklik işe yaradı mı" sorusunun tek doğrudan cevabı.

## Kota

| | Kotaya sayılır |
|---|---|
| Temel CV | ✅ `resume_count` = 3 |
| Dil sürümü | ❌ |
| CV snapshot (başvuru içi) | ❌ |
| Snapshot klonu | ✅ — yeni temel CV |
| Başvuru sayısı | ✅ `application_count` = 3 (ücretsiz), Pro sınırsız |

**Paywall özellikte değil, hakta.** İlan eşleştirme ve başvuru takibi herkese
açık; ücretsiz planda 3 başvurudan sonra duruyor. Özelliği tamamen saklamak
kimsenin ne için ödeyeceğini görmemesi demekti — birkaç kullanımdan sonra
bitmesi aynı paywall, ama kullanıcının değeri hissettiği yere konmuş hali.

## Gruplar

Başvurular kaynak temel CV'ye göre gruplanır:

```
Main CV   → 5 başvuru: Shakers, Beta, Gamma…
C++ CV    → 3 başvuru: Delta, Epsilon…
```

Panel **yalnızca en az iki farklı temel CV kullanılmışsa** görünür. Tek CV varken
her grup aynı belgeyi adlandırır ve hiçbir soruya cevap vermez.

## Dil

İlan ile CV'nin dili farklıysa agent **sorar**: *"İlan İngilizce, CV'niz
Türkçe. İngilizce bir sürüm oluşturayım mı?"* Onay verilirse dil sürümü açılır
(hak yemez) ve başvuru ona bağlanır. Sessizce çevirmez, sessizce de yok saymaz.

## Mevcut verinin taşınması

`0018` aynı ilanın kopyalarını zaten birleştirdi. Snapshot geçişi:

- Her başvuru için, bağlı CV'nin o anki içeriği snapshot olarak dondurulur.
- `derived_kind="tailored"` CV'lerden **bir başvuruya bağlı olanlar** snapshot'a
  dönüşüp silinir — artık CV listesinde yer almamaları gerekiyor.
- Bağlı başvurusu olmayan `tailored` CV'ler **temel CV'ye** yükseltilir; sessizce
  silmek kullanıcının üzerinde çalıştığı bir belgeyi yok etmek olurdu. Bu,
  kotayı geçici olarak aşabilir; mevcut kayıtlar korunur, yeni oluşturma engellenir.
- `tags` alanı kaldırılır.

## Kapsam dışı

- Başvuru başına birden fazla CV denemek (A/B). Şu an gereksiz karmaşıklık.
- Otomatik yeniden puanlama. Ölçüm bir LLM çağrısı; kullanıcı istediğinde çalışır.
