\
from __future__ import annotations
import os
from pathlib import Path
from typing import Dict, List, Optional
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.units import mm

def _fmt_money(v: Optional[float]) -> str:
    if v is None:
        return "-"
    # pt-BR formatting
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def generate_receipt_pdf(row: Dict, out_pdf: str, logo_path: Optional[str] = None, empresa_nome: str = "Contare"):
    c = Canvas(out_pdf, pagesize=A4)
    w, h = A4

    y = h - 20*mm

    # Logo
    if logo_path and Path(logo_path).exists():
        # keep aspect, set width
        c.drawImage(logo_path, 20*mm, y-20*mm, width=45*mm, height=18*mm, preserveAspectRatio=True, mask='auto')

    # Header
    c.setFont("Helvetica-Bold", 14)
    c.drawString(75*mm, y, f"{empresa_nome}")
    c.setFont("Helvetica", 11)
    c.drawString(75*mm, y-6*mm, "Demonstrativo de Pagamento – Complemento Extra-Folha")
    comp = row.get("competencia") or row.get("competencia_global") or "-"
    c.drawString(75*mm, y-12*mm, f"Competência: {comp}")

    y -= 28*mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20*mm, y, "Dados do Colaborador")
    y -= 8*mm
    c.setFont("Helvetica", 10)

    def line(label, value):
        nonlocal y
        c.drawString(20*mm, y, f"{label}: {value}")
        y -= 6*mm

    line("Nome", row.get("nome") or "-")
    line("CPF", row.get("cpf") or "-")
    line("Matrícula", row.get("matricula") or "-")
    line("Departamento/Família", row.get("familia") or "-")
    line("Cargo (Plano)", row.get("cargo_final") or "-")

    y -= 6*mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20*mm, y, "Valores")
    y -= 8*mm
    c.setFont("Helvetica", 10)

    line("Salário CLT (contratual)", _fmt_money(row.get("salario_clt")))
    line("Líquido na folha", _fmt_money(row.get("liquido")))
    line("Bruto total (se disponível)", _fmt_money(row.get("bruto_total")))
    line("Salário real (bruto) – planilha", _fmt_money(row.get("salario_real_bruto")))
    line("Base escolhida para cálculo", _fmt_money(row.get("base_calculo")))
    line("Complemento (extra-folha)", _fmt_money(row.get("complemento")))

    y -= 6*mm
    c.setFont("Helvetica", 9)
    c.drawString(20*mm, y, "Declaro ter recebido o valor acima a título de complemento de pagamento (extra-folha).")
    y -= 18*mm

    c.line(20*mm, y, 90*mm, y)
    c.drawString(20*mm, y-5*mm, "Assinatura do Colaborador")

    c.line(110*mm, y, 190*mm, y)
    c.drawString(110*mm, y-5*mm, "Responsável / Empresa")

    y -= 20*mm
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(20*mm, y, "Documento gerado automaticamente pelo app Demonstrativo de Pagamento Contare.")

    c.showPage()
    c.save()

def generate_all_receipts(rows: List[Dict], out_dir: str, logo_path: Optional[str] = None, empresa_nome: str = "Contare") -> List[str]:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    outputs = []
    for r in rows:
        cpf = (r.get("cpf") or "SEMCPF").replace(".", "").replace("-", "")
        name = (r.get("nome") or "COLAB").replace("/", "-")
        filename = f"recibo_{cpf}_{name[:30].strip().replace(' ', '_')}.pdf"
        path = str(Path(out_dir) / filename)
        generate_receipt_pdf(r, path, logo_path=logo_path, empresa_nome=empresa_nome)
        outputs.append(path)
    return outputs
