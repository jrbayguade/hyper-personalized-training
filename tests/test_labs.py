from fitdash.labs import find_labs_file, load_labs, prompt_lines, summary

CSV = """Secció,Paràmetre,Resultat,Unitats,Valors de referència
Hemograma,Hemoglobina,"14,5",g/dL,12.5 - 17.2
Metabolismo lipoproteíco,Colesterol LDL,164,mg/dL,< 116 (RCV bajo)
Metabolismo lipoproteíco,Colesterol HDL,55,mg/dL,> 40
Metabolismo del hierro,Hierro,64,µg/dL,65 - 170
Vitaminas,Vitamina D 25OH (calcidiol),"22,6",ng/mL,30 - 100
Inmunohematología,Grupo sanguíneo,O,,
Pruebas de función renal,Filtrado glomerular estimado (CKD-EPI),>90,ml/min/1.73m2,> 60
Marcadores tumorales,CEA,"<1,73",ng/mL,< 5
Análisis de orina,Glucosa,Negativo,mg/dL,Negativo
Análisis de orina,Proteínas,Positivo,,Negativo
Análisis de orina,Urocultivo,Negativo (<100 ufc/ml),,Negativo
fila rota
"""


def write_csv(tmp_path, text=CSV):
    path = tmp_path / "analitica_test.csv"
    path.write_text(text, encoding="utf-8-sig")  # real exports start with a BOM
    return path


def statuses(markers):
    return {m.name: m.status for m in markers}


def test_statuses_follow_the_reference_ranges(tmp_path):
    s = statuses(load_labs(write_csv(tmp_path)))
    assert s["Hemoglobina"] == "ok"
    assert s["Colesterol LDL"] == "high"
    assert s["Colesterol HDL"] == "ok"
    assert s["Hierro"] == "low"
    assert s["Vitamina D 25OH (calcidiol)"] == "low"


def test_prefixed_results_and_text_results(tmp_path):
    s = statuses(load_labs(write_csv(tmp_path)))
    assert s["Filtrado glomerular estimado (CKD-EPI)"] == "ok"  # ">90" against "> 60"
    assert s["CEA"] == "ok"  # "<1,73" against "< 5"
    assert s["Grupo sanguíneo"] == "n/a"
    assert s["Glucosa"] == "ok"  # "Negativo" == reference
    assert s["Proteínas"] == "abnormal"  # "Positivo" vs "Negativo"
    assert s["Urocultivo"] == "ok"  # "Negativo (<100 ufc/ml)" starts with the reference


def test_comma_decimals_are_read(tmp_path):
    markers = {m.name: m for m in load_labs(write_csv(tmp_path))}
    assert markers["Hemoglobina"].value == 14.5
    assert markers["Colesterol LDL"].unit == "mg/dL"


def test_summary_counts_checked_and_flagged(tmp_path):
    result = summary(load_labs(write_csv(tmp_path)))
    assert result == {"checked": 10, "flagged": 4}


def test_prompt_lines_only_contain_flagged_markers(tmp_path):
    lines = prompt_lines(load_labs(write_csv(tmp_path)))
    text = "\n".join(lines)
    assert len(lines) == 4
    assert "Colesterol LDL" in text and "HIGH" in text
    assert "Hierro" in text and "LOW" in text
    assert "Hemoglobina" not in text


def test_missing_or_broken_file_gives_no_markers(tmp_path):
    assert load_labs(tmp_path / "nope.csv") == []
    assert load_labs(write_csv(tmp_path, "esto,no,es\nun,csv,valido\n")) == []
    assert summary([]) == {"checked": 0, "flagged": 0}
    assert prompt_lines([]) == []


def test_find_labs_file(tmp_path):
    assert find_labs_file(tmp_path) is None
    path = write_csv(tmp_path)
    assert find_labs_file(tmp_path) == path
