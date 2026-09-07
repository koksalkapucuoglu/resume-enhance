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

## Model

```
Temel CV                    ← kullanıcının 3 hakkı bunlar
 ├── dil sürümü   (tr/en)   ─┐
 └── ilana özel sürüm        ├─ türev: hak yemez
                            ─┘
Başvuru (ilan) ── tam olarak bir CV kullanır
                └─ puan geçmişi: [(tarih, puan, cv_id)]
```

`Resume.translation_of` → `Resume.derived_from` + `Resume.derived_kind`
(`translation` | `tailored`) olarak genelleşir. Her iki türev de aynı
kurallara tabi: kök CV'ye bağlı, kotaya sayılmaz, kök silinince silinir.

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

### 2. Aynı ilan için hep aynı CV

`tailor_resume_for_job` bir başvuru için ikinci kez çalıştığında **yeni CV
açmaz**: o başvuruya bağlı mevcut varyantın içeriğini günceller ve bir
`ResumeRevision` yazar — yani geri alınabilir.

Kaynak her zaman **kök CV**'dir, varyantın kendisi değil. Başlığın birikmesinin
sebebi buydu. Varyant başlığı sabit: `<kök başlık> → <ilan başlığı>`.

### 3. Puan bir ölçüm, bir özellik değil

`JobPosting.score_history`, `[{at, score, resume_id}]` listesi tutar.
`match_score` en son ölçümdür. Düzeltmeden sonra yeniden puanlama, önceki
ölçümle karşılaştırmalı gösterilir: **72 → 85**.

Bu, "yaptığım değişiklik işe yaradı mı" sorusunun tek doğrudan cevabı.

## Etiketler ve gruplar

Etiketler rolleri birbirinden ayırmak içindir. `senior`, `remote`, `developer`
gibi neredeyse her ilanda geçen etiketler hiçbir şey ayırmaz; bunlar daha
belirgin bir etiket varken elenir ve ilan başına en fazla 3 etiket tutulur.
Önceden 5 etiket üretiliyordu ve tek bir ilan beş ayrı grupta aynı CV'yi
gösteriyordu — içgörü değil gürültü.

"Hangi rol için hangi CV" paneli **yalnızca en az iki farklı CV kullanılmışsa**
görünür. Tek CV varken her grup aynı belgeyi adlandırır ve hiçbir soruya cevap
vermez.

## Kota

| | Kotaya sayılır |
|---|---|
| Temel CV | ✅ (`FREE_TIER_LIMITS["resume_count"]`) |
| Dil sürümü | ❌ |
| İlana özel sürüm | ❌ |

Gerekçe: türevler aynı belgenin başka bir biçimi. İki kez saymak tam da hedef
kitleyi — iki dilde iş arayan ve ilana göre uyarlayan kullanıcıyı — cezalandırır.

## Dil

İlan ile CV'nin dili farklıysa agent **sorar**: *"İlan İngilizce, CV'niz
Türkçe. İngilizce bir sürüm oluşturayım mı?"* Onay verilirse dil sürümü açılır
(hak yemez) ve başvuru ona bağlanır. Sessizce çevirmez, sessizce de yok saymaz.

## Mevcut verinin taşınması

Bir migration:
- Aynı kullanıcı + aynı `content_hash` başvuruları tek kayda birleştirir; en
  yüksek puanı ve en yeni durumu korur, diğerlerini siler.
- `translation_of` değerlerini `derived_from` + `derived_kind="translation"`
  alanlarına taşır.
- Başlığı `<X> — <Y> — <Y>` biçiminde birikmiş varyantları tespit edip
  `derived_kind="tailored"` olarak işaretler ve başlığı sadeleştirir.

## Kapsam dışı

- Başvuru başına birden fazla CV denemek (A/B). Şu an gereksiz karmaşıklık.
- Otomatik yeniden puanlama. Ölçüm bir LLM çağrısı; kullanıcı istediğinde çalışır.
