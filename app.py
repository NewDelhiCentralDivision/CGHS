"""
CGHS Claim Checker & Sanction Letter Generator
------------------------------------------------
Upload a scanned/photographed CGHS medical reimbursement claim (form, bill,
referral). This tool reads it with an AI vision model, matches every claimed
test/treatment against the official CGHS rate list, flags any amount billed
above the CGHS rate, and generates the Sanction Memo + NS/Checklist note
used by this division -- with only the claimant details changing each time.

Run:  streamlit run app.py
"""
import os
import io
import json
import base64
import datetime

import streamlit as st
import pandas as pd

from matcher import load_master, match_line_item, flag_duplicates
from letter_templates import build_sanction_memo, build_ns_checklist
from num2words_inr import rupees_in_words

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
MASTER_JSON = os.path.join(DATA_DIR, 'cghs_tier1_delhi.json')

st.set_page_config(page_title="CGHS Claim Checker", page_icon="🩺", layout="wide")

# ---------------------------------------------------------------------------
# Sidebar: API key + rate list
# ---------------------------------------------------------------------------
st.sidebar.title("Setup")
api_key = st.sidebar.text_input(
    "Anthropic API key",
    type="password",
    help="Get one at console.anthropic.com. Used only for this session, never stored.",
    value=os.environ.get("ANTHROPIC_API_KEY", "")
)

st.sidebar.markdown("---")
st.sidebar.subheader("CGHS Rate List")
st.sidebar.caption(
    "Bundled: Delhi / Tier-I (X City) rates, effective 13.10.2025, 1,998 codes. "
    "Upload a replacement JSON/Excel below if CGHS issues a revision."
)
custom_master = st.sidebar.file_uploader("Replace rate list (optional)", type=["xlsx", "json"])

@st.cache_data
def get_master(_uploaded_bytes, _uploaded_name):
    if _uploaded_bytes is not None:
        if _uploaded_name.endswith('.json'):
            data = json.loads(_uploaded_bytes)
        else:
            df = pd.read_excel(io.BytesIO(_uploaded_bytes))
            data = df.rename(columns={
                'CGHS Code': 'code', 'Description': 'description',
                'Rate (Non-NABH)': 'rate_non_nabh', 'Rate (NABH)': 'rate_nabh',
                'Rate (Super Speciality)': 'rate_super_speciality', 'Classification': 'classification'
            }).to_dict('records')
        by_code = {r['code']: r for r in data}
        descriptions = {r['code']: r['description'] for r in data}
        return data, by_code, descriptions
    with open(MASTER_JSON) as f:
        data = json.load(f)
    by_code = {r['code']: r for r in data}
    descriptions = {r['code']: r['description'] for r in data}
    return data, by_code, descriptions

uploaded_bytes = custom_master.read() if custom_master else None
uploaded_name = custom_master.name if custom_master else None
MASTER_DATA, BY_CODE, DESCRIPTIONS = get_master(uploaded_bytes, uploaded_name)
st.sidebar.success(f"{len(MASTER_DATA)} CGHS codes loaded")

st.title("🩺 CGHS Claim Checker & Sanction Letter Generator")
st.caption("New Delhi Central Division — decision-support tool. Every match and amount must be reviewed by the processing officer before a case is sanctioned.")

if "extracted" not in st.session_state:
    st.session_state.extracted = None
if "line_items" not in st.session_state:
    st.session_state.line_items = None

# ---------------------------------------------------------------------------
# Step 1: Upload claim documents
# ---------------------------------------------------------------------------
st.header("1. Upload claim documents")
st.write("Upload the claim form, bill(s)/receipt, and referral if available. Photos, scans, or PDFs are all fine.")

uploaded_files = st.file_uploader(
    "Claim documents",
    type=["png", "jpg", "jpeg", "pdf"],
    accept_multiple_files=True
)

run_ocr = st.button("🔍 Extract claim details", type="primary", disabled=not uploaded_files)

def file_to_image_blocks(f):
    """Convert an uploaded file (image or PDF) into a list of Anthropic image content blocks."""
    blocks = []
    raw = f.read()
    if f.type == "application/pdf" or f.name.lower().endswith(".pdf"):
        from pdf2image import convert_from_bytes
        pages = convert_from_bytes(raw, dpi=200)
        for p in pages:
            buf = io.BytesIO()
            p.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}})
    else:
        media_type = f.type if f.type else "image/jpeg"
        b64 = base64.b64encode(raw).decode()
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}})
    return blocks

EXTRACTION_PROMPT = """You are reading scanned/photographed CGHS medical reimbursement claim documents
(claim form, bill/receipt, doctor's referral, CGHS card). Extract the following as strict JSON, with
no commentary before or after:

{
  "claimant_name": string or null,
  "employee_id": string or null,
  "designation": string or null,
  "office": string or null,
  "pin": string or null,
  "cghs_beneficiary_id": string or null,
  "cghs_card_validity": string or null,
  "patient_relation": string or null,        // e.g. "SELF", "SPOUSE", "SON" etc.
  "hospital_or_lab_name": string or null,
  "nabh_status": string or null,             // "NABH" or "Non-NABH" if stated/visible, else null
  "hco_type": string or null,                // e.g. "CGHS empanelled" / "Private" / "Govt"
  "submission_date": string or null,         // DD.MM.YYYY as printed
  "treatment_type": string or null,          // e.g. "OPD", "Indoor", "TEST/INVESTIGATION", "Emergency"
  "total_claimed_amount": number or null,
  "line_items": [
    {
      "description": string,          // the test/treatment name exactly as printed
      "code": string or null,         // any lab/CGHS code printed alongside it, if visible
      "claimed_amount": number
    }
  ]
}

Read every page provided. If a field is not present anywhere, use null. For line_items, include every
distinct billed test/treatment/procedure row you can find, with its exact price as printed - including
rows priced at 0. Do not invent values. Output ONLY the JSON object."""

if run_ocr:
    if not api_key:
        st.error("Please enter your Anthropic API key in the sidebar first.")
    else:
        with st.spinner("Reading documents with Claude..."):
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=api_key)
                content_blocks = []
                for f in uploaded_files:
                    content_blocks.extend(file_to_image_blocks(f))
                content_blocks.append({"type": "text", "text": EXTRACTION_PROMPT})

                resp = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=4000,
                    messages=[{"role": "user", "content": content_blocks}]
                )
                raw_text = "".join(b.text for b in resp.content if b.type == "text")
                raw_text = raw_text.strip().strip("`")
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:].strip()
                extracted = json.loads(raw_text)
                st.session_state.extracted = extracted
                st.session_state.line_items = extracted.get("line_items", [])
                st.success("Extraction complete. Review and correct the details below.")
            except Exception as e:
                st.error(f"Extraction failed: {e}")
                st.info("You can still fill in the details manually below.")
                st.session_state.extracted = {}
                st.session_state.line_items = []

# ---------------------------------------------------------------------------
# Step 2: Review / correct claimant details (manual override always available)
# ---------------------------------------------------------------------------
st.header("2. Claimant details")
st.caption("If OCR could not read the name or employee ID, fill them in here manually - everything on this page is editable.")

ex = st.session_state.extracted or {}
col1, col2, col3 = st.columns(3)
with col1:
    name = st.text_input("Claimant name", value=ex.get("claimant_name") or "")
    designation = st.text_input("Designation", value=ex.get("designation") or "")
    office = st.text_input("Office", value=ex.get("office") or "")
with col2:
    emp_id = st.text_input("Employee ID", value=ex.get("employee_id") or "")
    pin = st.text_input("PIN", value=ex.get("pin") or "")
    patient_relation = st.text_input("Patient relation to official", value=ex.get("patient_relation") or "SELF")
with col3:
    hospital = st.text_input("Hospital / Diagnostic Centre", value=ex.get("hospital_or_lab_name") or "")
    cghs_id = st.text_input("CGHS Beneficiary ID", value=ex.get("cghs_beneficiary_id") or "")
    cghs_validity = st.text_input("CGHS card validity", value=ex.get("cghs_card_validity") or "")

col4, col5, col6 = st.columns(3)
with col4:
    nabh_status = st.selectbox("Facility accreditation", ["NABH", "Non-NABH"],
                                index=0 if (ex.get("nabh_status") or "NABH") == "NABH" else 1)
with col5:
    submission_date = st.text_input("Claim submission date", value=ex.get("submission_date") or "")
with col6:
    treatment_type = st.text_input("Treatment type", value=ex.get("treatment_type") or "TEST/INVESTIGATION")

# ---------------------------------------------------------------------------
# Step 3: Line-item matching against CGHS rates
# ---------------------------------------------------------------------------
st.header("3. Item-wise CGHS rate check")

items_raw = st.session_state.line_items or []
if not items_raw:
    st.info("No line items extracted yet. Run extraction above, or add rows manually in the table below.")
    items_raw = [{"description": "", "code": "", "claimed_amount": 0}]

matched = [match_line_item(it, BY_CODE, DESCRIPTIONS) for it in items_raw]
matched = flag_duplicates(matched)

rate_field = 'cghs_rate_nabh' if nabh_status == 'NABH' else 'cghs_rate_non_nabh'
for it in matched:
    if it.get('matched_code'):
        it['cghs_rate_used'] = it.get(rate_field)
        it['admissible_amount'] = min(it.get('claimed_amount') or 0, it['cghs_rate_used']) if it['cghs_rate_used'] is not None else None
        if (it.get('claimed_amount') or 0) > (it['cghs_rate_used'] or 0):
            it['flag'] = 'OVERCHARGED' if 'DUPLICATE' not in it['flag'] else it['flag']
    else:
        it['cghs_rate_used'] = None

df = pd.DataFrame([{
    'Description (as billed)': it.get('description', ''),
    'Billed code': it.get('code', ''),
    'Matched CGHS code': it.get('matched_code') or '',
    'Matched CGHS description': it.get('matched_description') or '',
    'Claimed (₹)': it.get('claimed_amount') or 0,
    f'CGHS rate - {nabh_status} (₹)': it.get('cghs_rate_used'),
    'Admissible (₹)': it.get('admissible_amount'),
    'Flag': it.get('flag'),
} for it in matched])

st.write("Review every row. If a match looks wrong, correct the **Matched CGHS code** column directly and the rate/flag will update on the next save.")
edited_df = st.data_editor(
    df,
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "Flag": st.column_config.TextColumn(disabled=True),
    }
)

st.caption(
    "OK = within CGHS rate · OVERCHARGED = claimed above CGHS rate (capped at CGHS rate below) · "
    "POSSIBLE DUPLICATE = same CGHS code billed more than once, check for double-counted sub-tests · "
    "NO MATCH = could not find this item in the rate list, verify the code/description manually."
)

# Re-derive final admissible totals from (possibly edited) table, re-matching any manually corrected codes
final_items = []
for _, row in edited_df.iterrows():
    code = str(row.get('Matched CGHS code') or '').strip().upper()
    claimed = row.get('Claimed (₹)') or 0
    if code and code in BY_CODE:
        rate = BY_CODE[code]['rate_nabh'] if nabh_status == 'NABH' else BY_CODE[code]['rate_non_nabh']
        admissible = min(claimed, rate)
        desc = BY_CODE[code]['description']
    else:
        rate = None
        admissible = None
        desc = row.get('Description (as billed)')
    final_items.append({
        'description': row.get('Description (as billed)'),
        'code': code,
        'claimed_amount': claimed,
        'cghs_rate': rate,
        'admissible_amount': admissible,
        'matched_description': desc,
    })

total_claimed = sum(it['claimed_amount'] or 0 for it in final_items)
total_admissible = sum(it['admissible_amount'] or 0 for it in final_items if it['admissible_amount'] is not None)
unresolved = [it for it in final_items if it['admissible_amount'] is None]

m1, m2, m3 = st.columns(3)
m1.metric("Total claimed", f"₹{total_claimed:,.0f}")
m2.metric("Total admissible (as per CGHS rates)", f"₹{total_admissible:,.0f}")
diff = total_claimed - total_admissible
m3.metric("Difference", f"₹{diff:,.0f}", delta=f"-₹{diff:,.0f}" if diff > 0 else None, delta_color="inverse")

if unresolved:
    st.warning(f"{len(unresolved)} row(s) have no matched CGHS code and are excluded from the admissible total above. Resolve these before sanctioning.")

st.markdown("---")

# ---------------------------------------------------------------------------
# Step 4: Generate documents
# ---------------------------------------------------------------------------
st.header("4. Generate Sanction Memo & NS/Checklist")

col_a, col_b = st.columns(2)
with col_a:
    memo_no = st.text_input("Memo No.", value=f"L-2/NDHO/MR-XXX/{datetime.date.today().year}-{str(datetime.date.today().year+1)[-2:]}")
with col_b:
    memo_date = st.text_input("Memo date", value=datetime.date.today().strftime("%d.%m.%Y"))

use_admissible = st.checkbox("Sanction the CGHS-checked admissible amount (recommended)", value=True,
                              help="Uncheck only if you intend to sanction the full claimed amount regardless of the rate check above.")
final_amount = total_admissible if use_admissible else total_claimed

st.write(f"**Amount to be sanctioned: ₹{final_amount:,.0f}** ({rupees_in_words(final_amount)})")

if st.button("📄 Generate documents", type="primary", disabled=(not name or not emp_id)):
    case = {
        'memo_no': memo_no,
        'memo_date': memo_date,
        'amount': int(final_amount),
        'name': name,
        'designation': designation,
        'office': office,
        'pin': pin,
        'emp_id': emp_id,
        'hospital': hospital,
        'patient_relation': patient_relation,
        'patient_relation_display': patient_relation.upper(),
        'cghs_card_no': cghs_id,
        'cghs_validity': cghs_validity,
        'submission_date': submission_date,
        'treatment_type': treatment_type,
        'nabh_status': nabh_status,
        'hco_type': 'Yes',
        'total_admissible': int(total_admissible),
        'order_remark': 'in order' if abs(total_claimed - total_admissible) < 1 else
                         f'partially admissible (Rs {total_claimed - total_admissible:,.0f}/- in excess of CGHS rates has been disallowed - see item-wise sheet)',
        'recommended': 'Yes',
    }
    ns_items = [{
        'sl_no': i + 1,
        'particulars': it['matched_description'] or it['description'],
        'code': it['code'],
        'claimed': it['claimed_amount'],
        'admissible': it['admissible_amount'] if it['admissible_amount'] is not None else 'MANUAL REVIEW',
    } for i, it in enumerate(final_items)]

    os.makedirs('/tmp/cghs_out', exist_ok=True)
    sanction_path = '/tmp/cghs_out/Sanction_Memo.docx'
    ns_path = '/tmp/cghs_out/NS_Checklist.docx'
    build_sanction_memo(case, sanction_path)
    build_ns_checklist(case, ns_items, ns_path)

    st.success("Documents generated.")
    dl1, dl2 = st.columns(2)
    with dl1:
        with open(sanction_path, 'rb') as f:
            st.download_button("⬇️ Download Sanction Memo (.docx)", f, file_name=f"Sanction_Memo_{emp_id}.docx")
    with dl2:
        with open(ns_path, 'rb') as f:
            st.download_button("⬇️ Download NS/Checklist (.docx)", f, file_name=f"NS_Checklist_{emp_id}.docx")
