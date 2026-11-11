# PC and mobile: how data stays in sync

BEREV ERP is a web application with one central PostgreSQL database. Every PC, tablet and phone talks to the same
server over HTTPS, so an invoice created on a phone is visible on the PC immediately after the next refresh. There is no
separate copy of the data on each device to merge, so there is no sync conflict to resolve.

## Using it on a phone
1. Deploy it (see `DEPLOYMENT.md`) on a server with HTTPS, e.g. `https://erp.berev.world`.
2. Open the address in Chrome (Android) or Safari (iPhone) and choose *Add to Home screen* / *Install app*. The app has a web
   manifest and icons built from the BEREV logo, so it opens full-screen like an app.
3. Sign in with your own account; roles and permissions are the same on every device.

## What it does NOT do
- **No offline mode.** Without internet the app cannot create invoices or look up data. Offline billing needs a local database,
  a sync queue and conflict rules; with gapless GST invoice numbers and stock that is a high-risk feature and is deliberately not built.
- No native Android/iOS app, and no push notifications (in-app notifications only).

## Honest comparison with Vyapar-style billing apps
Present and tested by the automated suite (PostgreSQL): GST invoices (draft/approve/issue/cancel, PDF), credit notes, payments and
refunds, customer ledgers, quotation -> order -> challan flow, purchases/GRN/supplier invoices, weighted-average stock costing,
expenses, dealer portal, double-entry accounting, backups with a restore drill.

Not present (verified by searching the code): thermal/POS receipts, barcode scanning, WhatsApp/SMS sharing of invoices,
GSTR-1/GSTR-3B export, e-way bill, e-invoice (IRN/QR), bank reconciliation screens, offline mode, multiple firms in one login,
cloud backup to a third-party drive. Email of invoices exists only if SMTP is configured. Whether IRN e-invoicing applies to
BEREV must be confirmed with your tax professional.
