"""Record OCR output for the bundled scanned samples into fixtures/ocr/.

    python -m scripts.record_ocr_fixtures

The recordings let the scanned samples run on a machine or host with no OCR engine, and
give tests identical input everywhere. Each file stores the engine and the time of the
recording. Needs Tesseract on the machine that runs it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from invoice_desk import config, reader  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Record OCR output for the bundled scanned samples into fixtures/ocr/. Needs Tesseract. "
                    "The photographs of the robustness set are recorded by scripts.evaluate_robustness --record-ocr.")
    parser.parse_args(argv)                      # no options; this only makes --help safe to type
    engine = reader.get_engine(refresh=True)
    if not engine.available:
        print("Tesseract is not installed, so nothing can be recorded.")
        return 1
    manifest = json.loads((config.SAMPLES_DIR / "manifest.json").read_text(encoding="utf-8"))
    count = 0
    for sample in manifest:
        if not sample.get("scanned"):
            continue
        content = (config.SAMPLES_DIR / sample["file"]).read_bytes()
        for variant, name in reader.OCR_VARIANTS.items():           # both reads the pipeline makes of a scan
            doc = reader.read_pdf(content, record_ocr=True, ocr_mode="live", variant=variant)
            for page in doc.pages:
                if page.kind == "scan":
                    count += 1
                    print(f"recorded {sample['file']} page {page.number}, {name} read: {len(page.lines)} lines, "
                          f"confidence {page.ocr['mean_conf']}%, straightened {page.ocr['angle']} deg ({engine.label})")
    print(f"{count} page read(s) recorded into {config.FIXTURES_DIR / 'ocr'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
