# CGHS Claim Checker & Sanction Letter Generator

Upload a scanned/photographed CGHS medical reimbursement claim (claim form,
bill, referral). The app reads it with an AI vision model, checks every
billed test/treatment against the official CGHS rate list, flags anything
billed above the CGHS rate or double-billed, and generates the Sanction
Memo + NS/Checklist note used by this division — only the claimant's name,
ID, and figures change each time.

**This is a decision-support tool, not an auto-approver.** Every match,
rate, and total is shown for the processing officer to review and correct
before a case is sanctioned. Treat every output as a draft.

---

## What's included

| File | Purpose |
|---|---|
| `app.py` | The Streamlit app (UI, OCR call, matching, document generation) |
| `matcher.py` | Matches claimed line items to CGHS codes (exact code match first, fuzzy text match as fallback) |
| `letter_templates.py` | Generates the Sanction Memo and NS/Checklist `.docx` files from your existing wording |
| `num2words_inr.py` | Converts rupee figures to words (Indian lakh/crore numbering) |
| `data/CGHS_Rates_Delhi_TierI.xlsx` | The CGHS rate master, extracted from `CGHS_RATE_as_on_3_10_2025.pdf`, Tier I / X City (Delhi), 1,998 codes, semi-private ward base rates |
| `data/cghs_tier1_delhi.json` | Same data as above, used internally by the app |

## Setup

1. **Get an Anthropic API key** — sign up at [console.anthropic.com](https://console.anthropic.com), create a key. This is what powers the OCR/reading step; you enter it once per session in the app's sidebar (or set it as an environment variable — see below). It is never saved anywhere.

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
   You'll also need **poppler** installed on your machine so PDF pages can be converted to images:
   - Windows: download poppler binaries, add the `bin` folder to your PATH ([guide](https://github.com/oschwartz10612/poppler-windows/releases))
   - Mac: `brew install poppler`
   - Linux: `sudo apt install poppler-utils`

3. **Run it:**
   ```bash
   streamlit run app.py
   ```
   It opens in your browser at `http://localhost:8501`.

## Deploying to Streamlit Community Cloud (like PostBuddy)

1. Push this folder to a GitHub repo (the included `packages.txt` tells Streamlit Cloud to install `poppler-utils` automatically — you don't need to do anything extra for that).
2. On [share.streamlit.io](https://share.streamlit.io), point a new app at the repo, main file `app.py`.
3. Instead of typing your API key into the sidebar every time, you can add it as a **Streamlit secret**: in the app's settings → Secrets, add:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```
   The app reads this automatically if the sidebar field is left blank.

## How the item matching works

For each line item extracted from the claim:
1. If a CGHS code was read directly off the document, it's matched exactly.
2. Otherwise, the test/treatment description is fuzzy-matched against all 1,998 CGHS descriptions, and the closest one above a similarity threshold is used.
3. Every row shows: what was claimed, the matched CGHS code and rate, the admissible amount (the lower of the two), and a flag:
   - **OK** — claimed amount is within the CGHS rate
   - **OVERCHARGED** — claimed above the CGHS rate (capped automatically for the total)
   - **POSSIBLE DUPLICATE** — the same CGHS code appears more than once in the bill (labs sometimes split one test into several internal line items — worth a second look so it isn't double-counted)
   - **NO MATCH** — couldn't confidently match this row; needs your judgement
4. **Every row is editable** in the app — if a match looks wrong, correct the CGHS code directly in the table.
5. Name and Employee ID (and every other field) can always be typed in manually if OCR misses them or gets them wrong.

## Important limitations to know about

- **Only Delhi (Tier I / X City) rates are bundled by default.** If you process claims for other cities, you'll need the Tier II or Tier III rate tables added — let me know and I can extract those the same way from the same source PDF (they're on different pages of the same document).
- **Ward entitlement:** the bundled rates are the semi-private-ward base rates. Per the CGHS rate memo, General Ward is 5% less and Private Ward is 5% more — this isn't automated yet, since the memo also says investigations/consultations/radiotherapy are uniform regardless of ward. If you regularly process indoor/surgical claims (where ward entitlement changes the rate), this is worth adding next.
- **Bundled/discounted lab bills:** some diagnostic labs quote a package rate lower than the sum of individual listed prices (as in the sample case used to build this). The app checks each *line item* against its CGHS rate; it doesn't try to reconcile a lab's internal discounting logic. Always sanity-check the total against the actual amount paid on the receipt.
- **OCR quality depends on scan/photo clarity.** Handwritten forms are read reasonably well by the underlying model, but always verify the extracted fields, especially amounts, before sanctioning.
- **This tool never auto-approves anything.** It surfaces the correct rate next to whatever was claimed; the sanctioning decision remains with the processing officer, as it must.

## Extending the CGHS rate list

If CGHS issues a revised rate memo, or you want to add Tier II/III cities:
- The Excel format expected is: `CGHS Code | Description | Rate (Non-NABH) | Rate (NABH) | Rate (Super Speciality) | Classification`
- Upload it via the "Replace rate list" option in the sidebar, or replace `data/cghs_tier1_delhi.json` directly and redeploy.
