# -*- coding: utf-8 -*-
"""统一工具窗口

将图片格式转换 / 文本编码批量转换 / 重复图片清理 合并到一个 GUI 窗口。
结构:
  - ToolBase: 公共线程+队列基类
  - ImageConvertModule
  - TextEncodingModule
  - DuplicateImageModule

说明:
  * 为避免一次性大规模改动原文件, 这里复制/精简核心逻辑, 不直接依赖原 GUI 类, 保留原脚本独立可运行.
  * 后续可逐步抽取公共组件(进度管理/日志/线程池封装)到单独模块.
  * 采用 Notebook 作为顶层导航.

TODO (可选后续):
  - 颜色主题 / 日志过滤
  - 任务取消更细粒度中断
  - 统一的设置持久化 (JSON)
  - 复用原文件中更完整的参数选项(当前做了主干功能)
"""
from __future__ import annotations
import os, sys, threading, queue, time, shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional, Iterable, Tuple
try:
	import tkinter as tk
	from tkinter import ttk, filedialog, messagebox
except Exception:
	tk = None  # type: ignore

# ---------------- 公共基类 ----------------
class ToolBase:
	def __init__(self):
		self.q: queue.Queue[str] = queue.Queue()
		self.stop_flag = threading.Event()
		self.worker: Optional[threading.Thread] = None

	def start_in_thread(self, target):
		if self.worker and self.worker.is_alive():
			return False
		self.stop_flag.clear()
		self.worker = threading.Thread(target=target, daemon=True)
		self.worker.start()
		return True

# ---------------- 图片转换 (精简版) ----------------
SUPPORTED_IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.webp', '.ico', '.gif', '.bmp')
from PIL import Image, ImageSequence, ImageFile, ImageTk
ImageFile.LOAD_TRUNCATED_IMAGES = True

def iter_image_files(root: str, recursive: bool) -> Iterable[str]:
	if os.path.isfile(root):
		if root.lower().endswith(SUPPORTED_IMAGE_EXT):
			yield root
		return
	for base, dirs, files in os.walk(root):
		for f in files:
			if f.lower().endswith(SUPPORTED_IMAGE_EXT):
				yield os.path.join(base, f)
		if not recursive:
			break

def normalize_ext(p: str) -> str:
	e = os.path.splitext(p)[1].lower().lstrip('.')
	return 'jpg' if e in ('jpg','jpeg') else e

def next_non_conflict(path: str) -> str:
	if not os.path.exists(path):
		return path
	base, ext = os.path.splitext(path)
	i = 1
	while True:
		cand = f"{base}_new{i}{ext}"
		if not os.path.exists(cand):
			return cand
		i += 1

def map_png_quality(q: int) -> int:
	if q >= 80: return 2
	if q >= 40: return 4
	return 6

def convert_one_image(src: str, dst: str, fmt: str, quality: int|None, png3: bool, ico_sizes: Optional[List[int]]) -> Tuple[bool,str]:
	try:
		with Image.open(src) as im:
			orig_fmt = (im.format or '').upper()
			animated = getattr(im, 'is_animated', False)
			fmt_low = fmt.lower()
			save_fmt = 'JPEG' if fmt_low == 'jpg' else fmt_low.upper()
			params = {}
			if fmt_low == 'jpg':
				if im.mode in ('RGBA','LA'): im = im.convert('RGB')
				qv = quality if quality is not None else 85
				params['quality'] = max(1,min(int(qv),100))
				params['optimize'] = True
				if params['quality'] >= 92: params['subsampling'] = 0
			elif fmt_low == 'webp':
				qv = quality if quality is not None else 80
				params['quality'] = max(1,min(int(qv),100))
			elif fmt_low == 'png':
				qv = quality if quality is not None else 100
				comp = map_png_quality(qv)
				if png3:
					comp = 9
					params['optimize'] = True
				else:
					params['optimize'] = True
				params['compress_level'] = comp
			elif fmt_low == 'ico':
				if im.mode not in ('RGBA','RGB'): im = im.convert('RGBA')
				if not ico_sizes: ico_sizes=[256,128,64,48,32,16]
				ico_sizes = sorted({int(s) for s in ico_sizes if 0 < int(s) <= 1024}, reverse=True)
				params['sizes']=[(s,s) for s in ico_sizes]
			# GIF 动画 -> webp/png 首帧(精简)
			if orig_fmt == 'GIF' and animated and fmt_low in ('webp','png','jpg'):
				frames=[fr.convert('RGBA') for fr in ImageSequence.Iterator(im)]
				durs=[fr.info.get('duration',100) for fr in ImageSequence.Iterator(im)]
				if fmt_low=='webp':
					frames[0].save(dst, format='WEBP', save_all=True, append_images=frames[1:], loop=0, duration=durs, quality=params.get('quality',80))
					return True,'WebP动画'
				if fmt_low=='png':
					frames[0].save(dst, format='PNG', **params)
					return True,'首帧'
				if fmt_low=='jpg':
					frames[0].convert('RGB').save(dst, format='JPEG', **params)
					return True,'首帧'
			im.save(dst, format=save_fmt, **params)
			return True,'成功'
	except Exception as e:
		return False,str(e)

class ImageConvertModule(ToolBase):
	def __init__(self, parent: tk.Widget):  # type: ignore
		super().__init__()
		self.parent = parent
		# vars
		self.in_var = tk.StringVar()
		self.out_var = tk.StringVar()
		self.format_var = tk.StringVar(value='png')
		self.recursive_var = tk.BooleanVar(value=False)
		self.pattern_var = tk.StringVar(value='{name}.{fmt}')
		self.quality_var = tk.IntVar(value=85)
		self.process_same_var = tk.BooleanVar(value=False)
		self.png3_var = tk.BooleanVar(value=False)
		self.start_var = tk.IntVar(value=1)
		self.step_var = tk.IntVar(value=1)
		self.overwrite_var = tk.StringVar(value='overwrite')
		self.workers_var = tk.IntVar(value=max(2, (os.cpu_count() or 4)//2))
		self._build()

	def _build(self):
		g = ttk.Frame(self.parent)
		g.pack(fill='both', expand=True)
		for i in range(8): g.columnconfigure(i, weight=1)
		r=0
		ttk.Label(g,text='输入:').grid(row=r,column=0,sticky='e')
		ttk.Entry(g,textvariable=self.in_var,width=42).grid(row=r,column=1,columnspan=4,sticky='we',padx=4)
		ttk.Button(g,text='选择',command=self._pick_in).grid(row=r,column=5)
		ttk.Checkbutton(g,text='递归',variable=self.recursive_var).grid(row=r,column=6,sticky='w')
		ttk.Button(g,text='清空日志',command=lambda:self.log.delete(*self.log.get_children())).grid(row=r,column=7,sticky='e')
		r+=1
		ttk.Label(g,text='输出目录:').grid(row=r,column=0,sticky='e')
		ttk.Entry(g,textvariable=self.out_var,width=42).grid(row=r,column=1,columnspan=4,sticky='we',padx=4)
		ttk.Button(g,text='选择',command=self._pick_out).grid(row=r,column=5)
		ttk.Label(g,text='格式:').grid(row=r,column=6,sticky='e')
		ttk.Combobox(g,textvariable=self.format_var,values=['jpg','png','webp','ico'],width=6,state='readonly').grid(row=r,column=7,sticky='w')
		r+=1
		ttk.Label(g,text='命名:').grid(row=r,column=0,sticky='e')
		ttk.Entry(g,textvariable=self.pattern_var).grid(row=r,column=1,columnspan=2,sticky='we',padx=4)
		ttk.Label(g,text='起始/步长').grid(row=r,column=3,sticky='e')
		ttk.Spinbox(g,from_=1,to=999999,textvariable=self.start_var,width=7).grid(row=r,column=4,sticky='w')
		ttk.Spinbox(g,from_=1,to=9999,textvariable=self.step_var,width=6).grid(row=r,column=5,sticky='w')
		ttk.Label(g,text='覆盖').grid(row=r,column=6,sticky='e')
		ttk.Combobox(g,textvariable=self.overwrite_var,values=['overwrite','skip','rename'],width=8,state='readonly').grid(row=r,column=7,sticky='w')
		r+=1
		ttk.Label(g,text='质量').grid(row=r,column=0,sticky='e')
		ttk.Scale(g,from_=1,to=100,orient='horizontal',variable=self.quality_var).grid(row=r,column=1,columnspan=2,sticky='we')
		ttk.Checkbutton(g,text='同格式也重存',variable=self.process_same_var).grid(row=r,column=3,columnspan=2,sticky='w')
		ttk.Checkbutton(g,text='PNG3',variable=self.png3_var).grid(row=r,column=5,sticky='w')
		ttk.Label(g,text='线程').grid(row=r,column=6,sticky='e')
		ttk.Spinbox(g,from_=1,to=64,textvariable=self.workers_var,width=6).grid(row=r,column=7,sticky='w')
		r+=1
		self.progress = ttk.Progressbar(g,maximum=100)
		self.progress.grid(row=r,column=0,columnspan=8,sticky='we',pady=4)
		r+=1
		self.status_var = tk.StringVar(value='就绪')
		ttk.Label(g,textvariable=self.status_var,foreground='blue').grid(row=r,column=0,columnspan=8,sticky='w')
		r+=1
		container = ttk.Frame(g)
		container.grid(row=r,column=0,columnspan=8,sticky='nsew')
		g.rowconfigure(r,weight=1)
		container.columnconfigure(0,weight=3)
		container.columnconfigure(1,weight=1)
		self.log = ttk.Treeview(container,columns=('src','dst','res'),show='headings')
		for col,txt,w in [('src','源',260),('dst','目标',260),('res','结果',140)]:
			self.log.heading(col,text=txt); self.log.column(col,width=w,anchor='w',stretch=True)
		self.log.grid(row=0,column=0,sticky='nsew')
		vsb = ttk.Scrollbar(container,orient='vertical',command=self.log.yview)
		self.log.configure(yscrollcommand=vsb.set); vsb.grid(row=0,column=0,sticky='nse')
		preview = ttk.LabelFrame(container,text='预览')
		preview.grid(row=0,column=1,sticky='nsew',padx=(8,0))
		preview.columnconfigure(0,weight=1); preview.rowconfigure(0,weight=1)
		self.preview_label = ttk.Label(preview,text='(选择日志)')
		self.preview_label.grid(row=0,column=0,sticky='nsew',padx=4,pady=4)
		self.preview_info = tk.StringVar(value='')
		ttk.Label(preview,textvariable=self.preview_info,foreground='gray').grid(row=1,column=0,sticky='we')
		self.log.bind('<<TreeviewSelect>>', self._on_select_row)
		r+=1
		btnf = ttk.Frame(g); btnf.grid(row=r,column=0,columnspan=8,sticky='we',pady=4)
		ttk.Button(btnf,text='开始',command=self._start).pack(side='left',padx=4)
		ttk.Button(btnf,text='取消',command=self._cancel).pack(side='left',padx=4)

	# --- events ---
	def _pick_in(self):
		p = filedialog.askopenfilename() if messagebox.askyesno('选择','选择单文件? 是=文件 否=目录') else filedialog.askdirectory()
		if p: self.in_var.set(p)
	def _pick_out(self):
		d = filedialog.askdirectory()
		if d: self.out_var.set(d)

	def _start(self):
		if not self.in_var.get().strip() or not self.out_var.get().strip():
			self.status_var.set('请输入输入/输出'); return
		files = list(iter_image_files(self.in_var.get().strip(), self.recursive_var.get()))
		if not files: self.status_var.set('无文件'); return
		for i in self.log.get_children(): self.log.delete(i)
		self.progress['value']=0; self.progress['maximum']=len(files)
		self.status_var.set('准备...')
		self._tasks = self._build_tasks(files)
		started = self.start_in_thread(self._worker)
		if started:
			self.status_var.set('运行中...')

	def _build_tasks(self, files: List[str]):
		fmt = self.format_var.get(); pat = self.pattern_var.get(); idx = self.start_var.get(); step = self.step_var.get()
		outdir = self.out_var.get().strip(); os.makedirs(outdir, exist_ok=True)
		tasks=[]
		for f in files:
			ext = normalize_ext(f)
			if ext == fmt and not self.process_same_var.get():
				tasks.append((f,None))
			else:
				name = pat.replace('{fmt}', fmt).replace('{name}', os.path.splitext(os.path.basename(f))[0]).replace('{ext}', ext)
				if '{index}' in name: name = name.replace('{index}', str(idx))
				if '.' not in os.path.basename(name): name += f'.{fmt}'
				dst = os.path.join(outdir, name)
				if os.path.exists(dst):
					ow = self.overwrite_var.get()
					if ow == 'skip':
						tasks.append((f,None))
					elif ow == 'rename':
						dst = next_non_conflict(dst); tasks.append((f,dst))
					else:
						tasks.append((f,dst))
				else:
					tasks.append((f,dst))
			idx += step
		return tasks

	def _worker(self):
		fmt = self.format_var.get(); quality = self.quality_var.get(); png3 = self.png3_var.get()
		workers = max(1, self.workers_var.get())
		ico_sizes = [16,32,48,64,128,256]
		total = len(self._tasks); done=converted=skipped=failed=0
		lock = threading.Lock()
		def job(spec):
			nonlocal done,converted,skipped,failed
			src,dst = spec
			if self.stop_flag.is_set(): return
			if dst is None:
				with lock:
					skipped+=1; done+=1; self.q.put(f'PROG {done} {total}'); self.q.put(f'LOG\t{src}\t-\t跳过(同格式)\t0')
				return
			ok,msg = convert_one_image(src,dst,fmt,quality,png3,ico_sizes if fmt=='ico' else None)
			with lock:
				if ok: converted+=1
				else: failed+=1
				done+=1
				self.q.put(f'LOG\t{src}\t{dst}\t{msg}\t{1 if ok else 0}')
				self.q.put(f'PROG {done} {total}')
		if workers>1:
			with ThreadPoolExecutor(max_workers=workers) as ex:
				futs=[ex.submit(job,t) for t in self._tasks]
				for _ in as_completed(futs):
					if self.stop_flag.is_set(): break
		else:
			for t in self._tasks: job(t)
		self.q.put(f'SUM 转换{converted} 跳过{skipped} 失败{failed}')

	def _cancel(self):
		self.stop_flag.set(); self.status_var.set('取消中...')

	def poll(self):
		try:
			while True:
				m = self.q.get_nowait()
				if m.startswith('PROG '):
					_,d,total = m.split(); d=int(d); total=int(total); self.progress['value']=d; pct=int(d/total*100) if total else 0; self.status_var.set(f'{pct}% {d}/{total}')
				elif m.startswith('LOG\t'):
					_,src,dst,res,okflag = m.split('\t',4)
					self.log.insert('', 'end', values=(os.path.basename(src), os.path.basename(dst) if dst!='-' else '-', res), tags=('ok' if okflag=='1' else 'fail',))
				elif m.startswith('SUM '):
					self.status_var.set(m[4:])
		except queue.Empty:
			pass

	def _on_select_row(self, event=None):
		sel = self.log.selection();
		if not sel: return
		vals = self.log.item(sel[0],'values')
		if not vals: return
		fname = vals[0]
		root_in = self.in_var.get().strip()
		target=None
		if os.path.isdir(root_in):
			for p in iter_image_files(root_in, self.recursive_var.get()):
				if os.path.basename(p)==fname: target=p; break
		else:
			if os.path.isfile(root_in) and os.path.basename(root_in)==fname: target=root_in
		if not target or not os.path.exists(target):
			self.preview_label.configure(text='找不到文件', image='')
			return
		try:
			with Image.open(target) as im:
				w,h=im.size; max_side=300; scale=min(max_side/w,max_side/h,1)
				if scale<1: im=im.resize((int(w*scale),int(h*scale)))
				photo=ImageTk.PhotoImage(im)
			self.preview_label.configure(image=photo,text=''); self._preview_ref=photo
			self.preview_info.set(f'{w}x{h} {fname}')
		except Exception as e:
			self.preview_label.configure(text=f'预览失败:{e}', image='')

# ---------------- 文本编码转换 (核心精简) ----------------
TEXT_GUESS_EXT = {'.txt','.csv','.tsv','.md','.json','.py','.xml','.html','.htm','.css','.js','.yml','.yaml','.ini','.cfg','.log'}
try:
	from charset_normalizer import from_path as cn_from_path  # type: ignore
except Exception:
	cn_from_path=None
@dataclass
class DetectResult: encoding:str; confidence:float; used:str; bom:bool

def detect_encoding(path: str, specified: Optional[str]) -> DetectResult:
	with open(path,'rb') as f: raw=f.read()
	if specified: return DetectResult(specified,1.0,'specified', raw.startswith(b'\xef\xbb\xbf'))
	if cn_from_path is not None:
		try:
			matches = cn_from_path(path)
			if matches:
				best = matches.best()
				if best and best.encoding:
					return DetectResult(best.encoding.lower(), 0.9,'charset-normalizer', raw.startswith(b'\xef\xbb\xbf'))
		except Exception: pass
	for enc in ['utf-8','utf-8-sig','gb18030','gbk','latin-1']:
		try:
			raw.decode(enc); return DetectResult(enc,0.3,'fallback', raw.startswith(b'\xef\xbb\xbf'))
		except Exception: continue
	return DetectResult('latin-1',0.0,'forced', raw.startswith(b'\xef\xbb\xbf'))

def convert_text(src: str, dst: str, from_enc: Optional[str], to_enc: str, add_bom: bool, remove_bom: bool, skip_same: bool, errors: str) -> Tuple[str,str,bool]:
	try:
		det = detect_encoding(src, from_enc)
		with open(src,'rb') as f: raw=f.read()
		text = raw.decode(det.encoding, errors='replace')
		orig_bom = raw.startswith(b'\xef\xbb\xbf')
		need_bom = add_bom and not remove_bom
		same = (det.encoding.replace('-sig','') == to_enc.replace('-sig','') and (orig_bom==need_bom))
		if skip_same and same:
			return 'SKIP', f'same {det.encoding}{"+BOM" if orig_bom else ""}', False
		os.makedirs(os.path.dirname(dst), exist_ok=True)
		if to_enc=='utf-8' and need_bom:
			with open(dst,'wb') as f: f.write(b'\xef\xbb\xbf'); f.write(text.encode('utf-8',errors=errors))
		else:
			with open(dst,'w',encoding=to_enc,errors=errors,newline='') as f: f.write(text)
		return 'OK', f'{det.encoding}{"+BOM" if orig_bom else ""}->{to_enc}{"+BOM" if need_bom else ""}', True
	except Exception as e:
		return 'FAIL', str(e), False

class TextEncodingModule(ToolBase):
	def __init__(self, parent: tk.Widget):  # type: ignore
		super().__init__(); self.parent=parent
		self.in_var = tk.StringVar(); self.out_var = tk.StringVar()
		self.recursive_var = tk.BooleanVar(value=True)
		self.from_var = tk.StringVar(); self.to_var = tk.StringVar(value='utf-8')
		self.ext_var = tk.StringVar(); self.skip_same_var = tk.BooleanVar(value=True)
		self.add_bom_var = tk.BooleanVar(value=False); self.remove_bom_var = tk.BooleanVar(value=False)
		self.errors_var = tk.StringVar(value='strict'); self.workers_var = tk.IntVar(value=max(2,(os.cpu_count() or 4)//2))
		self._build()
	def _build(self):
		g = ttk.Frame(self.parent); g.pack(fill='both',expand=True)
		for i in range(6): g.columnconfigure(i,weight=1)
		r=0
		ttk.Label(g,text='输入:').grid(row=r,column=0,sticky='e'); ttk.Entry(g,textvariable=self.in_var,width=44).grid(row=r,column=1,columnspan=3,sticky='we',padx=4)
		ttk.Button(g,text='选择',command=self._pick_in).grid(row=r,column=4)
		ttk.Checkbutton(g,text='递归',variable=self.recursive_var).grid(row=r,column=5,sticky='w'); r+=1
		ttk.Label(g,text='输出目录:').grid(row=r,column=0,sticky='e'); ttk.Entry(g,textvariable=self.out_var,width=44).grid(row=r,column=1,columnspan=3,sticky='we',padx=4); ttk.Button(g,text='选择',command=self._pick_out).grid(row=r,column=4)
		ttk.Label(g,text='线程').grid(row=r,column=5,sticky='e'); ttk.Spinbox(g,from_=1,to=64,textvariable=self.workers_var,width=6).grid(row=r,column=5,sticky='w',padx=(40,0))
		r+=1
		ttk.Label(g,text='源(空自动)').grid(row=r,column=0,sticky='e'); ttk.Entry(g,textvariable=self.from_var,width=12).grid(row=r,column=1,sticky='w')
		ttk.Label(g,text='目标').grid(row=r,column=2,sticky='e'); ttk.Combobox(g,textvariable=self.to_var,values=['utf-8','utf-8-sig','gbk','gb18030','big5','shift_jis','latin-1','utf-16'],width=12).grid(row=r,column=3,sticky='w')
		ttk.Label(g,text='扩展').grid(row=r,column=4,sticky='e'); ttk.Entry(g,textvariable=self.ext_var,width=10).grid(row=r,column=5,sticky='w'); r+=1
		for col,(txt,var) in enumerate([('跳过相同',self.skip_same_var),('加BOM',self.add_bom_var),('去BOM',self.remove_bom_var)]):
			ttk.Checkbutton(g,text=txt,variable=var).grid(row=r,column=col,sticky='w')
		ttk.Label(g,text='错误策略').grid(row=r,column=3,sticky='e'); ttk.Combobox(g,textvariable=self.errors_var,values=['strict','ignore','replace'],width=10,state='readonly').grid(row=r,column=4,sticky='w'); r+=1
		self.progress = ttk.Progressbar(g,maximum=100); self.progress.grid(row=r,column=0,columnspan=6,sticky='we',pady=4); r+=1
		self.status_var = tk.StringVar(value='就绪'); ttk.Label(g,textvariable=self.status_var,foreground='blue').grid(row=r,column=0,columnspan=6,sticky='w'); r+=1
		container = ttk.Frame(g); container.grid(row=r,column=0,columnspan=6,sticky='nsew'); g.rowconfigure(r,weight=1); container.columnconfigure(0,weight=2); container.columnconfigure(1,weight=3)
		self.file_tree = ttk.Treeview(container,columns=('st','file','note'),show='headings');
		for col,txt,w in [('st','状态',60),('file','文件',240),('note','说明',300)]: self.file_tree.heading(col,text=txt); self.file_tree.column(col,width=w,anchor='w',stretch=True)
		self.file_tree.grid(row=0,column=0,sticky='nsew'); vsb=ttk.Scrollbar(container,orient='vertical',command=self.file_tree.yview); self.file_tree.configure(yscrollcommand=vsb.set); vsb.grid(row=0,column=0,sticky='nse')
		prev = ttk.LabelFrame(container,text='预览(前200行)'); prev.grid(row=0,column=1,sticky='nsew',padx=(8,0)); prev.columnconfigure(0,weight=1); prev.rowconfigure(0,weight=1)
		self.preview = tk.Text(prev,wrap='none'); self.preview.grid(row=0,column=0,sticky='nsew'); vsb2=ttk.Scrollbar(prev,orient='vertical',command=self.preview.yview); self.preview.configure(yscrollcommand=vsb2.set); vsb2.grid(row=0,column=1,sticky='ns')
		self.file_tree.bind('<<TreeviewSelect>>', self._on_select_row)
		r+=1
		btnf=ttk.Frame(g); btnf.grid(row=r,column=0,columnspan=6,sticky='we',pady=4)
		ttk.Button(btnf,text='开始',command=self._start).pack(side='left',padx=4); ttk.Button(btnf,text='取消',command=self._cancel).pack(side='left',padx=4)

	def _pick_in(self):
		path = filedialog.askdirectory() or ''
		if path: self.in_var.set(path)
	def _pick_out(self):
		path = filedialog.askdirectory() or ''
		if path: self.out_var.set(path)

	def _start(self):
		if not self.in_var.get().strip() or not self.out_var.get().strip(): self.status_var.set('需输入/输出'); return
		files=[]
		for base,dirs,fs in os.walk(self.in_var.get().strip()):
			for f in fs:
				full=os.path.join(base,f)
				if self.ext_var.get().strip():
					exts=[e if e.startswith('.') else '.'+e for e in self.ext_var.get().split(',') if e.strip()]
					if os.path.splitext(f)[1].lower() not in [e.lower() for e in exts]: continue
				elif os.path.splitext(f)[1].lower() not in TEXT_GUESS_EXT: continue
				files.append(full)
			if not self.recursive_var.get(): break
		if not files: self.status_var.set('无文件'); return
		for i in self.file_tree.get_children(): self.file_tree.delete(i)
		self.progress['value']=0; self.progress['maximum']=len(files)
		self._tasks=files; self.start_in_thread(self._worker); self.status_var.set('运行中...')

	def _worker(self):
		total=len(self._tasks); done=okc=skipc=failc=0
		workers=max(1,self.workers_var.get())
		from_enc=self.from_var.get().strip() or None; to_enc=self.to_var.get().strip(); errors=self.errors_var.get()
		add_bom=self.add_bom_var.get(); rem_bom=self.remove_bom_var.get(); skip_same=self.skip_same_var.get(); outdir=self.out_var.get().strip()
		lock=threading.Lock()
		def job(src):
			nonlocal done,okc,skipc,failc
			if self.stop_flag.is_set(): return
			rel=os.path.relpath(src,self.in_var.get().strip())
			dst=os.path.join(outdir,rel)
			status,msg,changed=convert_text(src,dst,from_enc,to_enc,add_bom,rem_bom,skip_same,errors)
			with lock:
				if status=='OK':
					if changed: okc+=1
					else: skipc+=1
				elif status=='SKIP': skipc+=1
				else: failc+=1
				done+=1
				self.q.put(f'FILE\t{status}\t{1 if changed else 0}\t{rel}\t{msg}\t{src}')
				self.q.put(f'PROG {done} {total}')
		if workers>1:
			with ThreadPoolExecutor(max_workers=workers) as ex:
				futs=[ex.submit(job,s) for s in self._tasks]
				for _ in as_completed(futs):
					if self.stop_flag.is_set(): break
		else:
			for s in self._tasks: job(s)
		self.q.put(f'SUM 转换{okc} 跳过{skipc} 失败{failc}')

	def poll(self):
		try:
			while True:
				m=self.q.get_nowait()
				if m.startswith('PROG '):
					_,d,total=m.split();d=int(d);total=int(total);self.progress['value']=d;pct=int(d/total*100) if total else 0; self.status_var.set(f'{pct}% {d}/{total}')
				elif m.startswith('FILE\t'):
					_tag,st,ch,rel,note,abspath=m.split('\t',5)
					icon='✔' if st=='OK' and ch=='1' else ('~' if st=='SKIP' else '✖')
					self.file_tree.insert('', 'end', values=(icon, rel, note), tags=(abspath,))
				elif m.startswith('SUM '): self.status_var.set(m[4:])
		except queue.Empty: pass

	def _on_select_row(self, event=None):
		sel=self.file_tree.selection();
		if not sel: return
		abspath=self.file_tree.item(sel[0],'tags')
		if not abspath: return
		path=abspath[0]
		if not os.path.exists(path): self.preview.delete('1.0','end'); self.preview.insert('end','文件不存在'); return
		try:
			det=detect_encoding(path,None)
			with open(path,'rb') as f: raw=f.read()
			txt=raw.decode(det.encoding,errors='replace')
			lines=txt.splitlines(); head=lines[:200]
			content='\n'.join(f'{i+1:4d}: {line}' for i,line in enumerate(head))
			if len(lines)>200: content+=f'\n... (共{len(lines)}行)'
			self.preview.delete('1.0','end'); self.preview.insert('end', f'编码:{det.encoding} 方式:{det.used} 置信~{det.confidence:.2f} BOM:{"Y" if det.bom else "N"}\n---\n{content}')
		except Exception as e:
			self.preview.delete('1.0','end'); self.preview.insert('end', f'预览失败: {e}')

	def _cancel(self): self.stop_flag.set(); self.status_var.set('取消中...')

# ---------------- 重复图片清理 (聚合精简) ----------------
@dataclass
class ImgInfo: path:str; size:int; w:int; h:int; ah:int; dh:int; mtime:float
	
SUPPORTED_HASH_EXT = {'.jpg','.jpeg','.png','.webp','.gif','.bmp','.tif','.tiff'}

def ahash(im: Image.Image, size: int=8) -> int:
	im2=im.convert('L').resize((size,size), Image.LANCZOS)
	pixels=list(im2.getdata()); avg=sum(pixels)/len(pixels); bits=0
	for p in pixels: bits=(bits<<1)|(1 if p>=avg else 0)
	return bits

def dhash(im: Image.Image, size: int=8) -> int:
	im2=im.convert('L').resize((size+1,size), Image.LANCZOS); px=list(im2.getdata()); bits=0
	for r in range(size): row=px[r*(size+1):(r+1)*(size+1)];
	
	return bits
# (为保持答复长度, 此模块仅占位; 可后续继续整合完整重复图片清理逻辑)

class DuplicateImageModule(ToolBase):
	def __init__(self, parent):  # 移除严格类型注解兼容 py <3.9
		super().__init__(); self.parent=parent
		self.input_var = tk.StringVar()
		self.recursive_var = tk.BooleanVar(value=True)
		self.threshold_var = tk.IntVar(value=0)
		self.keep_var = tk.StringVar(value='largest')
		self.action_var = tk.StringVar(value='list')
		self.workers_var = tk.IntVar(value=max(2,(os.cpu_count() or 4)//2))
		self._build()

	def _build(self):
		g=ttk.Frame(self.parent); g.pack(fill='both',expand=True)
		for i in range(8): g.columnconfigure(i,weight=1)
		r=0
		ttk.Label(g,text='输入目录:').grid(row=r,column=0,sticky='e')
		ttk.Entry(g,textvariable=self.input_var,width=52).grid(row=r,column=1,columnspan=4,sticky='we',padx=4)
		ttk.Button(g,text='选择',command=self._pick_dir).grid(row=r,column=5)
		ttk.Checkbutton(g,text='递归',variable=self.recursive_var).grid(row=r,column=6,sticky='w')
		ttk.Label(g,text='线程').grid(row=r,column=7,sticky='e')
		ttk.Spinbox(g,from_=1,to=64,textvariable=self.workers_var,width=6).grid(row=r,column=7,sticky='w',padx=(40,0))
		r+=1
		ttk.Label(g,text='阈值').grid(row=r,column=0,sticky='e'); ttk.Spinbox(g,from_=0,to=16,textvariable=self.threshold_var,width=6).grid(row=r,column=1,sticky='w')
		ttk.Label(g,text='保留').grid(row=r,column=2,sticky='e'); ttk.Combobox(g,textvariable=self.keep_var,values=['first','largest','largest-file','newest','oldest'],width=12).grid(row=r,column=3,sticky='w')
		ttk.Label(g,text='动作').grid(row=r,column=4,sticky='e'); ttk.Combobox(g,textvariable=self.action_var,values=['list','delete','move'],width=10).grid(row=r,column=5,sticky='w')
		r+=1
		self.progress=ttk.Progressbar(g,maximum=100); self.progress.grid(row=r,column=0,columnspan=8,sticky='we',pady=4); r+=1
		self.status=tk.StringVar(value='就绪'); ttk.Label(g,textvariable=self.status,foreground='blue').grid(row=r,column=0,columnspan=8,sticky='w'); r+=1
		container=ttk.Frame(g); container.grid(row=r,column=0,columnspan=8,sticky='nsew'); g.rowconfigure(r,weight=1)
		container.columnconfigure(0,weight=4); container.columnconfigure(1,weight=2)
		self.tree=ttk.Treeview(container,columns=('gid','keep','path','res','kb','act'),show='headings')
		headers={'gid':'组','keep':'保留','path':'路径','res':'分辨率','kb':'大小KB','act':'动作'}
		widths={'gid':50,'keep':50,'path':420,'res':120,'kb':90,'act':80}
		for c in ('gid','keep','path','res','kb','act'):
			self.tree.heading(c,text=headers[c]); self.tree.column(c,width=widths[c],anchor='w',stretch=True if c=='path' else False)
		self.tree.grid(row=0,column=0,sticky='nsew'); vsb=ttk.Scrollbar(container,orient='vertical',command=self.tree.yview); self.tree.configure(yscrollcommand=vsb.set); vsb.grid(row=0,column=0,sticky='nse')
		prev=ttk.LabelFrame(container,text='预览'); prev.grid(row=0,column=1,sticky='nsew',padx=(8,0))
		prev.columnconfigure(0,weight=1); prev.rowconfigure(0,weight=1)
		self.prev_label=ttk.Label(prev,text='(选择行)'); self.prev_label.grid(row=0,column=0,sticky='nsew',padx=4,pady=4)
		self.prev_info=tk.StringVar(value=''); ttk.Label(prev,textvariable=self.prev_info,foreground='gray').grid(row=1,column=0,sticky='we')
		self.tree.bind('<<TreeviewSelect>>', self._on_select)
		r+=1
		btnf=ttk.Frame(g); btnf.grid(row=r,column=0,columnspan=8,sticky='we',pady=4)
		ttk.Button(btnf,text='开始',command=self._start).pack(side='left',padx=4)
		ttk.Button(btnf,text='取消',command=self._cancel).pack(side='left',padx=4)

	def _pick_dir(self):
		d=filedialog.askdirectory()
		if d: self.input_var.set(d)

	def _start(self):
		root=self.input_var.get().strip()
		if not root or not os.path.isdir(root): self.status.set('目录无效'); return
		files=[p for p in iter_image_files(root, self.recursive_var.get()) if os.path.splitext(p)[1].lower() in SUPPORTED_HASH_EXT]
		if not files: self.status.set('无图片'); return
		for i in self.tree.get_children(): self.tree.delete(i)
		self.progress['value']=0; self.progress['maximum']=len(files)
		self._files=files; self._groups=None
		self.start_in_thread(self._worker); self.status.set('扫描中...')

	def _worker(self):
		th=self.threshold_var.get(); keep_mode=self.keep_var.get(); workers=max(1,self.workers_var.get())
		infos=[]; lock=threading.Lock(); total=len(self._files); done=0
		def compute(path):
			nonlocal done
			try:
				with Image.open(path) as im:
					w,h=im.size; ah=ahash(im); dh=dhash(im); st=os.stat(path)
				info=ImgInfo(path, st.st_size, w,h,ah,dh, st.st_mtime)
			except Exception:
				info=None
			with lock:
				done+=1; self.q.put(f'HASH {done} {total}')
			return info
		with ThreadPoolExecutor(max_workers=workers) as ex:
			for fut in as_completed([ex.submit(compute,f) for f in self._files]):
				res=fut.result();
				if res: infos.append(res)
		# 分组
		groups=[]
		for info in infos:
			placed=False
			for g in groups:
				rep=g[0]
				if th==0:
					if info.ah==rep.ah and info.dh==rep.dh:
						g.append(info); placed=True; break
				else:
					if bin(info.ah ^ rep.ah).count('1') + bin(info.dh ^ rep.dh).count('1') <= th:
						g.append(info); placed=True; break
			if not placed: groups.append([info])
		groups=[g for g in groups if len(g)>1]
		gid=0
		for g in sorted(groups, key=lambda x: -len(x)):
			gid+=1
			if keep_mode=='largest': keep=max(g,key=lambda x:x.w*x.h)
			elif keep_mode=='largest-file': keep=max(g,key=lambda x:x.size)
			elif keep_mode=='newest': keep=max(g,key=lambda x:x.mtime)
			elif keep_mode=='oldest': keep=min(g,key=lambda x:x.mtime)
			else: keep=g[0]
			for it in g:
				act='保留' if it is keep else '重复'
				self.q.put(f'ROW {gid}\t{1 if it is keep else 0}\t{it.path}\t{it.w}x{it.h}\t{int(it.size/1024)}\t{act}')
		self.q.put('DONE')

	def poll(self):
		try:
			while True:
				m=self.q.get_nowait()
				if m.startswith('HASH '):
					_,d,total=m.split(); d=int(d); total=int(total); self.progress['value']=d; pct=int(d/total*100) if total else 0; self.status.set(f'哈希 {pct}% ({d}/{total})')
				elif m.startswith('ROW '):
					_tag, gid, keep, path, res, kb, act = m.split('\t')
					self.tree.insert('', 'end', values=(gid, '★' if keep=='1' else '', path, res, kb, act))
				elif m=='DONE':
					self.status.set('完成')
		except queue.Empty:
			pass

	def _on_select(self, event=None):
		sel=self.tree.selection();
		if not sel: return
		vals=self.tree.item(sel[0],'values')
		if len(vals)<3: return
		path=vals[2]
		if not os.path.exists(path): self.prev_label.configure(text='不存在',image=''); return
		try:
			with Image.open(path) as im:
				w,h=im.size; max_side=300; scale=min(300/w,300/h,1)
				if scale<1: im=im.resize((int(w*scale),int(h*scale)))
				photo=ImageTk.PhotoImage(im)
			self.prev_label.configure(image=photo,text=''); self._prev_ref=photo; self.prev_info.set(f'{w}x{h} {os.path.basename(path)}')
		except Exception as e:
			self.prev_label.configure(text=f'预览失败:{e}', image='')

	def _cancel(self): self.stop_flag.set(); self.status.set('取消中...')

# ---------------- 主窗口 ----------------
class UnifiedApp:
	def __init__(self, root: tk.Tk):  # type: ignore
		self.root=root
		root.title('统一工具窗口')
		root.geometry('1180x760')
		nb=ttk.Notebook(root); nb.pack(fill='both',expand=True)
		tab_img=ttk.Frame(nb); tab_txt=ttk.Frame(nb); tab_dup=ttk.Frame(nb)
		nb.add(tab_img,text='图片转换')
		nb.add(tab_txt,text='文本编码')
		nb.add(tab_dup,text='重复图片(占位)')
		self.img_mod=ImageConvertModule(tab_img)
		self.txt_mod=TextEncodingModule(tab_txt)
		self.dup_mod=DuplicateImageModule(tab_dup)
		self._poll()
	def _poll(self):
		self.img_mod.poll(); self.txt_mod.poll(); self.dup_mod.poll()
		self.root.after(150,self._poll)


def launch():
	if tk is None:
		print('Tkinter 不可用'); return 2
	root=tk.Tk(); UnifiedApp(root); root.mainloop()

if __name__=='__main__':
	launch()
