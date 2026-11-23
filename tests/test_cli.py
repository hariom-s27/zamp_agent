import json

from invoice_desk import config, run


def test_all_samples_from_the_command_line(capsys, manifest):
    assert len(manifest) == 18                                   # 10 invoices and 8 bad inputs
    assert run.main(["--all"]) == 0
    out = capsys.readouterr().out
    assert "18 of 18 samples gave the expected decision" in out and "MISMATCH" not in out


def test_one_file_prints_every_stage_and_the_decision(capsys):
    assert run.main([str(config.SAMPLES_DIR / "04_E1_split_po_overbilling.pdf")]) == 0
    out = capsys.readouterr().out
    for piece in ("1 Intake", "6 PO match", "9 Post and verify", "HOLD: 2 points need a person", "[QTY_OVER_PO]", "Next:"):
        assert piece in out


def test_json_output_is_the_full_run_record(capsys):
    assert run.main([str(config.SAMPLES_DIR / "01_happy_path_furniture.pdf"), "--json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["decision"]["verdict"] == "APPROVE" and len(record["stages"]) == 9 and record["receipt"]["hash"]


def test_missing_file_and_no_arguments(capsys):
    assert run.main(["does_not_exist.pdf"]) == 1
    assert run.main([]) == 2


def test_the_after_hold_samples_have_their_own_manifest():
    from invoice_desk import samples
    cases = samples.load_case_manifest()
    assert [(c["id"], c["expected_verdict"], c["expected_reasons"]) for c in cases] == [
        ("c1_invoice_before_goods", "HOLD", ["GOODS_NOT_RECEIVED"]),
        ("c2_small_supplier_late_invoice", "HOLD", ["PRICE_VARIANCE"])]
    assert all(samples.sample_bytes(c).startswith(b"%PDF") for c in cases)
    assert len(samples.load_manifest()) == 18                       # the first manifest is untouched


def test_the_after_hold_samples_from_the_command_line(capsys):
    assert run.main(["--cases"]) == 0
    out = capsys.readouterr().out
    assert "2 of 2 after-hold samples gave the expected decision" in out and "MISMATCH" not in out
    lines = {ln.split()[0]: ln for ln in out.splitlines() if ln.startswith("c")}
    assert "HOLD" in lines["c1_invoice_before_goods"] and lines["c1_invoice_before_goods"].endswith("GOODS_NOT_RECEIVED")
    assert "HOLD" in lines["c2_small_supplier_late_invoice"] and lines["c2_small_supplier_late_invoice"].endswith("PRICE_VARIANCE")
