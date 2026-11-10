import pytest

from scripts.configure_wind import resolve_sdk


def make_sdk(path):
    path.mkdir(parents=True)
    (path / 'WindPy.py').write_text('# SDK placeholder; never imported\n')
    return path.resolve()


def test_explicit_path_with_spaces_overrides_saved_path(tmp_path):
    sdk = make_sdk(tmp_path / '官方 SDK')
    pth = tmp_path / 'WindPy.pth'
    pth.write_text('/stale/path\n')
    assert resolve_sdk(str(sdk), pth, 'Darwin', tmp_path) == sdk


def test_invalid_explicit_path_fails_instead_of_silent_fallback(tmp_path):
    sdk = make_sdk(tmp_path / 'valid')
    pth = tmp_path / 'WindPy.pth'
    pth.write_text(str(sdk))
    with pytest.raises(ValueError, match='configured directory'):
        resolve_sdk(str(tmp_path / 'missing'), pth, 'Windows', tmp_path)


def test_existing_relative_path_preserved_without_executing_pth(tmp_path):
    sdk = make_sdk(tmp_path / 'sdk')
    pth = tmp_path / 'WindPy.pth'
    pth.write_text('\ufeff# comment\nimport should_never_run\nsdk\n', encoding='utf-8')
    assert resolve_sdk(None, pth, 'Darwin', tmp_path) == sdk


def test_windows_default_and_stale_path_recovery(tmp_path):
    sdk = make_sdk(tmp_path / 'Wind' / 'Wind.NET.Client' / 'WindNET' / 'x64')
    pth = tmp_path / 'WindPy.pth'
    pth.write_text('/missing/old/sdk\n')
    assert resolve_sdk(None, pth, 'Windows', tmp_path) == sdk


def test_missing_sdk_is_actionable(tmp_path):
    with pytest.raises(ValueError, match='WIND_PYTHON_DIR'):
        resolve_sdk(None, tmp_path / 'WindPy.pth', 'Unsupported', tmp_path)
