import io
import re
import xml.etree.ElementTree as ET

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

NS = {'ns': 'http://jasperreports.sourceforge.net/jasperreports/print'}


def clean_numeric(value):
    if not value:
        return 0.0
    try:
        clean_val = re.sub(r'[^\d.-]', '', str(value))
        return float(clean_val) if clean_val else 0.0
    except ValueError:
        return 0.0


def get_text(elem):
    if elem is None:
        return None
    ce = elem.find('ns:textContent', NS)
    return ce.text.strip() if ce is not None and ce.text else None


def safe_text(all_texts, idx):
    try:
        return get_text(all_texts[idx]) or ""
    except IndexError:
        return ""


def safe_num(all_texts, idx):
    try:
        return clean_numeric(get_text(all_texts[idx]))
    except IndexError:
        return 0.0


def _build_tk_map(all_texts):
    """
    Build a dict mapping timekeeper code → full name from origin=66, x=46 entries.
    Each entry looks like: '1466 (Streegan-Hagen, Cooper)'
    """
    tk_map = {}
    for t in all_texts:
        re_elem = t.find('ns:reportElement', NS)
        if re_elem is None:
            continue
        if re_elem.get('origin') == '66' and re_elem.get('x') == '46':
            ce = t.find('ns:textContent', NS)
            val = ce.text.strip() if ce is not None and ce.text else ''
            m = re.match(r'^(\S+)\s+\((.+)\)$', val)
            if m:
                tk_map[m.group(1)] = m.group(2)
    return tk_map


def _flush_pending(last_item_bs, last_item_full,
                   pending_deductions, current_deduction,
                   billsync_items, full_items, current_inv):
    """
    Finalize a pending line item.
    Each deduction produces its OWN output row (same base data, individual
    Audit Category / Audit Comments / Reduced Amount).
    Returns (None, None, [], None) — cleared state.
    """
    if current_deduction is not None:
        pending_deductions.append(current_deduction)

    if pending_deductions:
        billed_total = current_inv["Billed Total"] if current_inv else 0.0
        base_narrative = last_item_bs["Narrative"] if last_item_bs else ""

        for d in pending_deductions:
            if last_item_bs:
                audit_part = f" Audit Reason : {d['category']}"
                if d['audit_text']:
                    audit_part += f" \n {d['audit_text']}"
                row_bs = last_item_bs.copy()
                row_bs["Description"]          = d['category']
                row_bs["Reduced Amount"]       = d['reduced']
                row_bs["Total Invoice Amount"] = billed_total
                row_bs["Narrative"]            = f"{base_narrative}\n \n{audit_part}"
                billsync_items.append(row_bs)

            if last_item_full:
                row_full = last_item_full.copy()
                row_full["Audit Category"] = d['category']
                row_full["Audit Comments"] = d['audit_text']
                row_full["Reduced Amount"] = d['reduced']
                full_items.append(row_full)

    else:
        if last_item_full is not None:
            full_items.append(last_item_full.copy())

    return None, None, [], None


def extract_all(xml_source):
    """
    Returns (summaries, billsync_items, full_items).
    summaries        – one dict per invoice (Bill Summary sheet)
    billsync_items   – 22-column BillSync line items (reduced items only)
    full_items       – 14-column All Line Items (every line item)
    """
    try:
        tree = ET.parse(xml_source)
        root = tree.getroot()
    except Exception as e:
        raise RuntimeError(f"Error parsing XML: {e}")

    all_texts = root.findall('.//ns:text', NS)
    tk_map = _build_tk_map(all_texts)

    summaries      = []
    billsync_items = []
    full_items     = []

    current_inv        = None
    last_item_bs       = None
    last_item_full     = None
    pending_deductions = []
    current_deduction  = None

    i = 0
    while i < len(all_texts):
        content = get_text(all_texts[i])
        if not content:
            i += 1
            continue

        re_elem = all_texts[i].find('ns:reportElement', NS)
        origin  = re_elem.get('origin') if re_elem is not None else None

        # ── New invoice ──────────────────────────────────────────────────
        if content == "Client:":
            last_item_bs, last_item_full, pending_deductions, current_deduction = \
                _flush_pending(last_item_bs, last_item_full,
                               pending_deductions, current_deduction,
                               billsync_items, full_items, current_inv)
            if current_inv and current_inv.get("Firm Invoice Number"):
                summaries.append(current_inv)
            current_inv = {
                "Client": safe_text(all_texts, i + 1),
                "Firm Invoice Number": "",
                "Firm Invoice Date": "",
                "Billed Total": 0.0,
                "Deductions Total": 0.0,
                "To Pay Total": 0.0,
                "Released Date": "",
                "Matter Number": ""
            }
            i += 1
            continue

        if current_inv is None:
            i += 1
            continue

        # ── Invoice header fields ────────────────────────────────────────
        if content == "Firm Invoice Number:":
            current_inv["Firm Invoice Number"] = safe_text(all_texts, i + 1)

        elif content == "Firm Invoice Date:":
            current_inv["Firm Invoice Date"] = safe_text(all_texts, i + 1)

        elif content == "Firm File Number:":
            current_inv["Matter Number"] = safe_text(all_texts, i + 1)

        elif content == "Release Date:":
            current_inv["Released Date"] = safe_text(all_texts, i + 1)

        elif content == "Totals":
            current_inv["Billed Total"]     = safe_num(all_texts, i + 1)
            current_inv["Deductions Total"] = safe_num(all_texts, i + 2)
            current_inv["To Pay Total"]     = safe_num(all_texts, i + 6)

        # ── Line item start (origin 36, x=40, numeric content) ──────────
        elif origin == "36":
            x = int(re_elem.get('x', 0))
            if x == 40 and content.isdigit():
                last_item_bs, last_item_full, pending_deductions, current_deduction = \
                    _flush_pending(last_item_bs, last_item_full,
                                   pending_deductions, current_deduction,
                                   billsync_items, full_items, current_inv)

                inv_num  = current_inv["Firm Invoice Number"]
                client   = current_inv["Client"]
                inv_date = current_inv["Firm Invoice Date"]
                matter   = current_inv["Matter Number"]

                tk_code = safe_text(all_texts, i + 3)
                tk_name = tk_map.get(tk_code, tk_code)

                try:
                    last_item_bs = {
                        "Invoice Number": inv_num,
                        "Company": client,
                        "User": "",
                        "Invoice Date": inv_date,
                        "Working Timekeeper": tk_name,
                        "Billing Timekeeper": "",
                        "Description": "",
                        "Date of Item": safe_text(all_texts, i + 1),
                        "Last Date to add Attorney Information": "",
                        "Appeal Status": "",
                        "Matter Number": matter,
                        "Task ID": safe_text(all_texts, i + 2),
                        "Item Type": safe_text(all_texts, i + 8),
                        "UNITS": safe_num(all_texts, i + 5),
                        "RATE": safe_num(all_texts, i + 6),
                        "AMOUNT": safe_num(all_texts, i + 7),
                        "Reduced Amount": 0.0,
                        "Total Invoice Amount": 0.0,
                        "Narrative": safe_text(all_texts, i + 4),
                        "Attorney Comment": "",
                        "Attachment": "",
                        "Attachment : URL": ""
                    }
                except Exception:
                    last_item_bs = None

                try:
                    last_item_full = {
                        "Invoice Number": inv_num,
                        "Client": client,
                        "Invoice Date": inv_date,
                        "Timekeeper": tk_name,
                        "Date of Item": safe_text(all_texts, i + 1),
                        "Matter Number": matter,
                        "Item Type": safe_text(all_texts, i + 8),
                        "UNITS": safe_num(all_texts, i + 5),
                        "RATE": safe_num(all_texts, i + 6),
                        "AMOUNT": safe_num(all_texts, i + 7),
                        "Reduced Amount": 0.0,
                        "Narrative": safe_text(all_texts, i + 4),
                        "Audit Category": "",
                        "Audit Comments": ""
                    }
                except Exception:
                    last_item_full = None

        # ── Deduction category start (origin 40) ─────────────────────────
        elif origin == "40" and content.startswith("Deduction:"):
            if current_deduction is not None:
                pending_deductions.append(current_deduction)
            cat     = content.replace("Deduction:", "").strip()
            reduced = abs(safe_num(all_texts, i + 3))
            current_deduction = {"category": cat, "reduced": reduced, "audit_text": ""}

        # ── Audit reason text (origin 41) ────────────────────────────────
        elif origin == "41":
            audit_text = content.strip()
            if current_deduction is not None:
                current_deduction["audit_text"] += (" " if current_deduction["audit_text"] else "") + audit_text
                pending_deductions.append(current_deduction)
                current_deduction = None
            elif pending_deductions:
                pending_deductions[-1]["audit_text"] += " " + audit_text

        i += 1

    _flush_pending(last_item_bs, last_item_full,
                   pending_deductions, current_deduction,
                   billsync_items, full_items, current_inv)

    if current_inv and current_inv.get("Firm Invoice Number"):
        summaries.append(current_inv)

    return summaries, billsync_items, full_items


# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

BILLSYNC_COLS = [
    "Invoice Number", "Company", "User", "Invoice Date", "Working Timekeeper",
    "Billing Timekeeper", "Description", "Date of Item",
    "Last Date to add Attorney Information", "Appeal Status", "Matter Number",
    "Task ID", "Item Type", "UNITS", "RATE", "AMOUNT", "Reduced Amount",
    "Total Invoice Amount", "Narrative", "Attorney Comment", "Attachment",
    "Attachment : URL"
]

FULL_COLS = [
    "Invoice Number", "Client", "Invoice Date", "Timekeeper", "Date of Item",
    "Matter Number", "Item Type", "UNITS", "RATE", "AMOUNT", "Reduced Amount",
    "Narrative", "Audit Category", "Audit Comments"
]

SUMMARY_COLS = [
    "Client", "Firm Invoice Number", "Firm Invoice Date",
    "Billed Total", "Deductions Total", "To Pay Total", "Released Date"
]


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Legal-X Invoice Extractor", page_icon="📄", layout="wide")

st.title("📄 Legal-X Invoice Extractor")
st.caption("Extract invoice data from Legal-X portal XML files into Excel")

uploaded_files = st.file_uploader(
    "Upload one or more Legal-X XML files",
    type=["xml"],
    accept_multiple_files=True
)

st.subheader("Output Options")
col1, col2, col3 = st.columns(3)
with col1:
    opt_summary = st.checkbox("Bill Summary", value=True,
                              help="One row per invoice — totals, dates, client")
with col2:
    opt_billsync = st.checkbox("Line Items — BillSync", value=False,
                               help="22-column format with embedded audit reason; one row per deduction")
with col3:
    opt_full = st.checkbox("All Line Items", value=False,
                           help="14-column format — every line item; audit fields blank if no reduction")

if st.button("Extract", type="primary", disabled=not uploaded_files):
    if not any([opt_summary, opt_billsync, opt_full]):
        st.warning("Please select at least one output option.")
        st.stop()

    all_summaries, all_billsync, all_full, errors = [], [], [], []

    progress = st.progress(0, text="Starting…")
    for idx, uf in enumerate(uploaded_files, 1):
        progress.progress(idx / len(uploaded_files), text=f"Processing {idx}/{len(uploaded_files)}: {uf.name}")
        try:
            s, b, f = extract_all(io.BytesIO(uf.getvalue()))
            all_summaries.extend(s)
            all_billsync.extend(b)
            all_full.extend(f)
        except Exception as e:
            errors.append(f"{uf.name}: {e}")
    progress.empty()

    if errors:
        st.error(f"{len(errors)} file(s) had errors:\n\n" + "\n".join(f"- {e}" for e in errors))

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        if opt_summary:
            df = pd.DataFrame(all_summaries, columns=SUMMARY_COLS)
            df.to_excel(writer, sheet_name='Bill Summary', index=False)
        if opt_billsync:
            df = pd.DataFrame(all_billsync, columns=BILLSYNC_COLS)
            df.to_excel(writer, sheet_name='Line Items - BillSync', index=False)
        if opt_full:
            df = pd.DataFrame(all_full, columns=FULL_COLS)
            df.to_excel(writer, sheet_name='All Line Items', index=False)
    buf.seek(0)

    st.success(
        f"Processed {len(uploaded_files)} file(s) — "
        f"{len(all_summaries)} invoice(s), "
        f"{len(all_billsync)} BillSync row(s), "
        f"{len(all_full)} line item(s)."
    )

    st.download_button(
        "⬇️ Download LegalX_Report.xlsx",
        data=buf,
        file_name="LegalX_Report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # Preview tabs
    tabs_to_show = []
    if opt_summary:  tabs_to_show.append(("Bill Summary", pd.DataFrame(all_summaries, columns=SUMMARY_COLS)))
    if opt_billsync: tabs_to_show.append(("Line Items — BillSync", pd.DataFrame(all_billsync, columns=BILLSYNC_COLS)))
    if opt_full:     tabs_to_show.append(("All Line Items", pd.DataFrame(all_full, columns=FULL_COLS)))

    if tabs_to_show:
        st.subheader("Preview")
        for tab, (name, df) in zip(st.tabs([t[0] for t in tabs_to_show]), tabs_to_show):
            with tab:
                st.dataframe(df, use_container_width=True)
