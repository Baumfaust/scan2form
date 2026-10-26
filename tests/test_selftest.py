import numpy as np
import pytest

from scan2form import selftest
from scan2form.core.ocr import Ocr, bundled_models_dir


def test_explicit_model_folder_is_used_instead_of_downloading(tmp_path):
    # An empty folder: RapidOCR must look there (and fail), not fetch anything.
    # It loads the models lazily, on the first image.
    engine = Ocr(models_dir=tmp_path)._get_engine()
    with pytest.raises(FileNotFoundError):
        engine(np.full((50, 200, 3), 255, np.uint8))
    assert not any(tmp_path.iterdir())


def test_development_install_has_no_bundled_models():
    assert bundled_models_dir() is None  # only a PyInstaller build sets sys._MEIPASS


def test_self_test_report(tmp_path):
    report = tmp_path / "report.txt"
    selftest.run(report)
    text = report.read_text(encoding="utf-8")
    for check in ("Qt", "PDF", "vault crypto", "OpenCV", "OCR (bundled models)"):
        assert f"OK    {check}" in text
    # The test session uses an in-memory keyring, so only that check may differ here.
    assert text.splitlines()[-1] in ("PASSED", "FAILED")
