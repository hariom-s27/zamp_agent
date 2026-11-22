import json

from invoice_desk import config, run


def test_all_samples_from_the_command_line(capsys):
    assert run.main(["--all"]) == 0
    out = capsys.readouterr().out
    assert "14 of 14 samples gave the expected decision" in out and "MISMATCH" not in out


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
