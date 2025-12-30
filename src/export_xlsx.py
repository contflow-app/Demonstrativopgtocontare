\
from __future__ import annotations
import pandas as pd
from typing import List
from openpyxl import load_workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.utils.dataframe import dataframe_to_rows
from pathlib import Path

def export_consolidado_xlsx(rows: List[dict], out_path: str, logo_path: str | None = None):
    df = pd.DataFrame(rows)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Consolidado")

    wb = load_workbook(out_path)
    ws = wb["Consolidado"]

    # Freeze header row
    ws.freeze_panes = "A2"

    # Auto-width (simple)
    for col in ws.columns:
        max_len = 0
        letter = col[0].column_letter
        for cell in col[:200]:
            val = "" if cell.value is None else str(cell.value)
            max_len = max(max_len, len(val))
        ws.column_dimensions[letter].width = min(max(12, max_len + 2), 45)

    # Optional logo
    if logo_path and Path(logo_path).exists():
        img = XLImage(logo_path)
        img.anchor = "A1"
        ws.add_image(img)

    wb.save(out_path)
