"""Downloadable PDF and Excel analytics reports for FieldData AI."""
from datetime import datetime, timezone
from io import BytesIO
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak


def _report_data(conn, user_id, dataset_id, storage, source):
    from . import analytics_service, datasets, services
    result = analytics_service.dataset_analytics(conn, user_id, dataset_id, storage, source)
    row = datasets._row(conn, user_id, dataset_id)
    project = services.get_project(conn, user_id, row["project_id"])
    return result, project


def _pdf_table(rows, widths=None):
    table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324d")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEADING", (0, 0), (-1, -1), 11),
        ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#d8dee6")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f6f9")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def build_pdf(conn, user_id, dataset_id, storage, source="original"):
    a, project = _report_data(conn, user_id, dataset_id, storage, source)
    o = a["overview"]
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm,
                            topMargin=16*mm, bottomMargin=16*mm, title="FieldData AI Analytics Report")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="BrandTitle", parent=styles["Title"], textColor=colors.HexColor("#17324d"), alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="SmallMuted", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#64748b")))
    story = [Paragraph("FieldData AI", styles["BrandTitle"]),
             Paragraph("Dataset Analytics Report", styles["Heading2"]),
             Paragraph(f"Project: {escape(str(project['name']))} | Dataset: {escape(str(a.get('dataset', 'Dataset')))}", styles["Normal"]),
             Paragraph(f"Location: {escape(str(project.get('location') or 'Not specified'))} | Source: {escape(source.title())} data", styles["Normal"]),
             Paragraph(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", styles["SmallMuted"]), Spacer(1, 8)]
    story += [Paragraph("1. Dataset overview", styles["Heading2"]), _pdf_table([
        ["Metric", "Result"], ["Total records", str(o["records_total"])], ["Records analysed", str(o["records_used"])],
        ["Duplicates excluded", str(o["duplicates_excluded"])], ["Records with errors", str(o["records_with_errors"])],
        ["Completeness", f"{o['completeness_pct']}%"], ["Columns", str(o["columns"])],
        ["Invalid cells excluded", str(o["cells_excluded"])],
    ], [75*mm, 85*mm]), Spacer(1, 8)]
    story.append(Paragraph("2. Key findings", styles["Heading2"]))
    if a.get("findings"):
        story.extend(Paragraph("• " + escape(str(item)), styles["BodyText"]) for item in a["findings"])
    else:
        story.append(Paragraph("No key findings were generated for this dataset.", styles["BodyText"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph("3. Missing answers by column", styles["Heading2"]))
    missing = a.get("quality", {}).get("missing_by_column", [])
    story.append(_pdf_table([["Column", "Missing", "Missing %"]] + [[str(x[0]), str(x[1]), f"{x[2]}%"] for x in missing] if missing else [["Column", "Missing", "Missing %"], ["No missing answers detected", "0", "0%"]]))
    story.append(Spacer(1, 8))
    story.append(Paragraph("4. Numeric summaries", styles["Heading2"]))
    numeric = a.get("numeric", {})
    nrows = [["Variable", "Valid n", "Mean", "Median", "Min", "Max"]]
    nrows += [[str(k), str(v.get("n", "")), str(v.get("mean", "")), str(v.get("median", "")), str(v.get("min", "")), str(v.get("max", ""))] for k, v in numeric.items()]
    story.append(_pdf_table(nrows if len(nrows) > 1 else [["Variable", "Valid n", "Mean", "Median", "Min", "Max"], ["No numeric variables", "-", "-", "-", "-", "-"]]))
    trend = a.get("trend")
    if trend:
        story += [Spacer(1, 8), Paragraph("5. Submission trend", styles["Heading2"]),
                  Paragraph(f"Date field: {escape(str(trend.get('column')))} | Peak day: {escape(str(trend.get('peak_day')))} "
                            f"({trend.get('peak_count')} submissions) | Average per day: {trend.get('average_per_day')}", styles["BodyText"])]
    story += [Spacer(1, 12), Paragraph("Interpretation note: Statistics exclude invalid values and duplicate records where applicable. Review the original dataset and data-quality findings before making operational decisions.", styles["SmallMuted"])]
    doc.build(story)
    return buf.getvalue()


def build_xlsx(conn, user_id, dataset_id, storage, source="original"):
    a, project = _report_data(conn, user_id, dataset_id, storage, source)
    o = a["overview"]
    wb = Workbook()
    ws = wb.active; ws.title = "Overview"
    rows = [["FieldData AI - Analytics Report"], ["Project", project["name"]], ["Location", project.get("location") or "Not specified"],
            ["Dataset", a.get("dataset", "Dataset")], ["Source", source], ["Generated (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")],
            [], ["Metric", "Result"], ["Total records", o["records_total"]], ["Records analysed", o["records_used"]],
            ["Duplicates excluded", o["duplicates_excluded"]], ["Records with errors", o["records_with_errors"]],
            ["Completeness (%)", o["completeness_pct"]], ["Columns", o["columns"]], ["Invalid cells excluded", o["cells_excluded"]]]
    for row in rows: ws.append(row)
    findings = wb.create_sheet("Key Findings"); findings.append(["Finding"])
    for item in a.get("findings", []): findings.append([item])
    missing = wb.create_sheet("Missing by Column"); missing.append(["Column", "Missing", "Missing %"])
    for item in a.get("quality", {}).get("missing_by_column", []): missing.append(item)
    numeric = wb.create_sheet("Numeric Summaries"); numeric.append(["Variable", "Valid values", "Mean", "Median", "Std dev", "Minimum", "Maximum"])
    for k, v in a.get("numeric", {}).items(): numeric.append([k, v.get("n"), v.get("mean"), v.get("median"), v.get("std"), v.get("min"), v.get("max")])
    trend = wb.create_sheet("Submission Trend"); trend.append(["Date", "Submissions"])
    if a.get("trend"):
        for item in a["trend"].get("daily", []): trend.append(item)
    for sheet in wb.worksheets:
        sheet.freeze_panes = "A2"
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="17324D")
        for col in sheet.columns:
            letter = col[0].column_letter
            width = min(max(max(len(str(c.value or "")) for c in col) + 2, 12), 55)
            sheet.column_dimensions[letter].width = width
            for cell in col: cell.alignment = Alignment(vertical="top", wrap_text=True)
    buf = BytesIO(); wb.save(buf); return buf.getvalue()
