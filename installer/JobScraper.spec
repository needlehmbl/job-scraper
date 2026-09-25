# -*- mode: python -*-
block_cipher = None
a = Analysis(
    ["../launcher.py"],
    pathex=[".."],
    binaries=[],
    datas=[("../dashboard/dist", "dashboard/dist"), ("../config.yaml", "."),
           ("../schema.sql", "."), ("../setup_wizard.py", ".")],
    hiddenimports=["uvicorn", "fastapi", "playwright"],
    excludes=[],
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="JobScraper",
          console=False)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="JobScraper")
