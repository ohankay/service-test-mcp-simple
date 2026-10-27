# service-test-mcp-simple (`service-test-mcp`)

**WCF/SOAP ve REST servislerini test etmek** için MCP sunucusu. VS Code'da GitHub Copilot (Agent modu, custom agent'lar) ile ya da VS Code'dan bağımsız herhangi bir MCP istemcisi / AI ajanıyla kullanılır. [sql-mcp-simple](https://github.com/ohankay/sql-mcp-simple) ve [mongo-mcp-simple](https://github.com/ohankay/mongo-mcp-simple) ile aynı yaklaşım: ajan servis çağrılarını PowerShell / `Invoke-WebRequest` / curl çalıştırmadan, doğrudan bu sunucunun araçlarıyla yapar.

| | |
|---|---|
| SOAP / WCF | WSDL keşfi, operasyon şeması ve örnek parametre, çağrı, SOAP fault (code/message/detail), ham zarf (isteğe bağlı). SOAP 1.1/1.2, `basicHttpBinding`, mesaj güvenliksiz `wsHttpBinding`, WS-Addressing |
| REST | Tüm HTTP metotları, JSON/metin/form gövde, OpenAPI/Swagger keşfi |
| Kimlik doğrulama | **Windows oturumu** (Kerberos/NTLM, şifresiz), NTLM, basic, bearer, API key, WS-Security UsernameToken, istemci sertifikası |
| Senaryolar | YAML; SOAP ve REST adımları aynı dosyada, `extract` ile adımlar arası değişken zinciri (token, id), `{{env.x}}` ortam test verisi, `{{secret.STM_SECRET_X}}`, `{{$uuid}}` vb. |
| Assertion'lar | Sunucu içinde, deterministik: `fault`, `status`, `responseTimeMs`, `header`, JSON Schema, JSONPath + 17 operatör (`equals`, `numeric` (Decimal, tolerans), `in`, `matches`, `count`, `type`, `all: true` ...). Türkçe büyük/küçük harf (I/ı, İ/i) duyarlı |
| Güvenlik | Host allowlist (WSDL import'ları ve yönlendirmeler dahil), prod ortam adları reddedilir, mutating operasyon engeli, maskeleme (TCKN checksum, IBAN, kart Luhn, telefon, e-posta, şifre/token alanları), JSONL denetim logu, şifreler hiçbir çıktıda görünmez |
| Bağımsız kullanım | `service-test-agent` (OpenAI uyumlu her model: kurum içi Qwen/vLLM, Ollama, Azure OpenAI), MCP over HTTP (token korumalı), `--run` ile AI'sız CI koşusu ve JUnit raporu |
| Kurulum | **Offline**: Windows x64 ve x86 (32-bit) için Python 3.10–3.14 tekerlekleri zip içinde |

## Neden EDR / DLP ajanlarını daha az tetikler?

Her servis çağrısında `powershell.exe` açılıp `Invoke-WebRequest` ile elle birleştirilmiş SOAP zarfları göndermek, script-block logging, AMSI ve EDR kurallarının tam olarak izlediği davranıştır. `service-test-mcp` ise VS Code'un **bir kez** başlattığı tek bir Python sürecidir; çağrı başına alt süreç açmaz, komut satırında URL, gövde veya şifre bulunmaz, bağlantılar yeniden kullanılır.

> Dürüst not: Hiçbir araç, kurumunuzdaki güvenlik ürünlerinin hiçbir şey raporlamayacağını garanti edemez. Gerekirse BT ekibinizden yalnızca bu `service-test-mcp.exe` yolu için istisna isteyin; bu, "her çağrıda PowerShell" istisnasından çok daha dar ve savunulabilir bir istisnadır.

## Kurulum (Windows, internet gerekmez)

1. Python'unuza uygun paketi indirip bir klasöre açın:
   - **64-bit Python:** [service-test-mcp-simple-windows-x64.zip](https://github.com/ohankay/service-test-mcp-simple/raw/main/release/service-test-mcp-simple-windows-x64.zip)
   - **32-bit Python:** [service-test-mcp-simple-windows-x86.zip](https://github.com/ohankay/service-test-mcp-simple/raw/main/release/service-test-mcp-simple-windows-x86.zip)

   Python'unuzun kaç bit olduğunu `py -c "import struct; print(struct.calcsize('P')*8)"` gösterir.
2. Klasörü VS Code'da açın, Copilot Chat → **Agent** moduna şunu yazın:
   `KURULUM_REHBERI.md dosyasını oku ve "Ajan için kurulum adımları" bölümünü sırayla uygula.`

Elle kurulum: zip klasöründe `install.cmd` çalıştırın → `%USERPROFILE%\.service-test-mcp\Scripts\service-test-mcp.exe`.

İnternet varsa:

```bat
py -m venv %USERPROFILE%\.service-test-mcp
%USERPROFILE%\.service-test-mcp\Scripts\pip install --prefer-binary "service-test-mcp-simple @ git+https://github.com/ohankay/service-test-mcp-simple.git"
```

### Hemen denemek için: demo servisler

Zip içindeki `demo` klasöründe WCF tarzı bir SOAP servisi ve bir REST API'ye karşı hazır `services.yaml` ve 3 senaryo var:

```bat
%USERPROFILE%\.service-test-mcp\Scripts\service-test-mcp.exe --demo-server
rem baska bir terminalde:
set STM_CONFIG=C:\Araclar\service-test-mcp-simple\demo\services.yaml
%USERPROFILE%\.service-test-mcp\Scripts\service-test-mcp.exe --run C:\Araclar\service-test-mcp-simple\demo\scenarios
```

## VS Code'a ekleme

`Ctrl+Shift+P` → **MCP: Open User Configuration**:

```json
{
  "inputs": [
    { "type": "promptString", "id": "stm-pw-ntlm", "description": "Servis kullanicisi sifresi", "password": true }
  ],
  "servers": {
    "service-test": {
      "type": "stdio",
      "command": "${userHome}\\.service-test-mcp\\Scripts\\service-test-mcp.exe",
      "env": {
        "STM_CONFIG": "${userHome}\\.service-test-mcp\\services.yaml",
        "STM_PW_NTLM": "${input:stm-pw-ntlm}"
      }
    }
  }
}
```

Şifreyi VS Code bir kez sorar ve güvenli deposunda saklar. Hiçbir dosyaya düz metin olarak yazılmaz. Custom agent için `.github/agents/service-tester.agent.md` (`tools: ['service-test/*', ...]`), terminale kaçmayı önlemek için `templates/copilot-instructions.md`, hazır görevler için `.github/prompts/` kullanılabilir.

## Servis kataloğu (`services.yaml`)

```yaml
default_environment: test
environments:
  test:
    verify_tls: true            # veya kurum CA dosyası: C:\certs\kurum-ca.pem
    allow_mutating: false
    variables: { test_musteri_no: "1234567" }   # senaryoda {{env.test_musteri_no}}
services:
  HesapServisi:
    protocol: soap
    wsdl: { test: https://sunucu-test/HesapServisi.svc?wsdl }
    auth: { type: windows }     # şifresiz, oturum açmış Windows kullanıcısı
    mutating_operations: [TransferYap, "*Sil"]
  OdemeApi:
    protocol: rest
    base_url: { test: https://api-test.sirket.local/odeme/v1 }
    openapi: { test: https://api-test.sirket.local/odeme/v1/swagger.json }
    auth: { type: bearer, token_env: STM_TOKEN_API }
```

Tüm seçenekler: [`bundle/templates/services.yaml`](bundle/templates/services.yaml). Bir servis hatalı tanımlıysa yalnızca o servis "broken" görünür, diğerleri çalışır. `prod`, `production`, `prd`, `canli`, `live` adlı ortamlar reddedilir. İstekler yalnızca o servisin o ortam için yapılandırılmış host'larına gidebilir; WSDL farklı bir adres yayınlıyorsa (`endpoint` veya `allowed_hosts` ile) açıkça izin verilmelidir.

## Araçlar

| Araç | Ne yapar |
|---|---|
| `list_services` | Servisler, ortamlar, varsayılan ortam, hatalı tanımlar |
| `describe_service` | SOAP: port/binding, adres, operasyonlar. REST: base URL, OpenAPI operasyonları |
| `describe_operation` | Girdi/çıktı şeması, `example_params` (SOAP) veya OpenAPI ayrıntısı (REST) |
| `check_service` | Operasyon çağırmadan bağlantı testi (WSDL yükleme / base URL) |
| `soap_call` | SOAP operasyonu çağırır; `extract` ile yanıttan oturum değişkeni saklar |
| `rest_call` | REST çağrısı; `path`, `headers`, `body` içinde `{{değişken}}` |
| `session_variables` | `extract` ile saklanan değişkenleri gösterir/temizler (maskeli) |
| `check_last_response` | Son çağrının yanıtına assertion'ları uygular |
| `validate_scenario` | Senaryo dosyası/klasörü/metni: yapı, servis adları, assertion sözdizimi, JSONPath, tanımsız değişkenler |
| `run_scenario` | Tek senaryo (dosya veya satır içi YAML) |
| `run_test_suite` | Klasördeki tüm senaryolar, etiket filtresi, JUnit raporu |
| `reload_config` | `services.yaml` değişince yeniden okur |

Maskelenen değerler (`***1326`) modele geri yazdırılamaz; token ve id'ler `extract` ile saklanıp `{{token}}` olarak kullanılır. Assertion'lar ve `extract` her zaman gerçek değerlerle çalışır.

## Senaryo formatı

```yaml
id: login-bakiye
title: Login -> bakiye -> hesap detayı
tags: [smoke]
steps:
  - name: Login
    service: HesapServisi          # SOAP: operation + params
    operation: Login
    params: { kullaniciAdi: "{{env.kullanici}}", sifre: "{{secret.STM_SECRET_TEST_SIFRE}}" }
    assert:
      - fault: none
      - path: $.Token
        notEmpty: true
    extract: { token: $.Token, musteriNo: $.MusteriNo }
  - name: Bakiye
    service: HesapServisi
    operation: GetBakiye
    params: { token: "{{token}}", musteriNo: "{{musteriNo}}" }
    assert:
      - path: $.ToplamBakiyeTRY
        numeric: { gte: 0 }
      - path: $.Hesaplar.Hesap[*].Iban
        matches: "^TR\\d{24}$"
        all: true
  - name: Hesap detayı
    service: OdemeApi              # REST: method + path (+ query, headers, body)
    method: GET
    path: /hesaplar/{{musteriNo}}
    headers: { Authorization: "Bearer {{token}}" }
    assert:
      - status: 200
      - schema: schemas/hesap.json
```

Assertion referansı: `fault: none | any | {code, message_contains}`, `status: 200 | [200, 201] | {gte: 200, lt: 300}`, `responseTimeMs: {lt: 2000}`, `header: Content-Type` + `contains`, `schema: dosya.json`, ve `path: <JSONPath>` + bir operatör: `equals`, `notEquals`, `equalsIgnoreCase`, `contains`, `notContains`, `startsWith`, `endsWith`, `matches`, `in`, `notIn`, `exists`, `notEmpty`, `isNull`, `type` (`string`, `number`, `integer`, `decimal`, `boolean`, `object`, `array`, `null`, `date`, `datetime`), `numeric` (`eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `between`, `tolerance`), `count`, `length`, `schema`. `all: true` her eşleşmeye uygular, `message:` hata metnine eklenir. Bir adım başarısız olursa sonraki adımlar atlanır (`continue_on_failure: true` hariç).

SOAP yanıtları JSON'a normalize edilir: namespace'ler düşer, `maxOccurs > 1` elemanlar her zaman listedir, `xsd:decimal` Decimal olarak karşılaştırılır, tarihler ISO-8601 metindir.

## VS Code'dan bağımsız kullanım

**Hazır ajan** (`service-test-agent`, ek paket gerekmez): OpenAI uyumlu herhangi bir chat-completions sunucusuna bağlanır, MCP sunucusunu kendisi başlatır.

```bat
set STM_AGENT_BASE_URL=http://qwen.sirket.local:8000/v1
set STM_AGENT_MODEL=qwen2.5-72b-instruct
set STM_CONFIG=%USERPROFILE%\.service-test-mcp\services.yaml
service-test-agent --check
service-test-agent --prompt "HesapServisi Login operasyonu icin pozitif ve negatif senaryo yaz ve kostur" --output rapor.md
service-test-agent            &rem interaktif sohbet
```

Model sunucusunda tool calling açık olmalı (vLLM + Qwen: `--enable-auto-tool-choice --tool-call-parser hermes`). Azure OpenAI için `STM_AGENT_AZURE_API_VERSION`, anahtar için `STM_AGENT_API_KEY`.

**Kendi ajanınız / framework'ünüz:** MCP sunucusunu stdio ile (`service-test-mcp.exe`) veya HTTP ile başlatın:

```bat
set STM_HTTP_TOKEN=uzun-rastgele-bir-deger
service-test-mcp --transport http --host 0.0.0.0 --port 8765
```

Uç nokta `http://sunucu:8765/mcp`, istemci `Authorization: Bearer <token>` gönderir (yerel olmayan adreste token zorunludur). LangChain/LangGraph (`langchain-mcp-adapters`), Semantic Kernel, Claude Desktop ve diğer MCP istemcileri doğrudan bağlanır; allowlist, maskeleme, mutating engeli ve denetim logu her istemci için aynen geçerlidir.

**AI'sız koşu (CI, zamanlanmış görev):**

```bat
service-test-mcp --run D:\testler\senaryolar --env test --tags smoke --junit sonuc.xml --report sonuc.json
```

Çıkış kodu: `0` hepsi geçti, `1` başarısız senaryo var, `2` yapılandırma/senaryo hatası.

## Bilinmesi gerekenler

* **Desteklenmeyen WCF binding'leri:** `netTcpBinding`, `netNamedPipeBinding` ve SecureConversation'lı mesaj güvenliği (`wsHttpBinding` varsayılanı) Python'dan çağrılamaz. Test ortamında ek bir `basicHttpBinding` endpoint'i istemek en temiz yoldur. Birden çok port varsa SOAP 1.1 port'u tercih edilir; `port_name` ile seçilebilir.
* **Maskeleme varsayılanları:** TCKN (checksum ile), TR IBAN, kart numarası (Luhn ile), cep telefonu, e-posta ve şifre/token/secret/cvv/pin alanları. Müşteri ve hesap numaraları varsayılan olarak maskelenmez (test akışında gerekli); `masking.fields` ile eklenir, `masking.unmask_fields` ile çıkarılır.
* **Denetim logu:** `%USERPROFILE%\.service-test-mcp\logs\audit-YYYYMMDD.jsonl`; her çağrı için kullanıcı, servis, operasyon, ortam, durum, süre ve maskeli gövde.
* Çıktılar varsayılan olarak 20.000 karakterle sınırlıdır (`max_response_chars`); büyük yanıtlarda assertion veya `extract` kullanın.

## Geliştirme

```bash
pip install -e ".[dev]"
pytest -v                                 # demo servislere karşı birim, entegrasyon, stdio/HTTP e2e ve ajan testleri
pip install uv && python scripts/build_bundle.py   # release/*.zip (x64 + x86, Python 3.10-3.14)
```

PyPI'da 32-bit Python 3.14 için PyYAML tekerleği olmadığından `vendor/` altında saf Python bir PyYAML tekerleği bulunur (`PYYAML_FORCE_LIBYAML=0 pip wheel pyyaml==6.0.3`); yalnızca ikili tekerlek olmayan hedefte kullanılır.

## Lisans

MIT
