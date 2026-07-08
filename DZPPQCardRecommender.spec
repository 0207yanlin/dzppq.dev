# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_dynamic_libs
from PyInstaller.utils.hooks import collect_submodules

datas = [('D:\\dzppq.dev\\data\\match_latest.db', 'data'), ('D:\\dzppq.dev\\data\\latest_meta_analysis.json', 'data'), ('D:\\miniconda3\\Lib\\site-packages\\rapidocr_onnxruntime\\config.yaml', 'rapidocr_onnxruntime'), ('D:\\miniconda3\\Lib\\site-packages\\rapidocr_onnxruntime\\models\\ch_PP-OCRv4_det_infer.onnx', 'rapidocr_onnxruntime/models'), ('D:\\miniconda3\\Lib\\site-packages\\rapidocr_onnxruntime\\models\\ch_PP-OCRv4_rec_infer.onnx', 'rapidocr_onnxruntime/models'), ('D:\\miniconda3\\Lib\\site-packages\\rapidocr_onnxruntime\\models\\ch_ppocr_mobile_v2.0_cls_infer.onnx', 'rapidocr_onnxruntime/models')]
binaries = []
hiddenimports = ['rapidocr_onnxruntime', 'onnxruntime', 'onnxruntime.capi.onnxruntime_pybind11_state', 'PIL', 'PIL.Image']
datas += collect_data_files('rapidocr_onnxruntime')
binaries += collect_dynamic_libs('onnxruntime')
hiddenimports += collect_submodules('PIL')


a = Analysis(
    ['D:\\dzppq.dev\\scripts\\card_pick_recommender.py'],
    pathex=['D:\\dzppq.dev', 'D:\\dzppq.dev\\src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'torchvision', 'tensorflow', 'scipy', 'matplotlib', 'pandas', 'IPython', 'jupyter', 'notebook', 'pytest', 'easyocr', 'skimage', 'onnxruntime.tools', 'onnxruntime.transformers', 'onnxruntime.datasets', 'onnxruntime.quantization'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DZPPQCardRecommender',
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
    name='DZPPQCardRecommender',
)
