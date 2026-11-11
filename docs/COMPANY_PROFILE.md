# Company profile (from BEREV's registration certificates)

`backend/company_profile.json` was filled from the GST registration certificate (REG-06), the Udyam certificate and the
Andhra Pradesh Form-C certificate. It is **git-ignored** (it holds tax identifiers). `company_profile.example.json` is the
committed template. Nothing in the application source hardcodes these values: they are stored in the `company_settings`
table, exactly as if an administrator had typed them into Settings.

## Load it
```powershell
# local (venv active, DATABASE_URL pointing at the database you want, migrations applied, seed/admin step done once)
cd backend
python -m app.cli load-company-profile company_profile.json

# production (Docker): mount the file read-only for one run
docker compose -f docker-compose.prod.yml run --rm -v "${PWD}/backend/company_profile.json:/tmp/profile.json:ro" api python -m app.cli load-company-profile /tmp/profile.json
```
The loader applies the same validation as the Settings screen (GSTIN checksum and state prefix, PAN format, invoice prefix length,
state exists). It prints `INVALID ...` lines and changes nothing if a value is wrong. You can also type the same values in **Settings**.

## Where each value came from
| Setting | Value source |
|---|---|
| legal_name `DANIEL GUMMADI` | GST certificate, "Legal Name" (proprietorship) |
| trade_name `M/s BER EV MARKETING` | GST certificate, "Trade Name" (kept exactly as registered, including "M/s") |
| gstin / state_code | GST certificate registration number; state code 37 = Andhra Pradesh. The checksum was verified. |
| pan | Characters 3-12 of the GSTIN. **Compare with the PAN card** before relying on it. |
| address | GST certificate principal place of business (PIN 518003) |
| phone, email | From the original BEREV brief (`+91-9440420255`, `admin@berev.world`), **not** from the certificates |
| authorized_signatory | Proprietor named on the certificates. Change if someone else signs. |
| invoice_prefix `BER` | Chosen so `BERT/26-27/00001` fits the 16-character invoice-number limit. Change if you prefer another (max 3 letters with the current format). |
| bank, UPI, terms, logo path | Not in any certificate: left empty. Enter bank/UPI/terms in Settings. The PDF uses the bundled BEREV logo when no logo path is set. |

## Things in the documents to check with your CA (not code problems)
1. **PIN code:** the GST and Form-C certificates say 518003; the Udyam certificate says 518002. The profile uses 518003 (GST). Align the Udyam record.
2. **Udyam activity code:** the Udyam certificate lists NIC 96091 ("social activities such as escort services, marriage bureaus") for a business that sells EV products. That looks wrong for the real activity and can be corrected on the Udyam portal.
3. **Form-C "nature of business":** reads "Others - ( MERKITING)" (typo for marketing).
4. **Contact details differ:** Udyam shows a different mobile number and a personal Gmail address. The profile uses the business contact from the BEREV brief; change it if you prefer the Udyam contacts.
5. The GST certificate is a **regular** registration effective 04/06/2026. HSN codes and GST rates for each EV product still need your CA's confirmation; the software does not decide tax treatment.
