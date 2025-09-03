from __future__ import annotations
import os, shutil
from PIL import Image, ImageSequence, ImageFile
from ..utils import norm_ext

try:
    from send2trash import send2trash
except Exception:
    send2trash=None

if ImageFile:
    ImageFile.LOAD_TRUNCATED_IMAGES=True

def do_convert(src,dst,fmt,quality,png3,ico_sizes,square_mode):
    try:
        with Image.open(src) as im:
            if fmt=='ico':
                w,h=im.size
                if w!=h and square_mode and square_mode!='keep':
                    if square_mode=='center':
                        side=min(w,h); left=(w-side)//2; top=(h-side)//2
                        im=im.crop((left,top,left+side,top+side))
                    elif square_mode=='topleft':
                        side=min(w,h); im=im.crop((0,0,side,side))
                    elif square_mode=='fit':
                        side=max(w,h)
                        canvas=Image.new('RGBA',(side,side),(0,0,0,0))
                        canvas.paste(im,((side-w)//2,(side-h)//2))
                        im=canvas
            if fmt=='gif':
                im.save(dst,save_all=True)
            elif fmt=='ico':
                im.save(dst, sizes=[(s,s) for s in (ico_sizes or [256])])
            else:
                params={}
                if fmt=='jpg':
                    params['quality']=quality or 100
                    if im.mode in ('RGBA','LA'):
                        bg=Image.new('RGB',im.size,(255,255,255))
                        bg.paste(im,mask=im.split()[-1]); im=bg
                    else:
                        im=im.convert('RGB')
                elif fmt=='png' and png3:
                    im=im.convert('P',palette=Image.ADAPTIVE,colors=256)
                elif fmt=='webp':
                    params['quality']=quality or 80
                im.save(dst, fmt.upper(), **params)
        return True,'OK'
    except Exception as e:
        return False,str(e)

def batch_convert(files, out_dir, fmt, process_same, quality, png3, ico_sizes, square_mode, preview, remove_src, q_log):
    res=[]
    for f in files:
        src_ext=norm_ext(f)
        tgt_fmt=fmt if True else src_ext
        need = (src_ext!=fmt or process_same)
        if not need:
            res.append(f); continue
        base=os.path.splitext(os.path.basename(f))[0]
        dst=os.path.join(out_dir, f"{base}.{tgt_fmt}")
        if preview:
            dst=os.path.join(out_dir,dst.split(os.sep)[-1])
        ok,msg=do_convert(f,dst,tgt_fmt,quality if tgt_fmt in ('jpg','png','webp') else None,png3 if tgt_fmt=='png' else False,ico_sizes if tgt_fmt=='ico' else None,square_mode if tgt_fmt=='ico' else None)
        q_log(f'LOG\tCONVERT\t{f}\t{dst}\t{"转换" if ok else "转换失败"}')
        if ok and remove_src and not preview:
            try:
                if send2trash: send2trash(f)
                else: os.remove(f)
            except Exception:
                pass
        res.append(dst if ok else f)
    return res
