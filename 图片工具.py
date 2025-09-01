"""图片工具
单窗口批处理: 图片 去重 / 转换 / 重命名。

流程: (可选)去重 -> (可选)转换/重命名
重命名占位: {name} {ext} {index} {fmt}
"""
from __future__ import annotations
import os, sys, threading, queue, shutil, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Iterable

try:
	import tkinter as tk
	from tkinter import ttk, filedialog, messagebox
except Exception:  # pragma: no cover
	tk=None  # type: ignore

try:
	from PIL import Image, ImageSequence, ImageFile, ImageTk  # type: ignore
except Exception:  # pragma: no cover
	Image=None  # type: ignore

# Windows 回收站支持 (可选)
try:
	from send2trash import send2trash  # type: ignore
except Exception:  # pragma: no cover
	send2trash=None  # type: ignore

ImageFile.LOAD_TRUNCATED_IMAGES = True  # 更宽容
SUPPORTED_EXT={'.jpg','.jpeg','.png','.webp','.gif','.bmp','.tiff','.ico'}

# 显示 -> 内部代码 映射
KEEP_MAP={
	'首个':'first',
	'最大分辨率':'largest',
	'最大文件':'largest-file',
	'最新':'newest',
	'最旧':'oldest',
}
ACTION_MAP={
	'仅列出':'list',
	'删除重复':'delete',
	'移动重复':'move',
}
FMT_MAP={
	'JPG(JPEG)':'jpg',
	'PNG':'png',
	'WebP':'webp',
	'ICO图标':'ico',
}
OVERWRITE_MAP={
	'覆盖原有':'overwrite',
	'跳过已存在':'skip',
	'自动改名':'rename',
}

# 日志阶段到中文显示
STAGE_MAP_DISPLAY={
	'DEDUP':'去重',
	'CONVERT':'转换',
	'RENAME':'重命名',
}

def _rev_map(mp:dict):
	return {v:k for k,v in mp.items()}

def iter_images(root:str, recursive:bool) -> Iterable[str]:
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

def safe_delete(path:str,use_trash:bool):
	"""删除文件: Windows 且 use_trash 时尝试送回收站, 否则直接删除."""
	if use_trash and send2trash is not None:
		try:
			send2trash(path)
			return True,'删除->回收站'
		except Exception as e:
			return False,f'回收站失败:{e}'
	try:
		os.remove(path); return True,'删除'
	except Exception as e:
		return False,f'删失败:{e}'

def ahash(im):
	im=im.convert('L').resize((8,8))
	avg=sum(im.getdata())/64.0
	bits=0
	for i,p in enumerate(im.getdata()):
		if p>=avg: bits|=1<<i
	return bits

def dhash(im):
	im=im.convert('L').resize((9,8))
	pixels=list(im.getdata())
	bits=0; idx=0
	for r in range(8):
		row=pixels[r*9:(r+1)*9]
		for c in range(8):
			if row[c] > row[c+1]: bits|=1<<idx
			idx+=1
	return bits

def hamming(a:int,b:int)->int:
	return (a^b).bit_count()

def convert_one(src,dst,fmt,quality=None,png3=False,ico_sizes=None):
	try:
		with Image.open(src) as im:  # type: ignore
			if fmt=='gif':
				im.save(dst,save_all=True)
			elif fmt=='ico':
				im.save(dst, sizes=[(s,s) for s in (ico_sizes or [256])])
			else:
				params={}
				if fmt=='jpg':
					params['quality']=quality or 85
					if im.mode in ('RGBA','LA'):
						bg=Image.new('RGB',im.size,(255,255,255))
						bg.paste(im,mask=im.split()[-1])
						im=bg
					else:
						im=im.convert('RGB')
				elif fmt=='png':
					if png3:
						im=im.convert('P',palette=Image.ADAPTIVE,colors=256)
				elif fmt=='webp':
					params['quality']=quality or 80
				im.save(dst, fmt.upper(), **params)
		return True,'OK'
	except Exception as e:
		return False,str(e)

@dataclass
class ImgInfo:
	path:str; size:int; w:int; h:int; ah:int; dh:int; mtime:float
	@property
	def res(self): return self.w*self.h

class ImageToolApp:
	def __init__(self, root):
		self.root=root; root.title('图片工具')
		self.q=queue.Queue(); self.worker=None; self.stop_flag=threading.Event()
		self._all_files=[]
		self._preview_ref=None
		self._tooltip=None; self._tooltip_after=None
		self.frame_convert=None; self.frame_rename=None
		self.move_dir_entry=None; self.move_dir_btn=None
		self.dry_run=False
		# 回收站 (Windows / Linux 桌面发送至废纸篓) 默认仅在 send2trash 可用时开启
		self.use_trash=tk.BooleanVar(value=(send2trash is not None)) if tk else None
		self.trash_cb=None
		self.last_out_dir=None
		self._build()
		self.root.after(200,self._drain)

	# ---------------- UI ----------------
	def _build(self):
		outer=ttk.Frame(self.root,padding=(8,6,8,6)); outer.pack(fill='both',expand=True)
		# I/O (支持目录或单文件)
		io=ttk.Frame(outer); io.pack(fill='x',pady=(0,6))
		for i in range(10): io.columnconfigure(i,weight=1 if i in (1,6) else 0)
		ttk.Label(io,text='输入:').grid(row=0,column=0,sticky='e')
		self.in_var=tk.StringVar(); ent_in=ttk.Entry(io,textvariable=self.in_var,width=40); ent_in.grid(row=0,column=1,sticky='we',padx=3)
		btn_in=ttk.Button(io,text='目录',command=self._pick_in,width=5); btn_in.grid(row=0,column=2,padx=(0,3))
		btn_in_file=ttk.Button(io,text='文件',command=self._pick_in_file,width=5); btn_in_file.grid(row=0,column=3,padx=(0,8))
		self.recursive_var=tk.BooleanVar(value=True); cb_rec=ttk.Checkbutton(io,text='递归',variable=self.recursive_var); cb_rec.grid(row=0,column=4,sticky='w')
		ttk.Label(io,text='输出:').grid(row=0,column=5,sticky='e')
		self.out_var=tk.StringVar(); ent_out=ttk.Entry(io,textvariable=self.out_var,width=32); ent_out.grid(row=0,column=6,sticky='we',padx=3)
		btn_out=ttk.Button(io,text='选择',command=self._pick_out,width=6); btn_out.grid(row=0,column=7,padx=(2,0))
		btn_open_out=ttk.Button(io,text='打开',command=self._open_last_out,width=6); btn_open_out.grid(row=0,column=8,padx=(4,0))
		# 功能
		opts=ttk.Frame(outer); opts.pack(fill='x',pady=(0,8))
		self.enable_dedupe=tk.BooleanVar(value=True)
		self.enable_convert=tk.BooleanVar(value=True)
		self.enable_rename=tk.BooleanVar(value=True)
		cb_dedupe=ttk.Checkbutton(opts,text='去重',variable=self.enable_dedupe); cb_dedupe.pack(side='left',padx=2)
		cb_convert=ttk.Checkbutton(opts,text='转换',variable=self.enable_convert); cb_convert.pack(side='left',padx=2)
		cb_rename=ttk.Checkbutton(opts,text='重命名',variable=self.enable_rename); cb_rename.pack(side='left',padx=2)
		ttk.Label(opts,text='线程').pack(side='left',padx=(12,2))
		self.workers_var=tk.IntVar(value=max(2,(os.cpu_count() or 4)//2))
		sp_workers=ttk.Spinbox(opts,from_=1,to=64,textvariable=self.workers_var,width=5); sp_workers.pack(side='left')
		btn_start=ttk.Button(opts,text='开始',command=self._start,width=8); btn_start.pack(side='right',padx=2)
		btn_preview=ttk.Button(opts,text='预览',command=self._preview,width=8); btn_preview.pack(side='right',padx=2)
		btn_cancel=ttk.Button(opts,text='取消',command=self._cancel,width=8); btn_cancel.pack(side='right',padx=2)
		# 去重
		ttk.Separator(outer,orient='horizontal').pack(fill='x',pady=(0,4))
		dedupe=ttk.LabelFrame(outer,text='去重设置'); dedupe.pack(fill='x',pady=(0,10))
		self.frame_dedupe=dedupe
		self.threshold_var=tk.IntVar(value=0)
		self.keep_var=tk.StringVar(value=_rev_map(KEEP_MAP)['largest'])
		self.dedup_action_var=tk.StringVar(value=_rev_map(ACTION_MAP)['list'])
		self.move_dir_var=tk.StringVar()
		for i in range(11): dedupe.columnconfigure(i,weight=0)
		ttk.Label(dedupe,text='阈值').grid(row=0,column=0,sticky='e')
		sp_th=ttk.Spinbox(dedupe,from_=0,to=32,textvariable=self.threshold_var,width=5); sp_th.grid(row=0,column=1,sticky='w',padx=(0,8))
		ttk.Label(dedupe,text='保留').grid(row=0,column=2,sticky='e')
		cb_keep=ttk.Combobox(dedupe,textvariable=self.keep_var,values=list(KEEP_MAP.keys()),width=12,state='readonly'); cb_keep.grid(row=0,column=3,sticky='w',padx=(0,8))
		ttk.Label(dedupe,text='动作').grid(row=0,column=4,sticky='e')
		cb_action=ttk.Combobox(dedupe,textvariable=self.dedup_action_var,values=list(ACTION_MAP.keys()),width=10,state='readonly'); cb_action.grid(row=0,column=5,sticky='w',padx=(0,8))
		if self.use_trash is not None:
			self.trash_cb=ttk.Checkbutton(dedupe,text='回收站',variable=self.use_trash)
			self.trash_cb.grid(row=0,column=6,sticky='w',padx=(0,6))
			if send2trash is None:
				self.trash_cb.state(['disabled'])
				self.trash_cb.configure(text='回收站(缺依赖)')
				self.root.after(100, lambda: self.status_var.set('缺少 send2trash, 运行: pip install send2trash'))
		col_mv=7 if self.trash_cb else 6
		ttk.Label(dedupe,text='移动到').grid(row=0,column=col_mv,sticky='e')
		self.move_dir_entry=ttk.Entry(dedupe,textvariable=self.move_dir_var,width=24); self.move_dir_entry.grid(row=0,column=col_mv+1,sticky='w')
		self.move_dir_btn=ttk.Button(dedupe,text='选',command=self._pick_move_dir,width=4); self.move_dir_btn.grid(row=0,column=col_mv+2,sticky='w',padx=(4,0))
		# 转换
		ttk.Separator(outer,orient='horizontal').pack(fill='x',pady=(0,4))
		convert=ttk.LabelFrame(outer,text='格式转换'); convert.pack(fill='x',pady=(0,10))
		self.frame_convert=convert
		self.fmt_var=tk.StringVar(value=_rev_map(FMT_MAP)['webp'])
		self.quality_var=tk.IntVar(value=85)
		self.process_same_var=tk.BooleanVar(value=False)
		self.png3_var=tk.BooleanVar(value=False)
		ttk.Label(convert,text='格式').grid(row=0,column=0,sticky='e')
		cb_fmt=ttk.Combobox(convert,textvariable=self.fmt_var,values=list(FMT_MAP.keys()),width=12,state='readonly'); cb_fmt.grid(row=0,column=1,sticky='w',padx=(0,12))
		ttk.Label(convert,text='质量').grid(row=0,column=2,sticky='e')
		sc_q=ttk.Scale(convert,from_=1,to=100,orient='horizontal',variable=self.quality_var,length=220); sc_q.grid(row=0,column=3,sticky='we',padx=(2,6))
		sp_q=ttk.Spinbox(convert,from_=1,to=100,textvariable=self.quality_var,width=5); sp_q.grid(row=0,column=4,sticky='w',padx=(0,8))
		cb_same=ttk.Checkbutton(convert,text='同格式也重存',variable=self.process_same_var); cb_same.grid(row=0,column=5,sticky='w')
		cb_png3=ttk.Checkbutton(convert,text='PNG3压缩',variable=self.png3_var); cb_png3.grid(row=0,column=6,sticky='w')
		for i in range(7): convert.columnconfigure(i,weight=1 if i==3 else 0)
		# 重命名
		ttk.Separator(outer,orient='horizontal').pack(fill='x',pady=(0,4))
		rename=ttk.LabelFrame(outer,text='重命名'); rename.pack(fill='x',pady=(0,10))
		self.frame_rename=rename
		self.pattern_var=tk.StringVar(value='{name}_{index}.{fmt}')
		self.start_var=tk.IntVar(value=1)
		self.step_var=tk.IntVar(value=1)
		self.overwrite_var=tk.StringVar(value=_rev_map(OVERWRITE_MAP)['overwrite'])
		self.rename_remove_src=tk.BooleanVar(value=False)
		ttk.Label(rename,text='模式').grid(row=0,column=0,sticky='e')
		ent_pattern=ttk.Entry(rename,textvariable=self.pattern_var,width=42); ent_pattern.grid(row=0,column=1,sticky='w',padx=(0,8))
		ttk.Label(rename,text='起始').grid(row=0,column=2,sticky='e')
		sp_start=ttk.Spinbox(rename,from_=1,to=999999,textvariable=self.start_var,width=7); sp_start.grid(row=0,column=3,sticky='w')
		ttk.Label(rename,text='步长').grid(row=0,column=4,sticky='e')
		sp_step=ttk.Spinbox(rename,from_=1,to=9999,textvariable=self.step_var,width=5); sp_step.grid(row=0,column=5,sticky='w')
		ttk.Label(rename,text='覆盖策略').grid(row=0,column=6,sticky='e')
		cb_over=ttk.Combobox(rename,textvariable=self.overwrite_var,values=list(OVERWRITE_MAP.keys()),width=12,state='readonly'); cb_over.grid(row=0,column=7,sticky='w')
		cb_rm_src=ttk.Checkbutton(rename,text='删源',variable=self.rename_remove_src)
		cb_rm_src.grid(row=0,column=8,sticky='w',padx=(8,0))
		for i in range(9): rename.columnconfigure(i,weight=0)
		# 进度
		ttk.Separator(outer,orient='horizontal').pack(fill='x',pady=(0,6))
		self.progress=ttk.Progressbar(outer,maximum=100); self.progress.pack(fill='x',pady=(0,4))
		self.status_var=tk.StringVar(value='就绪'); ttk.Label(outer,textvariable=self.status_var,foreground='blue').pack(fill='x')
		# 日志
		ttk.Separator(outer,orient='horizontal').pack(fill='x',pady=(4,4))
		pan=ttk.PanedWindow(outer,orient='vertical'); pan.pack(fill='both',expand=True)
		upper=ttk.Frame(pan); lower=ttk.Frame(pan); pan.add(upper,weight=3); pan.add(lower,weight=2)
		upper.columnconfigure(0,weight=1); upper.rowconfigure(0,weight=1)
		cols=[('stage','阶段',70),('src','源',260),('dst','目标/组',260),('info','信息',200)]
		self.log=ttk.Treeview(upper,columns=[c[0] for c in cols],show='headings',height=12)
		for cid,txt,w in cols: self.log.heading(cid,text=txt); self.log.column(cid,width=w,anchor='w',stretch=True)
		self.log.grid(row=0,column=0,sticky='nsew')
		vsb=ttk.Scrollbar(upper,orient='vertical',command=self.log.yview); vsb.grid(row=0,column=1,sticky='ns')
		hsb=ttk.Scrollbar(upper,orient='horizontal',command=self.log.xview); hsb.grid(row=1,column=0,sticky='we')
		self.log.configure(yscrollcommand=vsb.set,xscrollcommand=hsb.set)
		lower.columnconfigure(0,weight=1); lower.rowconfigure(0,weight=1)
		prev=ttk.LabelFrame(lower,text='预览'); prev.pack(fill='both',expand=True)
		prev.columnconfigure(0,weight=1); prev.rowconfigure(0,weight=1)
		self.preview_label=ttk.Label(prev,text='(选择日志行)'); self.preview_label.grid(row=0,column=0,sticky='nsew',padx=4,pady=4)
		self.preview_info=tk.StringVar(value=''); ttk.Label(prev,textvariable=self.preview_info,foreground='gray').grid(row=1,column=0,sticky='we')
		# 事件
		self.log.bind('<<TreeviewSelect>>', self._on_select_row)
		self.log.bind('<Motion>', self._on_log_motion)
		self.enable_convert.trace_add('write', lambda *a: self._update_states())
		self.enable_rename.trace_add('write', lambda *a: self._update_states())
		self.enable_dedupe.trace_add('write', lambda *a: self._update_states())
		self.dedup_action_var.trace_add('write', lambda *a: self._update_states())
		# tooltips
		tips=[
			(ent_in,'输入目录/文件 (支持常见图片)'),(btn_in,'选择输入目录'),(btn_in_file,'选择单个图片文件'),(cb_rec,'是否递归子目录 (单文件时忽略)'),
			(ent_out,'输出目录 (留空=跟随输入目录或文件所在目录)'),(btn_out,'选择输出目录'),(btn_open_out,'打开输出目录'),
			(cb_dedupe,'勾选执行重复检测'),(cb_convert,'勾选执行格式转换'),(cb_rename,'勾选执行重命名'),
			(sp_workers,'并行线程数'),(btn_start,'真实执行'),(btn_preview,'仅预览不写入'),(btn_cancel,'取消执行'),
			(sp_th,'相似阈值 0：严格 |  >0：近似'),(cb_keep,'重复组保留策略'),(cb_action,'重复文件动作'),
			*( [(self.trash_cb,'仅删除重复时可用 (send2trash)')] if self.trash_cb else [] ),
			(self.move_dir_entry,'重复文件移动目标'),(self.move_dir_btn,'选择移动目录'),
			(cb_fmt,'目标格式'),(sc_q,'拖动调整质量'),(sp_q,'直接输入质量 1-100'),(cb_same,'同格式也重新编码'),(cb_png3,'PNG 高压缩'),
			(ent_pattern,'重命名模式: {name}{ext}{index}{fmt}'),(sp_start,'序号起始'),(sp_step,'序号步长'),(cb_over,'覆盖策略'),(cb_rm_src,'删除源文件(移动而不是复制)')
		]
		for w,t in tips: self._bind_tip(w,t)
		self._update_states()

	# 事件
	def _pick_in(self):
		d=filedialog.askdirectory();
		if d: self.in_var.set(d)
	def _pick_in_file(self):
		f=filedialog.askopenfilename(filetypes=[('图片','*.jpg;*.jpeg;*.png;*.webp;*.gif;*.bmp;*.tiff;*.ico')])
		if f: self.in_var.set(f)
	def _pick_out(self):
		d=filedialog.askdirectory();
		if d: self.out_var.set(d)
	def _pick_move_dir(self):
		d=filedialog.askdirectory();
		if d: self.move_dir_var.set(d)

	def _start(self, dry_run:bool=False):
		if self.worker and self.worker.is_alive():
			messagebox.showinfo('提示','任务运行中'); return
		self.dry_run=dry_run
		inp=self.in_var.get().strip()
		if not inp: self.status_var.set('未选择输入'); return
		if os.path.isdir(inp):
			root_dir=inp
			out_dir=self.out_var.get().strip() or root_dir
			os.makedirs(out_dir,exist_ok=True)
			self._all_files=[p for p in iter_images(root_dir,self.recursive_var.get())]
		elif os.path.isfile(inp):
			# 单文件
			root_dir=os.path.dirname(inp) or os.getcwd()
			out_dir=self.out_var.get().strip() or root_dir
			os.makedirs(out_dir,exist_ok=True)
			self._all_files=[inp]
		else:
			self.status_var.set('输入不存在'); return
		if not self._all_files: self.status_var.set('无图片'); return
		for i in self.log.get_children(): self.log.delete(i)
		self.progress['value']=0; self.progress['maximum']=len(self._all_files)
		self.status_var.set('开始...')
		self.stop_flag.clear(); self.last_out_dir=out_dir
		self.worker=threading.Thread(target=self._pipeline,daemon=True); self.worker.start()

	def _cancel(self):
		self.stop_flag.set(); self.status_var.set('请求取消...')

	def _open_last_out(self):
		path=self.last_out_dir or self.out_var.get().strip()
		if not path:
			self.status_var.set('无输出目录'); return
		if not os.path.isdir(path):
			self.status_var.set('目录不存在'); return
		try:
			if sys.platform.startswith('win'):
				os.startfile(path)  # type: ignore
			elif sys.platform=='darwin':
				subprocess.Popen(['open', path])
			else:
				subprocess.Popen(['xdg-open', path])
			self.status_var.set('已打开输出目录')
		except Exception as e:
			self.status_var.set(f'打开失败:{e}')

	def _preview(self):
		if self.worker and self.worker.is_alive():
			messagebox.showinfo('提示','任务运行中'); return
		self._start(dry_run=True)
		self.status_var.set('预览模式 (不修改文件)')

	# 管线
	def _pipeline(self):
		try:
			kept=self._all_files
			if self.enable_dedupe.get():
				kept=self._dedupe_stage(kept)
				if self.stop_flag.is_set(): return
			if self.enable_convert.get() or self.enable_rename.get():
				self._convert_rename_stage(kept)
			self.q.put('STATUS 预览完成' if self.dry_run else 'STATUS 完成')
		except Exception as e:
			self.q.put(f'STATUS 失败: {e}')
		finally:
			# 执行后重置 dry_run
			self.dry_run=False

	# 去重
	def _dedupe_stage(self, files:List[str])->List[str]:
		th=self.threshold_var.get()
		keep_mode=KEEP_MAP.get(self.keep_var.get(), 'largest')
		action=ACTION_MAP.get(self.dedup_action_var.get(),'list')
		move_dir=self.move_dir_var.get().strip()
		workers=max(1,self.workers_var.get())
		self.q.put(f'STATUS 去重计算哈希 共{len(files)}')
		infos=[]; lock=threading.Lock(); done=0
		def compute(path):
			nonlocal done
			if self.stop_flag.is_set(): return None
			try:
				with Image.open(path) as im:  # type: ignore
					w,h=im.size; ah=ahash(im); dh=dhash(im); st=os.stat(path)
				info=ImgInfo(path,st.st_size,w,h,ah,dh,st.st_mtime)
			except Exception:
				info=None
			with lock:
				done+=1; self.q.put(f'HASH {done} {len(files)}')
			return info
		with ThreadPoolExecutor(max_workers=workers) as ex:
			for fut in as_completed([ex.submit(compute,f) for f in files]):
				r=fut.result();
				if r: infos.append(r)
		if self.stop_flag.is_set(): return []
		groups=[]
		for info in infos:
			placed=False
			for g in groups:
				rep=g[0]
				if th==0:
					if info.ah==rep.ah and info.dh==rep.dh: g.append(info); placed=True; break
				else:
					if hamming(info.ah,rep.ah)+hamming(info.dh,rep.dh)<=th: g.append(info); placed=True; break
			if not placed: groups.append([info])
		dup=[g for g in groups if len(g)>1]
		kept=[]
		for gi,g in enumerate(sorted(dup,key=lambda x:-len(x)),1):
			if keep_mode=='largest': keep=max(g,key=lambda x:x.res)
			elif keep_mode=='largest-file': keep=max(g,key=lambda x:x.size)
			elif keep_mode=='newest': keep=max(g,key=lambda x:x.mtime)
			elif keep_mode=='oldest': keep=min(g,key=lambda x:x.mtime)
			else: keep=g[0]
			kept.append(keep.path)
			for o in (x for x in g if x is not keep):
				act='保留'
				if action=='delete' and not self.stop_flag.is_set():
					if self.dry_run:
						act='删除(预览)'
					else:
						use_trash = bool(self.use_trash.get()) if self.use_trash is not None else False
						ok,msg = safe_delete(o.path,use_trash)
						act=msg if ok else msg
				elif action=='move' and move_dir and not self.stop_flag.is_set():
					if self.dry_run:
						act='移动(预览)'
					else:
						try:
							os.makedirs(move_dir,exist_ok=True)
							target=os.path.join(move_dir,os.path.basename(o.path))
							if os.path.exists(target): target=next_non_conflict(target)
							shutil.move(o.path,target); act='移动'
						except Exception as e: act=f'移失败:{e}'
				self.q.put(f'LOG\tDEDUP\t{o.path}\t{keep.path}\t{act}')
			self.q.put(f'LOG\tDEDUP\t{keep.path}\t组#{gi}\t保留({len(g)})')
		dup_paths={x.path for grp in dup for x in grp}
		for p in files:
			if p not in dup_paths: kept.append(p)
		return kept

	def _convert_rename_stage(self, files:List[str]):
		fmt=FMT_MAP.get(self.fmt_var.get(),'png')
		process_same=self.process_same_var.get(); quality=self.quality_var.get(); png3=self.png3_var.get()
		pattern=self.pattern_var.get(); start=self.start_var.get(); step=self.step_var.get()
		overwrite=OVERWRITE_MAP.get(self.overwrite_var.get(),'overwrite')
		remove_src_on_rename=self.rename_remove_src.get()
		out_dir=self.out_var.get().strip() or self.in_var.get().strip()
		workers=max(1,self.workers_var.get())
		tasks=[]; idx=start
		for f in files:
			src_ext=norm_ext(f)
			tgt_fmt=fmt if self.enable_convert.get() else src_ext
			need_convert=self.enable_convert.get() and (src_ext!=fmt or process_same)
			orig_stem=os.path.splitext(os.path.basename(f))[0]
			default_basename=f"{orig_stem}.{tgt_fmt}"  # 转换后默认文件名
			# 计算最终命名
			name=pattern.replace('{name}',orig_stem)\
						.replace('{ext}',src_ext)\
						.replace('{fmt}',tgt_fmt)
			if '{index}' in name: name=name.replace('{index}',str(idx))
			if '.' not in os.path.basename(name): name+=f'.{tgt_fmt}'
			final_basename=os.path.basename(name)
			final_path=os.path.join(out_dir,final_basename)
			# 冲突处理针对最终文件名
			if os.path.exists(final_path):
				if overwrite=='skip':
					self.q.put(f'LOG\tCONVERT\t{f}\t{final_path}\t跳过(存在)'); idx+=step; continue
				elif overwrite=='rename':
					final_path=next_non_conflict(final_path) if not self.dry_run else final_path+"(预览改名)"
			# 是否需要后续重命名（仅当启用重命名且最终名不同于默认名）
			will_rename = self.enable_rename.get() and (final_basename != default_basename)
			# 中间转换输出路径
			if need_convert:
				convert_basename=default_basename if will_rename else final_basename
				convert_path=os.path.join(out_dir,convert_basename)
				if will_rename:
					# 避免中间名冲突
					if os.path.exists(convert_path) and convert_path!=final_path:
						convert_path=next_non_conflict(convert_path)
			else:
				convert_path=final_path; convert_basename=final_basename
			tasks.append((f,need_convert,tgt_fmt,convert_path,final_path,will_rename,convert_basename,final_basename,idx))
			idx+=step
		total=len(tasks); self.q.put(f'STATUS 转换/重命名 共{total}')
		done=0; lock=threading.Lock()
		def job(spec):
			nonlocal done
			src,need_convert,tgt,convert_path,final_path,will_rename,convert_basename,final_basename,_idx=spec
			if self.stop_flag.is_set(): return
			if not self.dry_run:
				os.makedirs(os.path.dirname(convert_path),exist_ok=True)
			msg_convert=''; ok_convert=True
			if need_convert:
				if self.dry_run:
					ok_convert=True; msg_convert='转换(预览)'
				else:
					ok_convert,msg_convert=convert_one(src,convert_path,tgt,quality if tgt in ('jpg','png','webp') else None,png3 if tgt=='png' else False,None if tgt!='ico' else [16,32,48,64,128,256])
			else:
				# 纯重命名/复制路径
				if self.dry_run:
					if os.path.abspath(src)==os.path.abspath(convert_path):
						ok_convert=True; msg_convert='保持(预览)'
					elif remove_src_on_rename:
						ok_convert=True; msg_convert='移动(预览)'
					else:
						ok_convert=True; msg_convert='复制(预览)'
				else:
					try:
						if os.path.abspath(src)==os.path.abspath(convert_path):
							ok_convert=True; msg_convert='保持'
						elif remove_src_on_rename:
							shutil.move(src,convert_path); ok_convert=True; msg_convert='移动'
						else:
							shutil.copy2(src,convert_path); ok_convert=True; msg_convert='复制'
					except Exception as e:
						ok_convert=False; msg_convert=f'复制失败:{e}'
			# 如果需要重命名(第二阶段)
			msg_rename=''; ok_rename=True
			if will_rename:
				if self.dry_run:
					ok_rename=True; msg_rename='命名(预览)'
				else:
					try:
						os.replace(convert_path,final_path)
						ok_rename=True; msg_rename='命名'
					except Exception as e:
						ok_rename=False; msg_rename=f'命名失败:{e}'
			with lock:
				done+=1
				# 记录转换/复制阶段
				stage1='CONVERT' if need_convert else 'RENAME'
				self.q.put(f'LOG\t{stage1}\t{src}\t{convert_path}\t{msg_convert if ok_convert else "失败:"+msg_convert}')
				# 记录真正重命名阶段
				if will_rename:
					self.q.put(f'LOG\tRENAME\t{convert_path}\t{final_path}\t{msg_rename if ok_rename else "失败:"+msg_rename}')
				self.q.put(f'PROG {done} {total}')
		if workers>1:
			with ThreadPoolExecutor(max_workers=workers) as ex:
				futs=[ex.submit(job,t) for t in tasks]
				for _ in as_completed(futs):
					if self.stop_flag.is_set(): break
		else:
			for t in tasks: job(t)

	# 队列 + 预览
	def _drain(self):
		try:
			while True:
				m=self.q.get_nowait()
				if m.startswith('HASH '):
					_,d,total=m.split(); d=int(d); total=int(total)
					self.progress['maximum']=total; self.progress['value']=d
					pct=int(d/total*100) if total else 0
					self.status_var.set(f'去重哈希 {pct}% ({d}/{total})')
				elif m.startswith('PROG '):
					_,d,total=m.split(); d=int(d); total=int(total)
					self.progress['maximum']=total; self.progress['value']=d
					pct=int(d/total*100) if total else 0
					self.status_var.set(f'处理 {pct}% ({d}/{total})')
				elif m.startswith('STATUS '):
					self.status_var.set(m[7:])
				elif m.startswith('LOG\t'):
					try:
						_tag,stage,src,dst,info=m.split('\t',4)
						stage_disp=STAGE_MAP_DISPLAY.get(stage,stage)
						self.log.insert('', 'end', values=(stage_disp, os.path.basename(src), os.path.basename(dst), info), tags=(src,))
					except Exception:
						pass
		except queue.Empty:
			pass
		finally:
			self.root.after(150,self._drain)

	def _on_select_row(self,_=None):
		sel=self.log.selection();
		if not sel: return
		path=self.log.item(sel[0],'tags')[0]
		if not os.path.exists(path):
			self.preview_label.configure(text='文件不存在',image=''); return
		try:
			with Image.open(path) as im:  # type: ignore
				w,h=im.size; max_side=420; scale=min(max_side/w,max_side/h,1)
				if scale<1: im=im.resize((int(w*scale),int(h*scale)))
				photo=ImageTk.PhotoImage(im)
			self.preview_label.configure(image=photo,text=''); self._preview_ref=photo
			self.preview_info.set(f'{w}x{h} {os.path.basename(path)}')
		except Exception as e:
			self.preview_label.configure(text=f'预览失败:{e}',image='')

	def _update_states(self):
		# 去重区
		if hasattr(self,'frame_dedupe') and self.frame_dedupe:
			dedupe_enabled = self.enable_dedupe.get()
			for ch in self.frame_dedupe.winfo_children():
				try:
					if ch in (self.move_dir_entry,self.move_dir_btn):
						# 先统一灰化，后面再根据动作单独处理
						pass
					ch.configure(state='normal' if dedupe_enabled else 'disabled')
				except Exception: pass
		if self.frame_convert:
			enabled = self.enable_convert.get()
			for ch in self.frame_convert.winfo_children():
				try:
					if ch.winfo_class() == 'TCombobox':
						ch.configure(state='readonly' if enabled else 'disabled')
					else:
						ch.configure(state='normal' if enabled else 'disabled')
				except Exception:
					pass
		if self.frame_rename:
			enabled = self.enable_rename.get()
			for ch in self.frame_rename.winfo_children():
				try:
					if ch.winfo_class() == 'TCombobox':
						ch.configure(state='readonly' if enabled else 'disabled')
					else:
						ch.configure(state='normal' if enabled else 'disabled')
				except Exception:
					pass
		need_move=ACTION_MAP.get(self.dedup_action_var.get(),'list')=='move' and self.enable_dedupe.get()
		need_delete=ACTION_MAP.get(self.dedup_action_var.get(),'list')=='delete'
		mv_st='normal' if need_move else 'disabled'
		if self.move_dir_entry: \
			self.move_dir_entry.configure(state=mv_st)
		if self.move_dir_btn: \
			self.move_dir_btn.configure(state=mv_st)
		# 回收站复选框 (仅删除时可用)
		if self.trash_cb is not None:
			if send2trash is None:
				self.trash_cb.state(['disabled'])
			else:
				if need_delete:
					self.trash_cb.state(['!disabled'])
				else:
					self.trash_cb.state(['disabled'])

	# Tooltips
	def _show_tooltip(self,text,x,y):
		self._hide_tooltip()
		tw=tk.Toplevel(self.root); tw.wm_overrideredirect(True); tw.attributes('-topmost',True)
		lab=tk.Label(tw,text=text,background='#FFFFE0',relief='solid',borderwidth=1,justify='left'); lab.pack(ipadx=4,ipady=2)
		tw.wm_geometry(f"+{x+15}+{y+15}"); self._tooltip=tw
	def _hide_tooltip(self):
		if self._tooltip:
			try: self._tooltip.destroy()
			except Exception: pass
		self._tooltip=None
	def _bind_tip(self,widget,text):
		def enter(_e):
			if self._tooltip_after:
				try: self.root.after_cancel(self._tooltip_after)
				except Exception: pass
			self._tooltip_after=self.root.after(450, lambda: self._show_tooltip(text,self.root.winfo_pointerx(),self.root.winfo_pointery()))
		def leave(_e):
			if self._tooltip_after:
				try: self.root.after_cancel(self._tooltip_after)
				except Exception: pass
				self._tooltip_after=None
			self._hide_tooltip()
		widget.bind('<Enter>',enter,add='+'); widget.bind('<Leave>',leave,add='+'); widget.bind('<ButtonPress>',leave,add='+')
	def _on_log_motion(self,event):
		if self._tooltip_after:
			self.root.after_cancel(self._tooltip_after); self._tooltip_after=None
		iid=self.log.identify_row(event.y); col=self.log.identify_column(event.x)
		if not iid or col not in ('#2','#3'):
			self._hide_tooltip(); return
		tags=self.log.item(iid,'tags');
		if not tags: return
		full=tags[0]
		self._tooltip_after=self.root.after(500, lambda p=full,x=self.root.winfo_pointerx(),y=self.root.winfo_pointery(): self._show_tooltip(p,x,y))
		self.root.bind('<Leave>',lambda e: self._hide_tooltip(),add='+')

# 启动
def launch():
	if tk is None or Image is None:
		print('缺少 Tkinter 或 Pillow'); return 2
	root=tk.Tk(); ImageToolApp(root); root.mainloop(); return 0

if __name__=='__main__':
	launch()
