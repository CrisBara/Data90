# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

datas = []
hiddenimports = [
    'logging.config',
    'psycopg',
    'psycopg_binary',
    'psycopg_c',
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebChannel',
]
datas += collect_data_files('data90')
ui_root = Path('src/data90/ui')
datas += [
    (str(path), str(Path('data90/ui') / path.parent.relative_to(ui_root)))
    for path in ui_root.rglob('*')
    if path.is_file() and '__pycache__' not in path.parts
]
hiddenimports += collect_submodules('alembic')


a = Analysis(
    ['scripts/gui_entry.py'],
    pathex=['src'],
# Use the Windows ICU DLLs: the Poppler ICU build has suffixed exports incompatible with Qt.
binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
a.binaries = [
    entry
    for entry in a.binaries
    if Path(entry[0]).name.casefold() not in {"icuuc.dll", "icudt78.dll"}
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Data90',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Data90',
)
