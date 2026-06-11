# Legal-X Invoice Extractor (Streamlit)

Extracts invoice data from Legal-X portal Bill Analysis Report XML files into Excel.

## Features
- Upload multiple XML files at once
- Three output formats (sheets in one Excel file):
  - **Bill Summary** — one row per invoice (totals, dates, client)
  - **Line Items — BillSync** — 22-column format, reduced items only, one row per deduction with embedded audit reason
  - **All Line Items** — 14-column format, every line item; audit fields blank when no reduction
- Timekeeper codes resolved to full names
- Multiple deductions per line item are split into separate rows

## Run locally
```
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud
1. Push this folder to a GitHub repository
2. Go to https://share.streamlit.io and click **New app**
3. Select the repo, branch `main`, main file `app.py`
4. Deploy
