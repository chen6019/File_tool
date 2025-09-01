# -*- coding: utf-8 -*-
"""图片工具
单页多功能: 对单个目录中的图片执行 去重 / 转换格式 / 重命名 (可组合)。

操作顺序: 去重(可选) -> 转换/重命名(可选)

占位符: {name} 原文件名(无扩展)  {ext} 原扩展(规范化)  {index} 序号  {fmt} 目标格式(若无转换则为原格式)

去重策略:
  - 阈值=0 精确重复 (aHash+dHash 完全相同)
  - 阈值>0 允许近似 (aHash汉明 + dHash汉明 之和 <= 阈值)
保留策略: first / largest / largest-file / newest / oldest

日志阶段列: HASH / DEDUP / CONVERT / RENAME

"""
from __future__ import annotations
import os, sys, threading, queue, shutil, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional, Tuple, Iterable

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except Exception:
    tk = None  # type: ignore

try:
    from PIL import Image, ImageSequence, ImageFile, ImageTk
    ImageFile.LOAD_TRUNCATED_IMAGES = True
except Exception:
    Image = None  # type: ignore

SUPPORTED_EXT = {'.jpg','.jpeg','.png','.webp','.gif','.bmp','.tif','.tiff','.ico'}

# ---------- 工具函数 ----------

def iter_images(root: str, recursive: bool) -> Iterable[str]:
    for base, dirs, files in os.walk(root):
        for f in files:
            if f.lower().endswith(tuple(SUPPORTED_EXT)):
                yield os.path.join(base,f)
        if not recursive:
            break

def norm_ext(path: str) -> str:
    ext = os.path.splitext(path)[1].lower().lstrip('.')
    return 'jpg' if ext in ('jpg','jpeg') else ext

def next_non_conflict(path: str) -> str:
    if not os.path.exists(path): return path
    b,e = os.path.splitext(path); i=1
    while True:
        cand=f"{b}_new{i}{e}"; i+=1
        if not os.path.exists(cand): return cand

def map_png_quality(q: int) -> int:
    if q>=80: return 2
    if q>=40: return 4
    return 6

# ---------- 画像转换 ----------

def convert_one(src: str, dst: str, fmt: str, quality: Optional[int], png3: bool, ico_sizes: Optional[List[int]]) -> Tuple[bool,str]:
    try:
        with Image.open(src) as im:  # type: ignore
            orig_fmt=(im.format or '').upper()
            animated=getattr(im,'is_animated',False)
            f=fmt.lower(); save_fmt='JPEG' if f=='jpg' else f.upper(); params={}
            if f=='jpg':
                if im.mode in ('RGBA','LA'): im=im.convert('RGB')
                q=quality if quality is not None else 85
                params['quality']=max(1,min(int(q),100)); params['optimize']=True
                if params['quality']>=92: params['subsampling']=0
            elif f=='webp':
                q=quality if quality is not None else 80
                params['quality']=max(1,min(int(q),100))
            elif f=='png':
                q=quality if quality is not None else 100
                comp=map_png_quality(q)
                if png3: comp=9
                params['optimize']=True; params['compress_level']=comp
            elif f=='ico':
                if im.mode not in ('RGBA','RGB'): im=im.convert('RGBA')
                if not ico_sizes: ico_sizes=[256,128,64,48,32,16]
                params['sizes']=[(s,s) for s in ico_sizes]
            if orig_fmt=='GIF' and animated and f in ('webp','png','jpg'):
                frames=[fr.convert('RGBA') for fr in ImageSequence.Iterator(im)]
                durs=[fr.info.get('duration',100) for fr in ImageSequence.Iterator(im)]
                if f=='webp':
                    frames[0].save(dst, format='WEBP', save_all=True, append_images=frames[1:], loop=0, duration=durs, quality=params.get('quality',80))
                    return True,'WebP动画'
                if f=='png':
                    frames[0].save(dst, format='PNG', **params); return True,'首帧'
                if f=='jpg':
                    frames[0].convert('RGB').save(dst, format='JPEG', **params); return True,'首帧'
            im.save(dst, format=save_fmt, **params)
            return True,'成功'
    except Exception as e:
        return False, str(e)

# ---------- 去重哈希 ----------

def ahash(im: Image.Image, size: int=8) -> int:  # type: ignore
    g = im.convert('L').resize((size,size), Image.LANCZOS)
    px=list(g.getdata()); avg=sum(px)/len(px); bits=0
    for p in px: bits=(bits<<1)|(1 if p>=avg else 0)
    return bits

def dhash(im: Image.Image, size: int=8) -> int:  # type: ignore
    g = im.convert('L').resize((size+1,size), Image.LANCZOS); px=list(g.getdata()); bits=0
    for r in range(size):
        row=px[r*(size+1):(r+1)*(size+1)]
        for i in range(size): bits=(bits<<1)|(1 if row[i]>row[i+1] else 0)
    return bits

def hamming(a: int,b: int) -> int:
    return (a^b).bit_count()

@dataclass
class ImgInfo:
    path: str
    size: int
    w: int
    h: int
    ah: int
    dh: int
    mtime: float
    @property
    def res(self): return self.w*self.h

# ---------- GUI 核心 ----------
class ImageToolApp:
    def __init__(self, root: tk.Tk):  # type: ignore
            self.root = root
            root.title('图片工具')
            root.geometry('1500x840')
            root.minsize(1180,720)
            self.q = queue.Queue()
            self.stop_flag = threading.Event()
            self.worker = None
            # 引用占位
            self.frame_convert = None
            self.frame_rename = None
            self.move_dir_entry = None
            self.move_dir_btn = None
            self._build()
            self.root.after(150, self._drain)

    # UI
    def _build(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill='both', expand=True)
        for i in range(10):
            top.columnconfigure(i, weight=1)
        r = 0
        ttk.Label(top, text='输入目录:').grid(row=r, column=0, sticky='e')
        self.in_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.in_var, width=60).grid(row=r, column=1, columnspan=4, sticky='we', padx=4)
        ttk.Button(top, text='选择', command=self._pick_in).grid(row=r, column=5)
        rec_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text='递归', variable=rec_var).grid(row=r, column=6, sticky='w')
        self.recursive_var = rec_var
        ttk.Label(top, text='输出目录:').grid(row=r, column=7, sticky='e')
        self.out_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.out_var, width=60).grid(row=r, column=8, sticky='we', padx=4)
        ttk.Button(top, text='选择', command=self._pick_out).grid(row=r, column=9)
        r += 1
        # 功能启用
        self.enable_dedupe = tk.BooleanVar(value=True)
        self.enable_convert = tk.BooleanVar(value=True)
        self.enable_rename = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text='去重', variable=self.enable_dedupe).grid(row=r, column=0, sticky='w')
        ttk.Checkbutton(top, text='转换格式', variable=self.enable_convert).grid(row=r, column=1, sticky='w')
        ttk.Checkbutton(top, text='重命名', variable=self.enable_rename).grid(row=r, column=2, sticky='w')
        ttk.Label(top, text='线程').grid(row=r, column=3, sticky='e')
        self.workers_var = tk.IntVar(value=max(2, (os.cpu_count() or 4)//2))
        ttk.Spinbox(top, from_=1, to=64, textvariable=self.workers_var, width=6).grid(row=r, column=4, sticky='w')
        ttk.Button(top, text='开始', command=self._start).grid(row=r, column=8, sticky='e')
        ttk.Button(top, text='取消', command=self._cancel).grid(row=r, column=9, sticky='w')
        r += 1
        # 去重区
        dedupe = ttk.LabelFrame(top, text='去重设置')
        dedupe.grid(row=r, column=0, columnspan=3, sticky='we', padx=4, pady=4)
        dedupe.columnconfigure(5, weight=1)
        self.threshold_var = tk.IntVar(value=0)
        ttk.Label(dedupe, text='阈值').grid(row=0, column=0, sticky='e')
        ttk.Spinbox(dedupe, from_=0, to=32, textvariable=self.threshold_var, width=6).grid(row=0, column=1, sticky='w')
        self.keep_var = tk.StringVar(value='largest')
        ttk.Label(dedupe, text='保留策略').grid(row=0, column=2, sticky='e')
        ttk.Combobox(dedupe, textvariable=self.keep_var, values=['first','largest','largest-file','newest','oldest'], width=12, state='readonly').grid(row=0, column=3, sticky='w')
        self.dedup_action_var = tk.StringVar(value='list')
        ttk.Label(dedupe, text='动作').grid(row=0, column=4, sticky='e')
        ttk.Combobox(dedupe, textvariable=self.dedup_action_var, values=['list','delete','move'], width=10, state='readonly').grid(row=0, column=5, sticky='w')
        self.move_dir_var = tk.StringVar()
        ttk.Label(dedupe, text='移动到').grid(row=0, column=6, sticky='e')
        self.move_dir_entry = ttk.Entry(dedupe, textvariable=self.move_dir_var, width=24)
        self.move_dir_entry.grid(row=0, column=7, sticky='w')
        self.move_dir_btn = ttk.Button(dedupe, text='选', command=self._pick_move_dir)
        self.move_dir_btn.grid(row=0, column=8)
        # 转换区
        convert = ttk.LabelFrame(top, text='格式转换')
        convert.grid(row=r, column=3, columnspan=3, sticky='we', padx=4, pady=4)
        self.frame_convert = convert
        self.fmt_var = tk.StringVar(value='png')
        ttk.Label(convert, text='格式').grid(row=0, column=0, sticky='e')
        ttk.Combobox(convert, textvariable=self.fmt_var, values=['jpg','png','webp','ico'], width=10, state='readonly').grid(row=0, column=1, sticky='w')
        self.quality_var = tk.IntVar(value=85)
        ttk.Label(convert, text='质量').grid(row=0, column=2, sticky='e')
        ttk.Scale(convert, from_=1, to=100, orient='horizontal', variable=self.quality_var, length=150).grid(row=0, column=3, sticky='we')
        self.process_same_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(convert, text='同格式也重存', variable=self.process_same_var).grid(row=0, column=4, sticky='w')
        self.png3_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(convert, text='PNG3压缩', variable=self.png3_var).grid(row=0, column=5, sticky='w')
        # 重命名区
        rename = ttk.LabelFrame(top, text='重命名')
        rename.grid(row=r, column=6, columnspan=4, sticky='we', padx=4, pady=4)
        self.frame_rename = rename
        self.pattern_var = tk.StringVar(value='{name}_{index}.{fmt}')
        ttk.Label(rename, text='模式').grid(row=0, column=0, sticky='e')
        ttk.Entry(rename, textvariable=self.pattern_var, width=34).grid(row=0, column=1, sticky='w', padx=2)
        self.start_var = tk.IntVar(value=1)
        self.step_var = tk.IntVar(value=1)
        ttk.Label(rename, text='起始/步长').grid(row=0, column=2, sticky='e')
        ttk.Spinbox(rename, from_=1, to=999999, textvariable=self.start_var, width=7).grid(row=0, column=3, sticky='w')
        ttk.Spinbox(rename, from_=1, to=9999, textvariable=self.step_var, width=5).grid(row=0, column=4, sticky='w')
        self.overwrite_var = tk.StringVar(value='overwrite')
        ttk.Label(rename, text='覆盖').grid(row=0, column=5, sticky='e')
        ttk.Combobox(rename, textvariable=self.overwrite_var, values=['overwrite','skip','rename'], width=9, state='readonly').grid(row=0, column=6, sticky='w')
        r += 1
        self.progress = ttk.Progressbar(top, maximum=100)
        self.progress.grid(row=r, column=0, columnspan=10, sticky='we', pady=6)
        r += 1
        self.status_var = tk.StringVar(value='就绪')
        ttk.Label(top, textvariable=self.status_var, foreground='blue').grid(row=r, column=0, columnspan=10, sticky='w')
        r += 1
        # 日志 + 预览
        container = ttk.Frame(top)
        container.grid(row=r, column=0, columnspan=10, sticky='nsew')
        top.rowconfigure(r, weight=1)
        container.columnconfigure(0, weight=3)
        container.columnconfigure(1, weight=1)
        self.log = ttk.Treeview(container, columns=('stage','src','dst','info'), show='headings')
        for col, txt, w in [('stage','阶段',80),('src','源',360),('dst','目标/组',360),('info','信息',240)]:
            self.log.heading(col, text=txt)
            self.log.column(col, width=w, anchor='w', stretch=True)
        self.log.grid(row=0, column=0, sticky='nsew')
        vsb = ttk.Scrollbar(container, orient='vertical', command=self.log.yview)
        hsb = ttk.Scrollbar(container, orient='horizontal', command=self.log.xview)
        self.log.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.grid(row=0, column=0, sticky='nse')
        hsb.grid(row=1, column=0, sticky='we')
        prev = ttk.LabelFrame(container, text='预览')
        prev.grid(row=0, column=1, sticky='nsew', padx=(8,0))
        prev.columnconfigure(0, weight=1)
        prev.rowconfigure(0, weight=1)
        self.preview_label = ttk.Label(prev, text='(选择日志行)')
        self.preview_label.grid(row=0, column=0, sticky='nsew', padx=4, pady=4)
        self.preview_info = tk.StringVar(value='')
        ttk.Label(prev, textvariable=self.preview_info, foreground='gray').grid(row=1, column=0, sticky='we')
        self.log.bind('<<TreeviewSelect>>', self._on_select_row)
        self.log.bind('<Motion>', self._on_log_motion)
        # 联动与提示
        self.enable_convert.trace_add('write', lambda *a: self._update_states())
        self.enable_rename.trace_add('write', lambda *a: self._update_states())
        self.dedup_action_var.trace_add('write', lambda *a: self._update_states())
        self._tooltip = None
        self._tooltip_after = None
        self._update_states()

    # ------------ 事件 ------------
    def _pick_in(self):
        d=filedialog.askdirectory();
        if d: self.in_var.set(d)
    def _pick_out(self):
        d=filedialog.askdirectory();
        if d: self.out_var.set(d)
    def _pick_move_dir(self):
        d=filedialog.askdirectory();
        if d: self.move_dir_var.set(d)

    def _start(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo('提示','任务运行中'); return
        root_dir=self.in_var.get().strip(); out_dir=self.out_var.get().strip()
        if not root_dir or not os.path.isdir(root_dir): self.status_var.set('输入目录无效'); return
        if not out_dir: out_dir=root_dir
        os.makedirs(out_dir, exist_ok=True)
        self._all_files=[p for p in iter_images(root_dir, self.recursive_var.get())]
        if not self._all_files: self.status_var.set('无图片'); return
        for i in self.log.get_children(): self.log.delete(i)
        self.progress['value']=0; self.progress['maximum']=len(self._all_files)
        self.status_var.set('开始...')
        self.stop_flag.clear()
        self.worker=threading.Thread(target=self._pipeline, daemon=True); self.worker.start()

    def _cancel(self):
        self.stop_flag.set(); self.status_var.set('请求取消...')

    # ------------ 管线 ------------
    def _pipeline(self):
        try:
            kept_files=self._all_files
            # 去重
            if self.enable_dedupe.get():
                kept_files=self._dedupe_stage(kept_files)
                if self.stop_flag.is_set(): return
            # 转换/重命名
            if self.enable_convert.get() or self.enable_rename.get():
                self._convert_rename_stage(kept_files)
            self.q.put('STATUS 完成')
        except Exception as e:
            self.q.put(f'STATUS 失败: {e}')

    # 去重阶段
    def _dedupe_stage(self, files: List[str]) -> List[str]:
        th=self.threshold_var.get(); keep_mode=self.keep_var.get(); action=self.dedup_action_var.get(); move_dir=self.move_dir_var.get().strip()
        workers=max(1,self.workers_var.get())
        self.q.put(f'STATUS 去重计算哈希 共{len(files)}')
        infos=[]; lock=threading.Lock(); done=0
        def compute(path):
            nonlocal done
            if self.stop_flag.is_set(): return None
            try:
                with Image.open(path) as im:  # type: ignore
                    w,h=im.size; ah=ahash(im); dh=dhash(im); st=os.stat(path)
                info=ImgInfo(path, st.st_size, w,h,ah,dh,st.st_mtime)
            except Exception:
                info=None
            with lock:
                done+=1; self.q.put(f'HASH {done} {len(files)}')
            return info
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for fut in as_completed([ex.submit(compute,f) for f in files]):
                res=fut.result();
                if res: infos.append(res)
        if self.stop_flag.is_set(): return []
        # 分组
        groups=[]
        for info in infos:
            placed=False
            for g in groups:
                rep=g[0]
                if th==0:
                    if info.ah==rep.ah and info.dh==rep.dh: g.append(info); placed=True; break
                else:
                    if hamming(info.ah,rep.ah)+hamming(info.dh,rep.dh) <= th: g.append(info); placed=True; break
            if not placed: groups.append([info])
        dup_groups=[g for g in groups if len(g)>1]
        kept=[]
        for gi,g in enumerate(sorted(dup_groups,key=lambda x:-len(x)),1):
            if keep_mode=='largest': keep=max(g,key=lambda x:x.res)
            elif keep_mode=='largest-file': keep=max(g,key=lambda x:x.size)
            elif keep_mode=='newest': keep=max(g,key=lambda x:x.mtime)
            elif keep_mode=='oldest': keep=min(g,key=lambda x:x.mtime)
            else: keep=g[0]
            kept.append(keep.path)
            others=[x for x in g if x is not keep]
            for o in others:
                act='保留'
                if action=='delete' and not self.stop_flag.is_set():
                    try: os.remove(o.path); act='删除'
                    except Exception as e: act=f'删失败:{e}'
                elif action=='move' and move_dir and not self.stop_flag.is_set():
                    try:
                        os.makedirs(move_dir, exist_ok=True)
                        target=os.path.join(move_dir, os.path.basename(o.path))
                        if os.path.exists(target): target=next_non_conflict(target)
                        shutil.move(o.path, target); act='移动'
                    except Exception as e: act=f'移失败:{e}'
                self.q.put(f'LOG\tDEDUP\t{o.path}\t{keep.path}\t{act}')
            self.q.put(f'LOG\tDEDUP\t{keep.path}\t组#{gi}\t保留({len(g)})')
        # 未参与重复的直接保留
        all_dup_paths={x.path for grp in dup_groups for x in grp}
        for p in files:
            if p not in all_dup_paths: kept.append(p)
        return kept

    def _convert_rename_stage(self, files: List[str]):
        fmt=self.fmt_var.get(); process_same=self.process_same_var.get(); quality=self.quality_var.get(); png3=self.png3_var.get()
        pattern=self.pattern_var.get(); start=self.start_var.get(); step=self.step_var.get(); overwrite=self.overwrite_var.get()
        out_dir=self.out_var.get().strip() or self.in_var.get().strip()
        workers=max(1,self.workers_var.get())
        tasks=[]; idx=start
        for f in files:
            src_ext=norm_ext(f)
            target_fmt = fmt if self.enable_convert.get() else src_ext
            need_convert = self.enable_convert.get() and (src_ext!=fmt or process_same)
            name_pat = pattern
            name_pat = name_pat.replace('{name}', os.path.splitext(os.path.basename(f))[0])\
                                 .replace('{ext}', src_ext)\
                                 .replace('{fmt}', target_fmt)
            if '{index}' in name_pat:
                name_pat = name_pat.replace('{index}', str(idx))
            if '.' not in os.path.basename(name_pat):
                name_pat += f'.{target_fmt}'
            out_path=os.path.join(out_dir, name_pat)
            if os.path.exists(out_path):
                if overwrite=='skip':
                    self.q.put(f'LOG\tCONVERT\t{f}\t{out_path}\t跳过(存在)')
                    idx+=step; continue
                elif overwrite=='rename':
                    out_path=next_non_conflict(out_path)
            tasks.append((f,out_path,need_convert,target_fmt))
            idx+=step
        total=len(tasks); self.q.put(f'STATUS 转换/重命名 共{total}')
        done=0; lock=threading.Lock()
        def job(spec):
            nonlocal done
            src,dst,need_convert,target_fmt=spec
            if self.stop_flag.is_set(): return
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            ok=True; msg=''
            if need_convert:
                ok,msg=convert_one(src,dst,target_fmt,quality if target_fmt in ('jpg','png','webp') else None,png3 if target_fmt=='png' else False, None if target_fmt!='ico' else [16,32,48,64,128,256])
            else:
                # 仅重命名/复制
                try:
                    if os.path.abspath(src)==os.path.abspath(dst):
                        msg='保持'
                    else:
                        shutil.copy2(src,dst)
                        msg='复制'
                except Exception as e:
                    ok=False; msg=f'复制失败:{e}'
            with lock:
                done+=1
                stage='CONVERT' if need_convert else 'RENAME'
                self.q.put(f'LOG\t{stage}\t{src}\t{dst}\t{msg if ok else "失败:"+msg}')
                self.q.put(f'PROG {done} {total}')
        if workers>1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs=[ex.submit(job,t) for t in tasks]
                for _ in as_completed(futs):
                    if self.stop_flag.is_set(): break
        else:
            for t in tasks: job(t)

    # ------------ 队列/预览 ------------
    def _drain(self):
        try:
            while True:
                m=self.q.get_nowait()
                if m.startswith('HASH '):
                    _,d,total=m.split(); d=int(d);total=int(total); self.progress['maximum']=total; self.progress['value']=d; pct=int(d/total*100) if total else 0; self.status_var.set(f'去重哈希 {pct}% ({d}/{total})')
                elif m.startswith('PROG '):
                    _,d,total=m.split(); d=int(d); total=int(total); self.progress['maximum']=total; self.progress['value']=d; pct=int(d/total*100) if total else 0; self.status_var.set(f'处理 {pct}% ({d}/{total})')
                elif m.startswith('STATUS '):
                    self.status_var.set(m[7:])
                elif m.startswith('LOG\t'):
                    try:
                        _tag,stage,src,dst,info=m.split('\t',4)
                        self.log.insert('', 'end', values=(stage, os.path.basename(src), os.path.basename(dst), info), tags=(src,))
                    except Exception:
                        pass
        except queue.Empty:
            pass
        finally:
            self.root.after(150,self._drain)

    def _on_select_row(self, event=None):
        sel=self.log.selection();
        if not sel: return
        iid=sel[0]; tags=self.log.item(iid,'tags')
        if not tags: return
        path=tags[0]
        if not os.path.exists(path):
            self.preview_label.configure(text='文件不存在', image='')
            return
        try:
            with Image.open(path) as im:  # type: ignore
                w,h=im.size; max_side=420; scale=min(max_side/w,max_side/h,1)
                if scale<1: im=im.resize((int(w*scale),int(h*scale)))
                photo=ImageTk.PhotoImage(im)
            self.preview_label.configure(image=photo,text=''); self._preview_ref=photo; self.preview_info.set(f'{w}x{h} {os.path.basename(path)}')
        except Exception as e:
            self.preview_label.configure(text=f'预览失败:{e}', image='')
    # -------- 状态联动 --------
    def _update_states(self):
        if self.frame_convert:
            conv_state = 'normal' if self.enable_convert.get() else 'disabled'
            for ch in self.frame_convert.winfo_children():
                try: ch.configure(state=conv_state)
                except Exception: pass
        if self.frame_rename:
            ren_state = 'normal' if self.enable_rename.get() else 'disabled'
            for ch in self.frame_rename.winfo_children():
                try: ch.configure(state=ren_state)
                except Exception: pass
        need_move = self.dedup_action_var.get() == 'move'
        state_move = 'normal' if need_move else 'disabled'
        if self.move_dir_entry: 
            try: self.move_dir_entry.configure(state=state_move)
            except Exception: pass
        if self.move_dir_btn:
            try: self.move_dir_btn.configure(state=state_move)
            except Exception: pass
    # -------- 日志 Tooltip --------
    def _show_tooltip(self, text, x, y):
        self._hide_tooltip()
        tw=tk.Toplevel(self.root)
        tw.wm_overrideredirect(True)
        tw.attributes('-topmost',True)
        lab=tk.Label(tw,text=text,background='#FFFFE0',relief='solid',borderwidth=1,justify='left')
        lab.pack(ipadx=4,ipady=2)
        tw.wm_geometry(f"+{x+15}+{y+15}")
        self._tooltip=tw
    def _hide_tooltip(self):
        if self._tooltip:
            try: self._tooltip.destroy()
            except Exception: pass
        self._tooltip=None
    def _on_log_motion(self, event):
        if self._tooltip_after:
            self.root.after_cancel(self._tooltip_after)
            self._tooltip_after=None
        iid=self.log.identify_row(event.y)
        col=self.log.identify_column(event.x)
        if not iid or col not in ('#2','#3'):
            self._hide_tooltip(); return
        tags=self.log.item(iid,'tags')
        if not tags: return
        full_path=tags[0]
        # 延迟显示避免抖动
        self._tooltip_after=self.root.after(500, lambda p=full_path,x=self.root.winfo_pointerx(),y=self.root.winfo_pointery(): self._show_tooltip(p,x,y))
        # 移动快速隐藏
        self.root.bind('<Leave>', lambda e: self._hide_tooltip(), add='+')

# ---------- 启动 ----------
def launch():
    if tk is None or Image is None:
        print('缺少 Tkinter 或 Pillow'); return 2
    root=tk.Tk(); ImageToolApp(root); root.mainloop(); return 0

if __name__=='__main__':
    launch()
