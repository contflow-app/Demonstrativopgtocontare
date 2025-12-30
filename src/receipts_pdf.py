from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Optional

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.units import mm


def _fmt_money(v: Optional[float]) -> str:
    if v is None:
        return "-"
    try:
        s = f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {s}"
    except Exception:
        return "-"


def _get_num(row: Dict, *keys: str) -> Optional[float]:
    for k in keys:
        if k in row and row.get(k) is not None:
            try:
                return float(row.get(k))
            except Exception:
                return None
    return None


def generate_receipt_pdf(
    row: Dict,
    out_pdf: str,
    logo_path: Optional[str] = None,
    empresa_nome: str = "Contare",
):
    c = Canvas(out_pdf, pagesize=A4)
    w, h = A4
    y = h - 20 * mm

    # Logo
    if logo_path and Path(logo_path).exists():
        c.drawImage(
            logo_path,
            20 * mm,
            y - 20 * mm,
            width=45 * mm,
            height=18 * mm,
            preserveAspectRatio=True,
            mask="auto",
        )

    # Header
    c.setFont("Helvetica-Bold", 14)
    c.drawString(75 * mm, y, empresa_nome)
    c.setFont("Helvetica", 11)
    c.drawString(75 * mm, y - 6 * mm, "Demonstrativo de Pagamento – Complemento Extra-Folha")
    comp = row.get("competencia") or row.get("competencia_global") or "-"
    c.drawString(75 * mm, y - 12 * mm, f"Competência: {comp}")

    y -= 28 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, y, "Dados do Colaborador")
    y -= 8 * mm
    c.setFont("Helvetica", 10)

    def line(label, value):
        nonlocal y
        c.drawString(20 * mm, y, f"{label}: {value}")
        y -= 6 * mm

    line("Nome", row.get("nome") or "-")
    line("CPF", row.get("cpf") or "-")
    line("Matrícula", row.get("matricula") or "-")
    line("Departamento/Família", row.get("familia") or "-")
    line("Cargo (Plano)", row.get("cargo_final") or "-")

    y -= 6 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, y, "Regra de Cálculo")
    y -= 8 * mm
    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, y, "Valor a pagar = Bruto (planilha Excel) − Total pago reconhecido (folha/CSV)")
    y -= 10 * mm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, y, "Valores")
    y -= 8 * mm
    c.setFont("Helvetica", 10)

    bruto_planilha = _get_num(row, "salario_real_bruto_planilha", "salario_real_bruto")
    liquido_pdf = _get_num(row, "liquido_folha", "liquido_folha_pdf", "liquido")
    pago_csv = _get_num(row, "pagamentos_folha_csv")
    total_pago = _get_num(row, "total_pago_folha")

    # Se total_pago não vier preenchido (por qualquer motivo), recalcula de forma segura:
    if total_pago is None:
        total_pago = (liquido_pdf or 0.0) + (pago_csv or 0.0)

    diferenca = _get_num(row, "diferenca_calculada")
    if diferenca is None and bruto_planilha is not None and total_pago is not None:
        diferenca = float(bruto_planilha) - float(total_pago)

    valor_a_pagar = _get_num(row, "valor_a_pagar")
    if valor_a_pagar is None and diferenca is not None:
        valor_a_pagar = max(float(diferenca), 0.0)

    line("Bruto referencial (planilha Excel)", _fmt_money(bruto_planilha))
    line("Líquido na folha (PDF)", _fmt_money(liquido_pdf))

    # Só mostra a linha do CSV se existir (evita poluir recibo quando não há anexo)
    if pago_csv is not None:
        line("Pagamentos em folha (CSV/anexo)", _fmt_money(pago_csv))

    line("Total pago reconhecido", _fmt_money(total_pago))
    line("Diferença calculada", _fmt_money(diferenca))
    line("VALOR A PAGAR", _fmt_money(valor_a_pagar))

    # Evidência opcional (boa para auditoria)
    ev_liq = row.get("evidencia_liquido")
    if ev_liq:
        y -= 2 * mm
        c.setFont("Helvetica-Oblique", 8)
        c.drawString(20 * mm, y, f"Fonte (PDF): {ev_liq}")
        c.setFont("Helvetica", 10)
        y -= 6 * mm

    y -= 6 * mm
    c.setFont("Helvetica", 9)
    c.drawString(
        20 * mm,
        y,
        "Declaro ter recebido o valor acima a título de complemento de pagamento (extra-folha).",
    )
    y -= 18 * mm

    c.line(20 * mm, y, 90 * mm, y)
    c.drawString(20 * mm, y - 5 * mm, "Assinatura do Colaborador")

    c.line(110 * mm, y, 190 * mm, y)
    c.drawString(110 * mm, y - 5 * mm, "Responsável / Empresa")

    y -= 20 * mm
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(20 * mm, y, "Documento gerado automaticamente pelo app Demonstrativo de Pagamento Contare.")
    c.showPage()
    c.save()


def generate_all_receipts(
    rows: List[Dict],
    out_dir: str,
    logo_path: Optional[str] = None,
    empresa_nome: str = "Contare",
) -> List[str]:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    outputs = []
    for idx, r in enumerate(rows, start=1):
        cpf = (r.get("cpf") or "").replace(".", "").replace("-", "")
        matricula = (r.get("matricula") or f"{idx}")
        name = (r.get("nome") or "COLAB").replace("/", "-")
        key = cpf if cpf else matricula
        filename = f"recibo_{key}_{name[:30].strip().replace(' ', '_')}.pdf"
        path = str(Path(out_dir) / filename)
        generate_receipt_pdf(r, path, logo_path=logo_path, empresa_nome=empresa_nome)
        outputs.append(path)
    return outputs
