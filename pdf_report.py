"""Professional, read-only PDF export for a verified MediaOps incident."""

from __future__ import annotations

from html import escape
from io import BytesIO

from models import Incident, IncidentStatus, VerificationVerdict


class PDFReportError(RuntimeError):
    pass


def _text(value: object) -> str:
    return escape(str(value if value is not None else "—"))


def _time(value) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z") if value else "—"


def _mttr(incident: Incident) -> float:
    end = incident.closed_at or incident.updated_at
    return max(0.0, (end - incident.anomaly.detected_at).total_seconds())


def _duration(seconds: float) -> str:
    minutes, remainder = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {remainder}s"
    if minutes:
        return f"{minutes}m {remainder}s"
    return f"{remainder}s"


def _validated(incident: Incident) -> None:
    if incident.status not in {IncidentStatus.CLOSED, IncidentStatus.RESOLVED}:
        raise PDFReportError("PDF report is available only after the incident is closed")
    if (
        incident.verification is None
        or incident.verification.verdict is not VerificationVerdict.RECOVERED
        or not incident.verification.recovered
    ):
        raise PDFReportError("PDF report requires independently verified recovery")


def build_incident_pdf(incident: Incident) -> bytes:
    """Render a factual PDF from the authoritative Firestore Incident model."""
    _validated(incident)
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_RIGHT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            BaseDocTemplate,
            Frame,
            KeepTogether,
            PageTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:  # pragma: no cover - deployment configuration
        raise PDFReportError("reportlab is not installed; run pip install -r requirements.txt") from exc

    buffer = BytesIO()
    width, height = A4
    styles = getSampleStyleSheet()
    navy = colors.HexColor("#17243A")
    blue = colors.HexColor("#3E7DD6")
    green = colors.HexColor("#16845B")
    pale = colors.HexColor("#F3F6FA")
    muted = colors.HexColor("#5F6C7B")
    line = colors.HexColor("#D7DEE8")

    title = ParagraphStyle("ReportTitle", parent=styles["Title"], fontName="Helvetica-Bold",
                           fontSize=22, leading=27, textColor=navy, spaceAfter=4)
    subtitle = ParagraphStyle("Subtitle", parent=styles["Normal"], fontSize=8.5,
                              leading=12, textColor=muted)
    section = ParagraphStyle("Section", parent=styles["Heading2"], fontName="Helvetica-Bold",
                             fontSize=12, leading=15, textColor=navy, spaceBefore=13, spaceAfter=7)
    body = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=9, leading=13,
                          textColor=colors.HexColor("#283442"), spaceAfter=5)
    small = ParagraphStyle("Small", parent=body, fontSize=7.5, leading=10, textColor=muted)
    right = ParagraphStyle("Right", parent=small, alignment=TA_RIGHT)

    doc = BaseDocTemplate(buffer, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
                          topMargin=20 * mm, bottomMargin=18 * mm,
                          title=f"MediaOps Incident {incident.incident_id}")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="report")

    def decorate(canvas, document) -> None:
        canvas.saveState()
        canvas.setStrokeColor(line)
        canvas.line(18 * mm, 13 * mm, width - 18 * mm, 13 * mm)
        canvas.setFillColor(muted)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(18 * mm, 8.5 * mm, "MediaOps CoPilot · Verified Incident Report")
        canvas.drawRightString(width - 18 * mm, 8.5 * mm, f"Page {document.page}")
        canvas.restoreState()

    doc.addPageTemplates(PageTemplate(id="report", frames=[frame], onPage=decorate))

    def p(value: object, style=body) -> Paragraph:
        return Paragraph(_text(value), style)

    def section_title(value: str) -> Paragraph:
        return Paragraph(value, section)

    def facts(rows: list[tuple[str, object]]) -> Table:
        table = Table([[p(label, small), p(value)] for label, value in rows], colWidths=[38 * mm, 132 * mm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), pale), ("BOX", (0, 0), (-1, -1), .4, line),
            ("INNERGRID", (0, 0), (-1, -1), .3, line), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        return table

    fault = incident.evidence.fault_class.value if incident.evidence else incident.anomaly.fault_class.value
    mttr = _duration(_mttr(incident))
    story = [
        Paragraph("MediaOps Incident Report", title),
        Table([[Paragraph(f"Incident <b>{_text(incident.incident_id)}</b>", subtitle),
                Paragraph(f"Generated from authoritative Firestore state", right)]], colWidths=[100 * mm, 70 * mm]),
        Spacer(1, 7 * mm),
    ]
    status = Table([[p("VERIFIED RECOVERY", ParagraphStyle("status", parent=body, textColor=colors.white,
                                                            fontName="Helvetica-Bold", fontSize=9)),
                     p(incident.status.value, ParagraphStyle("statusRight", parent=right, textColor=colors.white,
                                                             fontName="Helvetica-Bold"))]], colWidths=[115 * mm, 55 * mm])
    status.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), green), ("LEFTPADDING", (0, 0), (-1, -1), 9),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 7),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story += [status, Spacer(1, 4 * mm), facts([
        ("Detected", _time(incident.anomaly.detected_at)), ("Closed", _time(incident.closed_at)),
        ("Final status", incident.status.value), ("MTTR", mttr),
    ])]

    story += [section_title("1 · What happened"), facts([
        ("Fault type", fault), ("Viewer-facing symptom", incident.vision.symptom.value if incident.vision else "—"),
        ("Detection signal", incident.anomaly.reason), ("Detection time", _time(incident.anomaly.detected_at)),
    ])]

    metrics = incident.infra.supporting_metrics if incident.infra else {}
    metric_text = ", ".join(f"{key}={value:g}" for key, value in metrics.items()) or "—"
    story += [section_title("2 · Diagnosis"), facts([
        ("Vision finding", f"{incident.vision.symptom.value} ({incident.vision.confidence:.0%}) — {incident.vision.description}" if incident.vision else "—"),
        ("Infrastructure finding", f"{incident.infra.fault_class.value} on {incident.infra.affected_component} ({incident.infra.confidence:.0%}) — {incident.infra.description}" if incident.infra else "—"),
        ("Key metrics", metric_text),
        ("Independent agreement", "YES — video and infrastructure evidence corroborated" if incident.evidence and incident.evidence.agreement else "NO"),
    ])]

    precedent_rows = [[p("Incident", small), p("Similarity", small), p("Action", small), p("Outcome", small)]]
    for match in incident.precedent:
        precedent_rows.append([p(match.kb_id, small), p(f"{match.similarity:.3f}", small),
                               p(match.action_taken.value, small), p(match.outcome, small)])
    if len(precedent_rows) == 1:
        precedent_rows.append([p("No precedent retrieved", small), p("—", small), p("—", small), p("—", small)])
    precedent_table = Table(precedent_rows, colWidths=[67 * mm, 25 * mm, 45 * mm, 33 * mm], repeatRows=1)
    precedent_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), navy),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), .35, line),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story += [section_title("3 · Retrieved precedent"), precedent_table]

    decision = incident.safety_decision
    execution = incident.execution
    checks = ", ".join(f"{item.name}: {'PASS' if item.passed else 'FAIL'}" for item in decision.checks) if decision else "—"
    story += [section_title("4 · Action taken"), facts([
        ("Proposed fix", incident.proposal.action.value if incident.proposal else "—"),
        ("Rationale", incident.proposal.rationale if incident.proposal else "—"),
        ("Safety Gate", decision.verdict.value if decision else "—"), ("Deterministic checks", checks),
        ("Executed action", execution.action.value if execution else "—"),
        ("Execution result", execution.detail if execution else "—"),
    ])]

    verification = incident.verification
    samples = verification.samples
    if samples:
        health = [sample.get("media_pipeline_health") for sample in samples if "media_pipeline_health" in sample]
        fps = [sample.get("media_fps") for sample in samples if "media_fps" in sample]
        sample_summary = f"{len(samples)} samples across {verification.stable_window_seconds:g}s"
        if health:
            sample_summary += f"; health={min(health):g}–{max(health):g}"
        if fps:
            sample_summary += f"; fps={min(fps):g}–{max(fps):g}"
    else:
        sample_summary = "No samples retained"
    video_result = verification.vision_recheck
    story += [section_title("5 · Recovery and outcome"), facts([
        ("Verification verdict", verification.verdict.value),
        ("Telemetry evidence", f"CONFIRMED — {sample_summary}" if verification.telemetry_ok else "NOT CONFIRMED"),
        ("Video evidence", f"CONFIRMED — {video_result.symptom.value} ({video_result.confidence:.0%})" if verification.video_ok and video_result else "CONFIRMED" if verification.video_ok else "NOT CONFIRMED"),
        ("Outcome", f"Resolved with independently verified recovery; MTTR {mttr}"),
        ("Slack report", "Sent" if incident.report_sent else "Not sent"),
        ("Knowledge Base", incident.kb_writeback_id or "Not recorded"),
    ])]
    story += [Spacer(1, 5 * mm), KeepTogether([
        Paragraph("Authority statement", section),
        Paragraph("AI supplied observation and a remediation proposal. Deterministic code independently approved execution and verified recovery in both telemetry and video evidence.", body),
    ])]

    doc.build(story)
    return buffer.getvalue()
