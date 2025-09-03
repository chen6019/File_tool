import os, re, shutil
from typing import Iterable
from PIL import Image, ImageFile
try:
    from send2trash import send2trash
except Exception:
    send2trash=None

SUPPORTED_EXT={'.jpg','.jpeg','.png','.webp','.gif','.bmp','.tiff','.ico'}
if ImageFile:
    ImageFile.LOAD_TRUNCATED_IMAGES=True

def iter_images(root:str, recursive:bool)->Iterable[str]:
    for dirpath, dirs, files in os.walk(root):
        for f in files:
            if os.path.splitext(f)[1].lower() in SUPPORTED_EXT:
                yield os.path.join(dirpath,f)
        if not recursive:
            break

def norm_ext(path:str)->str:
    e=os.path.splitext(path)[1].lower().lstrip('.')
    return 'jpg' if e=='jpeg' else e

def next_non_conflict(path:str)->str:
    base,ext=os.path.splitext(path); i=1
    while os.path.exists(path):
        path=f"{base}_{i}{ext}"; i+=1
    return path

def safe_delete(path:str):
    if send2trash is not None:
        try:
            send2trash(path); return True,'回收站'
        except Exception:
            pass
    try:
        os.remove(path); return True,'删除'
    except Exception as e:
        return False,f'删失败:{e}'
