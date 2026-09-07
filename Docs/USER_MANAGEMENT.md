# Zeropark MCP — zarządzanie użytkownikami (Cognito)

Serwer MCP uwierzytelnia przez **OAuth / Amazon Cognito**. Każdy użytkownik ma
własne konto w Cognito, loguje się przy podłączaniu connectora w Claude, a jego
tożsamość (`sub` z tokenu JWT) wyznacza **osobny prefiks S3** — pełna izolacja
per-user na AWS. Nie ma żadnych tokenów do rozdawania ani rotowania.

## Wartości tego wdrożenia (stałe)

| Co | Wartość |
|---|---|
| User Pool ID | `us-east-1_7BGhLbilR` |
| User Pool (nazwa) | `commercemedia-users` |
| App Client ID | `3aacg37044mmuatr28aom1c7k3` |
| Hosted UI | `https://zeropark-mcp-auth.auth.us-east-1.amazoncognito.com` |
| MCP URL (connector) | `https://er5w3b4qr6cymypvchda6n4lje0kovpf.lambda-url.us-east-1.on.aws/mcp` |
| Profil AWS / region | `Automations_dev` / `us-east-1` |

Skrót do komend (wklej raz na sesję PowerShell):
```powershell
$POOL = "us-east-1_7BGhLbilR"
$AWS  = "--profile Automations_dev --region us-east-1"
```

---

## 1. Dodawanie nowego użytkownika

Jedna komenda — Cognito samo wysyła mail z tymczasowym hasłem, a użytkownik
ustawia własne przy pierwszym logowaniu (nikt poza nim nie zna hasła):

```powershell
aws cognito-idp admin-create-user --user-pool-id us-east-1_7BGhLbilR `
  --username jan.kowalski@cm.tech.com `
  --user-attributes Name=email,Value=jan.kowalski@cm.tech.com Name=email_verified,Value=true `
  --profile Automations_dev --region us-east-1
```

Co się dzieje dalej:
1. Użytkownik dostaje mail **"Your temporary password"** (nadawca
   `no-reply@verificationemail.com` — może wpaść do spamu, uprzedź go).
2. Przekazujesz mu instrukcję podłączenia (sekcja 2).
3. Przy pierwszym logowaniu Cognito wymusza ustawienie własnego hasła.

Uwagi:
- **Używaj firmowych maili** — przy przyszłej federacji Entra te same adresy
  przejdą płynnie na logowanie kontem Microsoft.
- Limit domyślnej wysyłki maili Cognito to ~50/dobę — przy naszej skali
  (maks. ~30 userów) bez znaczenia.
- Tymczasowe hasło wygasa (domyślnie po 7 dniach). Jeśli wygaśnie zanim user
  się zaloguje — patrz "Reset hasła" niżej.

## 2. Instrukcja dla nowego użytkownika (do przekazania)

> 1. Sprawdź maila — dostałeś tymczasowe hasło od `no-reply@verificationemail.com`
>    (zajrzyj też do spamu).
> 2. W Claude: **Settings → Connectors → Add custom connector**
>    - Name: `Zeropark Reports`
>    - Remote MCP server URL: `https://er5w3b4qr6cymypvchda6n4lje0kovpf.lambda-url.us-east-1.on.aws/mcp`
>    - Advanced settings → OAuth Client ID: `3aacg37044mmuatr28aom1c7k3`
>    - OAuth Client Secret: **zostaw puste**
> 3. Kliknij **Add** — otworzy się okno logowania.
> 4. Zaloguj się swoim mailem + tymczasowym hasłem z maila; system poprosi
>    o ustawienie własnego hasła (min. 12 znaków, duża/mała litera, cyfra, symbol).
> 5. Po zalogowaniu connector pokaże narzędzia Zeropark — gotowe.

## 3. Zarządzanie użytkownikami

### Lista wszystkich użytkowników
```powershell
aws cognito-idp list-users --user-pool-id us-east-1_7BGhLbilR `
  --query "Users[].[Username,UserStatus,Attributes[?Name=='email'].Value|[0]]" `
  --output table --profile Automations_dev --region us-east-1
```
Statusy: `CONFIRMED` = aktywny; `FORCE_CHANGE_PASSWORD` = utworzony, jeszcze się
nie logował.

### Podgląd jednego użytkownika
```powershell
aws cognito-idp admin-get-user --user-pool-id us-east-1_7BGhLbilR `
  --username jan.kowalski@cm.tech.com `
  --profile Automations_dev --region us-east-1
```

### Reset hasła (user zapomniał / tymczasowe wygasło)
```powershell
# wygeneruje NOWY mail z tymczasowym hasłem:
aws cognito-idp admin-reset-user-password --user-pool-id us-east-1_7BGhLbilR `
  --username jan.kowalski@cm.tech.com `
  --profile Automations_dev --region us-east-1
```
Alternatywnie hasło można ustawić ręcznie (np. gdy maile nie dochodzą):
```powershell
aws cognito-idp admin-set-user-password --user-pool-id us-east-1_7BGhLbilR `
  --username jan.kowalski@cm.tech.com --password "NoweHaslo123!" --permanent `
  --profile Automations_dev --region us-east-1
```
(`--permanent` = bez wymuszania zmiany; przekaż hasło bezpiecznym kanałem.)

### Zablokowanie dostępu (odejście z zespołu, incydent)
```powershell
aws cognito-idp admin-disable-user --user-pool-id us-east-1_7BGhLbilR `
  --username jan.kowalski@cm.tech.com `
  --profile Automations_dev --region us-east-1
```
Skutek: user nie zaloguje się ponownie. **Uwaga:** już wydane tokeny działają do
wygaśnięcia (access/id: 1h). Żeby uciąć NATYCHMIAST — dodatkowo:
```powershell
aws cognito-idp admin-user-global-sign-out --user-pool-id us-east-1_7BGhLbilR `
  --username jan.kowalski@cm.tech.com `
  --profile Automations_dev --region us-east-1
```
(unieważnia refresh tokeny; connector w Claude przestanie się odświeżać).

Odblokowanie: `admin-enable-user` (te same parametry co disable).

### Trwałe usunięcie użytkownika
```powershell
aws cognito-idp admin-delete-user --user-pool-id us-east-1_7BGhLbilR `
  --username jan.kowalski@cm.tech.com `
  --profile Automations_dev --region us-east-1
```
Uwaga: usunięcie kasuje `sub` użytkownika. Jego dane na S3 (prefiks) zostają,
ale nikt już nie dostanie tego samego prefiksu — nawet jeśli założysz konto na
ten sam email, dostanie NOWY `sub`, czyli nowy, pusty prefiks. Do rozważenia:
`admin-disable-user` zamiast delete, jeśli osoba może wrócić.

### Wszystko powyższe działa też z konsoli AWS
Cognito → User pools → `commercemedia-users` → Users. Tworzenie, reset, disable,
delete — klikalne. CLI jest szybsze przy wielu userach; konsola wygodniejsza
przy pojedynczych.

## 4. Jak działa izolacja per-user (dla świadomości)

- Po zalogowaniu Claude wysyła do serwera JWT wydany przez Cognito.
- Serwer waliduje podpis (JWKS), issuer, audience i wyciąga **`sub`** — stały,
  unikalny identyfikator konta.
- `lib/identity.py` hashuje `sub` → prefiks S3 (`mcp-<hash>/`). Każdy użytkownik
  widzi wyłącznie własne raporty; prefiksy się nie przecinają.
- Nie ma współdzielonych sekretów: zablokowanie/usunięcie konta w Cognito
  odcina dostęp bez dotykania serwera, S3 ani innych użytkowników.

## 5. Rzeczy, których NIE trzeba robić

- **Żadnych tokenów do generowania/rotowania** — stary mechanizm bearer
  (`zeropark-mcp/BEARER_TOKENS` w Secrets Manager) jest nieaktywny w trybie
  OAuth. Secret można zignorować (zostawiony dla kompatybilności dev/stdio).
- **Żadnych cold startów po zmianach użytkowników** — Cognito jest źródłem
  prawdy w czasie rzeczywistym; serwer nic nie cache'uje o userach.
- **Żadnych zmian w serwerze przy dodawaniu/usuwaniu ludzi.**

## 6. Na przyszłość — federacja Microsoft Entra (opcjonalnie)

Gdy zespół zechce logowania kontem Microsoft zamiast haseł Cognito:
1. W Entra rejestrujesz aplikację (enterprise app / OIDC).
2. W Cognito dodajesz identity provider (`aws_cognito_user_pool_identity_provider`
   typu OIDC/SAML) na TYM SAMYM poolu.
3. W app clencie dodajesz "Microsoft" do SupportedIdentityProviders.
4. **Serwer nie zmienia się wcale** — JWT nadal wydaje Cognito.

Uwaga przy migracji: użytkownicy federowani dostają INNE `sub` niż lokalni —
czyli nowe prefiksy S3. Stare raporty zostaną pod starymi prefiksami; zaplanuj
przejście (np. moment, gdy nikt nie ma ważnych raportów "w locie").
