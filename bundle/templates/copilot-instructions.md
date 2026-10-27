## Servis testleri

- SOAP/WCF ve REST servis cagrilarinda terminal, PowerShell, curl veya `Invoke-WebRequest` KULLANMA; `service-test`
  MCP araclarini kullan (`list_services`, `describe_operation`, `soap_call`, `rest_call`, `check_last_response`).
- Tekrarlanacak kontrolleri `scenarios/` altina YAML senaryo olarak yaz, `validate_scenario` ile dogrula,
  `run_scenario` / `run_test_suite` ile kostur.
- Maskelenmis degerleri (`***`) tahmin etme; token ve id'leri `extract` ile sakla ve `{{degisken}}` olarak kullan.
