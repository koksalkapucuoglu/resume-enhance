# Ödeme Kurulumu

> Tek seferlik satın alma, abonelik yok. Gerekçe: `PRODUCT.md` ve
> `resume/services/payment_service.py` docstring'i.

## Kod tarafı (bitti)

| Parça | Yer |
|---|---|
| `UserProfile.premium_until` + `grant_premium()` | `resume/models.py` |
| `Purchase` — denetim kaydı + webhook idempotency | `resume/models.py` |
| Sağlayıcı adaptörü, plan kataloğu, erişim verme | `resume/services/payment_service.py` |
| `/pricing/`, `/checkout/<plan>/`, `/webhooks/payments/` | `resume/views.py`, `resume/urls.py` |
| Planlar ve sağlayıcı ayarları | `core/settings.py` → `PREMIUM_PLANS`, `PAYMENTS` |

`is_pro()` iki kaynağa bakar: `tier == "pro"` (admin'in elle verdiği, süresiz)
**veya** `premium_until` gelecekte (satın alınan süre). Süre dolunca hesap
ücretsiz plana düşer, veri durur.

## Şu anki durum: `coming_soon`

`PAYMENT_STATUS` varsayılan olarak `coming_soon`. Bu haldeyken:

- `/pricing/` planları ve fiyatları **gösteriyor** (Pro'nun ne kadar olacağı
  yine de yararlı bilgi), üstünde "Pro henüz satışta değil" bandı var
- Satın alma butonu yerine **"Hazır olunca haber ver"** — tıklayan kişi
  `Feedback` kaydına `page="pricing"` ile düşüyor. Hangi planın istendiği
  sağlayıcı ve fiyat kararının en iyi girdisi
- Elle POST atılsa bile `/checkout/<plan>/` reddediyor
- `/webhooks/payments/` **503** dönüyor — henüz kimse çağırmıyor, gelen şey
  varsa da işlem yapılmamalı
- Admin panelinden `tier="pro"` yaparak erişim vermek çalışmaya devam ediyor.
  Kapalı olan ödeme, erişim değil

Satışa açmak için: aşağıdaki adımları tamamla, sonra `PAYMENT_STATUS=live`.

## Yapılması gerekenler (senin yapman gereken kısım)

Hesap açma ve ödeme bilgisi girme işlemlerini ben yapamam — bunlar sende.

1. **LemonSqueezy hesabı aç** ve mağazanı oluştur.
   Merchant of record olarak çalışır, yani KDV/VAT yükümlülüğünü o üstlenir —
   tek kişilik bir operasyonun AB ve Türkiye'ye satış yaparken en çok
   zorlanacağı kısım bu.
2. **İki ürün oluştur** (tek seferlik ödeme, abonelik değil):
   - Pro · 3 ay → $9
   - Pro · 12 ay → $24
3. Her ürünün **hosted checkout link**'ini kopyala.
4. **Webhook ekle**: `https://<alan-adin>/webhooks/payments/`, event olarak
   `order_created`. Ürettiği **signing secret**'ı kopyala.
5. `.env.prod`'a şunları ekle:

```
PAYMENT_STATUS=live
PAYMENT_PROVIDER=lemonsqueezy
PAYMENT_WEBHOOK_SECRET=<signing secret>
CHECKOUT_URL_PRO_3M=<3 aylık checkout link>
CHECKOUT_URL_PRO_12M=<12 aylık checkout link>
PRODUCT_ID_PRO_3M=<opsiyonel: ürün adı/id>
PRODUCT_ID_PRO_12M=<opsiyonel>
```

`CHECKOUT_URL_*` boşken fiyat kartı "Checkout is not set up yet" gösterir —
yarım kurulumla kimse ödeme ekranına düşmez.

6. **Test modunda bir satın alma yap**, `/pricing/` sayfasında sürenin
   göründüğünü doğrula.

## Doğrulanma durumu

Webhook akışı 35 testle kapsanıyor (`resume/tests/test_payments.py`): imza
doğrulama, kurcalanmış gövde, tekrar teslimat, bilinmeyen kullanıcı, süre
uzatma, süre dolması ve satın alınan erişimin kotaları/pro tool'ları açması.
Testler view'ı Django test client üzerinden gerçekten çağırıyor.

**Gerçek bir ödeme denenmedi** — bunun için canlı hesap ve kart gerekiyor.
Yukarıdaki 6. adım o boşluğu kapatır.

## Sağlayıcı değiştirmek

`payment_service.py` içinde `LemonSqueezyProvider` gibi bir sınıf yaz
(`checkout_url`, `verify`, `parse` metodları), `_PROVIDERS`'a ekle,
`PAYMENT_PROVIDER` env değişkenini değiştir. Başka hiçbir yer sağlayıcıyı
bilmiyor.

**Iyzico notu:** TR kartları ve TL fiyatlandırma için daha uygun olabilir ama
merchant of record değil — vergi yükümlülüğü sende kalır. TR nişine
geçildiğinde tekrar değerlendirilmeli.

## Sağlayıcı ararken

**"Şirketim yok" tek başına engel olmayabilir.** LemonSqueezy ve Paddle gibi
merchant-of-record sağlayıcılar birçok ülkede şahıs olarak hesap açtırıyor —
satış argümanlarından biri bu. Ancak ülke uygunluğu sağlayıcıdan sağlayıcıya
değişiyor; Türkiye'den şahıs başvurusunun kabul edilip edilmediğini başvurmadan
önce doğrula. Bakılacaklar:

- Şahıs (sole proprietor / individual) başvurusu kabul ediliyor mu
- Türkiye desteklenen ülkeler listesinde mi
- Ödeme çıkışı (payout) nasıl geliyor — banka havalesi, Wise, Payoneer
- Komisyon: merchant of record tipik olarak daha yüksek keser ama KDV/VAT
  yükümlülüğünü üstlenir

Waitlist kayıtları biriktikçe hangi planın istendiğini görebilirsin:

```bash
docker compose exec web python manage.py shell -c "from resume.models import Feedback; [print(f.created_at, f.user, f.message) for f in Feedback.objects.filter(page='pricing')]"
```
