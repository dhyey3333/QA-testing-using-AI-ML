# Invoice templates

*Check with a CA before your first GST invoice. Number invoices in one unbroken series per
financial year (April to March), e.g. NSQ/2026-27/001.*

## A. Not registered for GST

```
INVOICE                                              No. NSQ/2026-27/001
                                                     Date: [dd Mon yyyy]
From: [Your legal name / business name]
      [Address]
      [Email]   PAN: [your PAN]
To:   [Agency legal name]
      [Address]

| # | Description                                   | Period            | Amount (₹) |
|---|-----------------------------------------------|-------------------|-----------:|
| 1 | Nightshift QA, Starter plan (3 testers)       | [1-31 Oct 2026]   |  14,999.00 |
|   |                                               | Total             |  14,999.00 |

Amount in words: Fourteen thousand nine hundred ninety-nine rupees only.
Not registered under GST; no GST charged.
Pay by [date] to: [Account name], A/c [number], IFSC [code], [Bank], or UPI [id].
```

## B. Registered for GST (tax invoice for a service; ask your CA which SAC code fits a software
service like this one, and put it in the SAC column)

```
TAX INVOICE                                          No. NSQ/2026-27/001
                                                     Date: [dd Mon yyyy]
From: [Legal name]   GSTIN: [your GSTIN]   [Address, State, State code]
To:   [Agency legal name]   GSTIN: [their GSTIN, if registered]   [Address, State, State code]
Place of supply: [their State]

| # | Description                        | SAC    | Taxable value (₹) |
|---|------------------------------------|--------|------------------:|
| 1 | Nightshift QA, Starter plan, [Oct] | [SAC]  |         14,999.00 |
|   | Same state:  CGST 9% + SGST 9%     |        |    1,349.91 + 1,349.91 |
|   | Other state: IGST 18%              |        |          2,699.82 |
|   | Total                              |        |         17,698.82 |

Amount in words: ...
Pay by [date] to: [bank details / UPI]
```

Keep a copy of every invoice; file GST returns (GSTR-1, GSTR-3B) as your CA advises.
