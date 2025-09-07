"""图片工具
"""
from __future__ import annotations
import os, sys, threading, queue, shutil, subprocess, re, hashlib, time
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
	ImageFile=None  # type: ignore

# Windows 回收站支持 (可选)
try:
	from send2trash import send2trash  # type: ignore
except Exception:  # pragma: no cover
	send2trash=None  # type: ignore

if ImageFile:
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
	'CLASSIFY':'分类',
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

def safe_delete(path:str):
	"""删除文件: 若可用 send2trash 则发送系统回收站/废纸篓, 否则直接删除."""
	if send2trash is not None:
		try:
			send2trash(path)
			return True,'删除->回收站'
		except Exception as e:
			# 回退到直接删除
			try:
				os.remove(path)
				return True,f'删除(回收站失败:{e})'
			except Exception as e2:
				return False,f'删失败:{e2}'
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

def _fmt_size(n:int)->str:
	"""人类可读文件大小"""
	units=['B','KB','MB','GB','TB']
	f=float(n); i=0
	while f>=1024 and i<len(units)-1:
		f/=1024; i+=1
	return (f'{f:.2f}{units[i]}' if i>0 else f'{int(f)}{units[i]}')

def convert_one(src,dst,fmt,quality=None,png3=False,ico_sizes=None,square_mode=None):
	try:
		with Image.open(src) as im:  # type: ignore
			if fmt=='ico':
				w,h=im.size
				if w!=h and square_mode and square_mode!='keep':
					if square_mode=='center':
						side=min(w,h); left=(w-side)//2; top=(h-side)//2
						im=im.crop((left,top,left+side,top+side))
					elif square_mode=='topleft':
						side=min(w,h); im=im.crop((0,0,side,side))
					elif square_mode=='fit':  # 填充
						side=max(w,h)
						canvas=Image.new('RGBA',(side,side),(0,0,0,0))
						x=(side-w)//2; y=(side-h)//2
						canvas.paste(im,(x,y))
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
		import traceback
		# 返回详细的错误信息，包含异常类型和堆栈
		error_detail = f"{type(e).__name__}: {str(e)}"
		# 添加关键的堆栈信息（最后几行）
		tb_lines = traceback.format_exc().strip().split('\n')
		if len(tb_lines) > 2:
			# 取最后的错误行
			error_detail += f" | {tb_lines[-2].strip()}"
		return False, error_detail

@dataclass
class ImgInfo:
	path:str; size:int; w:int; h:int; ah:int; dh:int; mtime:float
	@property
	def res(self): return self.w*self.h

class PreviewThread(threading.Thread):
	"""独立的预览处理线程"""
	def __init__(self, app):
		super().__init__(daemon=True)
		self.app = app
		self.preview_queue = queue.Queue()
		self.stop_flag = threading.Event()
		
	def run(self):
		"""预览线程主循环"""
		while not self.stop_flag.is_set():
			try:
				task = self.preview_queue.get(timeout=1.0)
				if task is None:  # 停止信号
					break
				self._process_preview_task(task)
			except queue.Empty:
				continue
			except Exception as e:
				print(f"Preview thread error: {e}")
	
	def add_preview_task(self, src_path, result_path=None):
		"""添加预览任务"""
		if not self.stop_flag.is_set():
			self.preview_queue.put((src_path, result_path))
	
	def _process_preview_task(self, task):
		"""处理单个预览任务"""
		src_path, result_path = task
		try:
			# 在后台线程中准备图片数据
			src_data = self._prepare_image_data(src_path) if src_path else None
			result_data = self._prepare_image_data(result_path) if result_path else None
			
			# 通过队列发送到主线程更新UI
			self.app.root.after_idle(lambda: self.app._update_preview_ui(src_data, result_data))
		except Exception as e:
			print(f"Preview processing error: {e}")
			self.app.root.after_idle(lambda: self.app._show_preview_error(str(src_path), str(e)))
	
	def _prepare_image_data(self, path):
		"""在后台线程中准备图片数据"""
		if not path or not os.path.exists(path):
			return None
		
		try:
			is_animated = self.app.is_animated_image(path)
			
			with Image.open(path) as im:
				w, h = im.size
				max_side = 320
				scale = min(max_side/w, max_side/h, 1)
				
				if is_animated:
					# 处理动图
					frames = []
					try:
						for frame in ImageSequence.Iterator(im):
							frame = frame.copy()
							if scale < 1:
								frame = frame.resize((int(w*scale), int(h*scale)), Image.Resampling.LANCZOS)
							frames.append(frame)
					except Exception:
						# 如果动画加载失败，显示第一帧
						if scale < 1:
							im = im.resize((int(w*scale), int(h*scale)))
						frames = [im.copy()]
					
					return {
						'type': 'animated',
						'frames': frames,
						'path': path,
						'size': os.path.getsize(path)
					}
				else:
					# 处理静态图片
					if scale < 1:
						im = im.resize((int(w*scale), int(h*scale)))
					
					return {
						'type': 'static',
						'image': im.copy(),
						'path': path,
						'size': os.path.getsize(path)
					}
		except Exception as e:
			return {
				'type': 'error',
				'path': path,
				'error': str(e)
			}
	
	def stop(self):
		"""停止预览线程"""
		self.stop_flag.set()
		self.preview_queue.put(None)  # 发送停止信号

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
		self.single_file_mode=False
		self._ratio_map={}  # 路径 -> 比例标签
		# 回收站逻辑自动化 (send2trash 可用即使用)
		self.trash_cb=None  # 兼容旧引用
		self.last_out_dir=None
		self.cache_dir=None  # 预览缓存文件夹
		
		# 初始化预览线程
		self.preview_thread = PreviewThread(self)
		self.preview_thread.start()
		self._build()
		self.root.after(200,self._drain)
		# 退出时清理缓存
		self.root.protocol("WM_DELETE_WINDOW", self._on_close)
		self.cache_trash_dir=None  # 预览模拟回收站目录
		self.cache_final_dir=None  # 预览最终结果目录 (_final)
		self.processed_source_files = set()  # 记录已成功处理的源文件路径
		self.cache_to_original_map = {}  # 缓存文件到原始文件的映射
		self._last_preview_signature=None
		self._last_preview_files=None  # list of (path,mtime,size)

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
		self.recursive_var=tk.BooleanVar(value=False); cb_rec=ttk.Checkbutton(io,text='递归',variable=self.recursive_var); cb_rec.grid(row=0,column=4,sticky='w')
		ttk.Label(io,text='输出:').grid(row=0,column=5,sticky='e')
		self.out_var=tk.StringVar(); ent_out=ttk.Entry(io,textvariable=self.out_var,width=32); ent_out.grid(row=0,column=6,sticky='we',padx=3)
		self.out_var.trace_add('write', self._on_out_dir_change)
		btn_out=ttk.Button(io,text='选择',command=self._pick_out,width=6); btn_out.grid(row=0,column=7,padx=(2,0))
		btn_open_out=ttk.Button(io,text='打开',command=self._open_last_out,width=6); btn_open_out.grid(row=0,column=8,padx=(4,0))
		# 功能
		opts=ttk.Frame(outer); opts.pack(fill='x',pady=(0,8))
		self.enable_dedupe=tk.BooleanVar(value=False)
		self.enable_convert=tk.BooleanVar(value=False)
		self.enable_rename=tk.BooleanVar(value=False)
		# 比例分类配置 (新独立区域)
		self.classify_ratio_var=tk.BooleanVar(value=False)
		# 比例分类默认容差 15%
		self.ratio_tol_var=tk.DoubleVar(value=0.15)
		# 保留常用预设: 16:9,3:2,4:3,1:1,21:9
		self.ratio_custom_var=tk.StringVar(value='16:9,3:2,4:3,1:1,21:9')
		self.ratio_snap_var=tk.BooleanVar(value=False)  # 不匹配是否取最近
		cb_classify=ttk.Checkbutton(opts,text='分类',variable=self.classify_ratio_var); cb_classify.pack(side='left',padx=2)
		cb_convert=ttk.Checkbutton(opts,text='转换',variable=self.enable_convert); cb_convert.pack(side='left',padx=2)
		cb_dedupe=ttk.Checkbutton(opts,text='去重',variable=self.enable_dedupe); cb_dedupe.pack(side='left',padx=2)
		cb_rename=ttk.Checkbutton(opts,text='重命名',variable=self.enable_rename); cb_rename.pack(side='left',padx=2)
		ttk.Label(opts,text='线程').pack(side='left',padx=(12,2))
		# 默认线程数 16
		self.workers_var=tk.IntVar(value=16)
		sp_workers=ttk.Spinbox(opts,from_=1,to=64,textvariable=self.workers_var,width=5); sp_workers.pack(side='left')
		# 统一的删源选项
		self.global_remove_src=tk.BooleanVar(value=False)
		cb_global_rm_src=ttk.Checkbutton(opts,text='删源',variable=self.global_remove_src); cb_global_rm_src.pack(side='left',padx=(8,0))
		btn_start=ttk.Button(opts,text='开始',command=self._start,width=8); btn_start.pack(side='right',padx=2)
		btn_preview=ttk.Button(opts,text='预览',command=self._preview,width=8); btn_preview.pack(side='right',padx=2)
		btn_cancel=ttk.Button(opts,text='取消',command=self._cancel,width=8); btn_cancel.pack(side='right',padx=2)
		# 分类 (第一阶段)
		ttk.Separator(outer,orient='horizontal').pack(fill='x',pady=(0,4))
		clsf=ttk.LabelFrame(outer,text='比例分类'); clsf.pack(fill='x',pady=(0,10))
		self.frame_ratio=clsf
		clsf.columnconfigure(5,weight=1)
		# 分类内部控件（除启用复选框外可整体禁用）
		self.cb_ratio_inner_snap=ttk.Checkbutton(clsf,text='不匹配吸附最近',variable=self.ratio_snap_var)
		cb_tol_label=ttk.Label(clsf,text='容差')
		sp_rt=ttk.Spinbox(clsf,from_=0.0,to=0.2,increment=0.005,format='%.3f',width=6,textvariable=self.ratio_tol_var)
		btn_reset_ratio=ttk.Button(clsf,text='恢复默认',width=10,command=lambda: self.ratio_custom_var.set('16:9,3:2,4:3,1:1,21:9'))
		lbl_ratio_input=ttk.Label(clsf,text='自定义(16:9 16x10 ...)')
		ent_ratio=ttk.Entry(clsf,textvariable=self.ratio_custom_var,width=58)
		# 保存引用供后续 tooltip / 状态控制
		self._ratio_sp_rt=sp_rt; self._ratio_ent=ent_ratio; self._ratio_btn_reset=btn_reset_ratio; self._ratio_snap=self.cb_ratio_inner_snap; self._ratio_lbl_input=lbl_ratio_input; self._ratio_lbl_tol=cb_tol_label
		# 布局
		cb_tol_label.grid(row=0,column=0,sticky='e')
		sp_rt.grid(row=0,column=1,sticky='w',padx=(4,12))
		self.cb_ratio_inner_snap.grid(row=0,column=2,sticky='w')
		btn_reset_ratio.grid(row=0,column=3,sticky='w',padx=(12,0))
		lbl_ratio_input.grid(row=1,column=0,sticky='e',pady=(4,0))
		ent_ratio.grid(row=1,column=1,columnspan=4,sticky='we',pady=(4,2))
		# 预设比例按钮行
		preset_frame=ttk.Frame(clsf)
		preset_frame.grid(row=2,column=0,columnspan=5,sticky='w',pady=(2,2))
		presets=['16:9','16:10','4:3','3:2','5:4','21:9','1:1']
		def _toggle_ratio(val:str):
			cur=self.ratio_custom_var.get().replace('；',';').replace('，',',').replace(';',',')
			parts=[p.strip() for p in cur.split(',') if p.strip()]
			lower_map={p.lower():p for p in parts}
			key=val.lower()
			if key in lower_map:
				# 移除
				parts=[p for p in parts if p.lower()!=key]
			else:
				parts.append(val)
			self.ratio_custom_var.set(','.join(parts))
		self._ratio_preset_buttons=[]
		for r in presets:
			btn=ttk.Button(preset_frame,text=r,width=6,command=lambda v=r: _toggle_ratio(v))
			btn.pack(side='left',padx=1)
			self._ratio_preset_buttons.append(btn)
		btn_clear=ttk.Button(preset_frame,text='清空',width=6,command=lambda: self.ratio_custom_var.set(''))
		btn_clear.pack(side='left',padx=(8,0))
		self._ratio_btn_clear=btn_clear
		# 转换 (第二阶段)
		ttk.Separator(outer,orient='horizontal').pack(fill='x',pady=(0,4))
		convert=ttk.LabelFrame(outer,text='格式转换'); convert.pack(fill='x',pady=(0,10))
		self.frame_convert=convert
		self.fmt_var=tk.StringVar(value=_rev_map(FMT_MAP)['webp'])
		self.quality_var=tk.IntVar(value=100)
		self.process_same_var=tk.BooleanVar(value=False)
		self.png3_var=tk.BooleanVar(value=False)
		# 默认转换后删源
		self.ico_sizes_var=tk.StringVar(value='')  # 自定义尺寸输入
		self.ico_keep_orig=tk.BooleanVar(value=False)
		self.ico_size_vars={s:tk.BooleanVar(value=(s in (16,32,48,64))) for s in (16,32,48,64,128,256)}
		self.ico_square_mode=tk.StringVar(value='fit')  # keep|center|topleft|fit
		ttk.Label(convert,text='格式').grid(row=0,column=0,sticky='e')
		cb_fmt=ttk.Combobox(convert,textvariable=self.fmt_var,values=list(FMT_MAP.keys()),width=12,state='readonly'); cb_fmt.grid(row=0,column=1,sticky='w',padx=(0,12))
		ttk.Label(convert,text='质量').grid(row=0,column=2,sticky='e')
		sc_q=ttk.Scale(convert,from_=1,to=100,orient='horizontal',variable=self.quality_var,length=220); sc_q.grid(row=0,column=3,sticky='we',padx=(2,6))
		sp_q=ttk.Spinbox(convert,from_=1,to=100,textvariable=self.quality_var,width=5); sp_q.grid(row=0,column=4,sticky='w',padx=(0,8))
		cb_same=ttk.Checkbutton(convert,text='同格式也重存',variable=self.process_same_var); cb_same.grid(row=0,column=5,sticky='w')
		cb_png3=ttk.Checkbutton(convert,text='PNG3压缩',variable=self.png3_var); cb_png3.grid(row=0,column=6,sticky='w')
		# ICO 尺寸输入 (仅当选择 ico 有效)
		lbl_ico=ttk.Label(convert,text='ICO尺寸')
		ent_ico=ttk.Entry(convert,textvariable=self.ico_sizes_var,width=22)
		lbl_ico.grid(row=1,column=0,sticky='e',pady=(4,0))
		ent_ico.grid(row=1,column=1,sticky='w',pady=(4,0))
		# 复选框区域
		ico_box=ttk.Frame(convert)
		ico_box.grid(row=1,column=2,columnspan=5,sticky='w',pady=(4,0))
		# 非方图处理方式
		frame_sq=ttk.Frame(convert)
		frame_sq.grid(row=2,column=0,columnspan=8,sticky='w',pady=(2,2))
		ttk.Label(frame_sq,text='非方图:').pack(side='left')
		sq_choices=[('保持','keep'),('中心裁切','center'),('左上裁切','topleft'),('等比例填充','fit')]
		for txt,val in sq_choices:
			ttk.Radiobutton(frame_sq,text=txt,variable=self.ico_square_mode,value=val).pack(side='left',padx=(4,0))
		self.ico_square_warn=tk.StringVar(value='')
		lbl_warn=ttk.Label(frame_sq,textvariable=self.ico_square_warn,foreground='orange')
		lbl_warn.pack(side='left',padx=(10,0))
		# 保存引用以便根据是否为 ICO 格式启用/禁用
		self.frame_sq=frame_sq
		self.ico_checks=[]
		for i,s in enumerate((16,32,48,64,128,256)):
			cb=ttk.Checkbutton(ico_box,text=str(s),variable=self.ico_size_vars[s])
			cb.grid(row=0,column=i,sticky='w')
			self.ico_checks.append(cb)
		cb_keep=ttk.Checkbutton(ico_box,text='不改变',variable=self.ico_keep_orig)
		cb_keep.grid(row=0,column=6,sticky='w',padx=(6,0))
		self.ico_keep_cb=cb_keep; self.ico_custom_entry=ent_ico; self.ico_label=lbl_ico
		# 去重 (第三阶段)
		ttk.Separator(outer,orient='horizontal').pack(fill='x',pady=(0,4))
		dedupe=ttk.LabelFrame(outer,text='去重设置'); dedupe.pack(fill='x',pady=(0,10))
		self.frame_dedupe=dedupe
		# 去重阈值默认 3
		self.threshold_var=tk.IntVar(value=3)
		self.keep_var=tk.StringVar(value=_rev_map(KEEP_MAP)['largest'])
		# 去重动作默认 删除重复
		self.dedup_action_var=tk.StringVar(value=_rev_map(ACTION_MAP)['delete'])
		self.move_dir_var=tk.StringVar()
		for i in range(11): dedupe.columnconfigure(i,weight=0)
		ttk.Label(dedupe,text='阈值').grid(row=0,column=0,sticky='e')
		sp_th=ttk.Spinbox(dedupe,from_=0,to=32,textvariable=self.threshold_var,width=5); sp_th.grid(row=0,column=1,sticky='w',padx=(0,8))
		ttk.Label(dedupe,text='保留').grid(row=0,column=2,sticky='e')
		cb_keep=ttk.Combobox(dedupe,textvariable=self.keep_var,values=list(KEEP_MAP.keys()),width=12,state='readonly'); cb_keep.grid(row=0,column=3,sticky='w',padx=(0,8))
		ttk.Label(dedupe,text='动作').grid(row=0,column=4,sticky='e')
		cb_action=ttk.Combobox(dedupe,textvariable=self.dedup_action_var,values=list(ACTION_MAP.keys()),width=10,state='readonly'); cb_action.grid(row=0,column=5,sticky='w',padx=(0,8))
		col_mv=6
		ttk.Label(dedupe,text='移动到').grid(row=0,column=col_mv,sticky='e')
		self.move_dir_entry=ttk.Entry(dedupe,textvariable=self.move_dir_var,width=24); self.move_dir_entry.grid(row=0,column=col_mv+1,sticky='w')
		self.move_dir_btn=ttk.Button(dedupe,text='选',command=self._pick_move_dir,width=4); self.move_dir_btn.grid(row=0,column=col_mv+2,sticky='w',padx=(4,0))
		convert.columnconfigure(3,weight=1)
		for i in range(8):
			if i!=3: convert.columnconfigure(i,weight=0)
		# 重命名
		ttk.Separator(outer,orient='horizontal').pack(fill='x',pady=(0,4))
		rename=ttk.LabelFrame(outer,text='重命名'); rename.pack(fill='x',pady=(0,10))
		self.frame_rename=rename
		self.pattern_var=tk.StringVar(value='{name}_{index}.{fmt}')
		self.start_var=tk.IntVar(value=1)
		self.step_var=tk.IntVar(value=1)
		# 默认序号宽度 3
		self.index_width_var=tk.IntVar(value=3)  # 0=不补零
		self.overwrite_var=tk.StringVar(value=_rev_map(OVERWRITE_MAP)['overwrite'])
		ttk.Label(rename,text='模式').grid(row=0,column=0,sticky='e')
		ent_pattern=ttk.Entry(rename,textvariable=self.pattern_var,width=42); ent_pattern.grid(row=0,column=1,sticky='w',padx=(0,8))
		ttk.Label(rename,text='起始').grid(row=0,column=2,sticky='e')
		sp_start=ttk.Spinbox(rename,from_=1,to=999999,textvariable=self.start_var,width=7); sp_start.grid(row=0,column=3,sticky='w')
		ttk.Label(rename,text='步长').grid(row=0,column=4,sticky='e')
		sp_step=ttk.Spinbox(rename,from_=1,to=9999,textvariable=self.step_var,width=5); sp_step.grid(row=0,column=5,sticky='w')
		ttk.Label(rename,text='宽度').grid(row=0,column=6,sticky='e')
		sp_indexw=ttk.Spinbox(rename,from_=0,to=10,textvariable=self.index_width_var,width=5); sp_indexw.grid(row=0,column=7,sticky='w')
		ttk.Label(rename,text='覆盖策略').grid(row=0,column=8,sticky='e')
		cb_over=ttk.Combobox(rename,textvariable=self.overwrite_var,values=list(OVERWRITE_MAP.keys()),width=12,state='readonly'); cb_over.grid(row=0,column=9,sticky='w')
		for i in range(10): rename.columnconfigure(i,weight=0)
		# 进度
		ttk.Separator(outer,orient='horizontal').pack(fill='x',pady=(0,6))
		self.progress=ttk.Progressbar(outer,maximum=100); self.progress.pack(fill='x',pady=(0,4))
		self.status_var=tk.StringVar(value='就绪'); ttk.Label(outer,textvariable=self.status_var,foreground='blue').pack(fill='x')
		# 日志
		ttk.Separator(outer,orient='horizontal').pack(fill='x',pady=(4,4))
		# 日志筛选工具条
		filter_bar=ttk.Frame(outer); filter_bar.pack(fill='x',pady=(0,2))
		self.log_filter_stage=tk.StringVar(value='全部')
		self.log_filter_kw=tk.StringVar()
		self.log_filter_fail=tk.BooleanVar(value=False)
		ttk.Label(filter_bar,text='筛选:').pack(side='left')
		cb_stage=ttk.Combobox(filter_bar,width=8,state='readonly',textvariable=self.log_filter_stage,
			values=['全部','去重','转换','重命名','删除','移动','保留','信息'])
		cb_stage.pack(side='left',padx=(2,4))
		ent_kw=ttk.Entry(filter_bar,width=18,textvariable=self.log_filter_kw)
		ent_kw.pack(side='left');
		cb_fail=ttk.Checkbutton(filter_bar,text='仅失败',variable=self.log_filter_fail)
		cb_fail.pack(side='left',padx=(6,0))
		btn_reset=ttk.Button(filter_bar,text='重置',width=6,command=lambda: self._reset_log_filter())
		btn_reset.pack(side='right',padx=(4,0))
		btn_open_log=ttk.Button(filter_bar,text='打开日志',width=8,command=self._open_program_log)
		btn_open_log.pack(side='right',padx=(4,0))
		# 绑定变更实时刷新
		self.log_filter_stage.trace_add('write', self._on_change_log_filter)
		self.log_filter_kw.trace_add('write', self._on_change_log_filter)
		self.log_filter_fail.trace_add('write', self._on_change_log_filter)
		pan=ttk.PanedWindow(outer,orient='vertical'); pan.pack(fill='both',expand=True)
		self.paned=pan  # 保存引用用于自动调整
		upper=ttk.Frame(pan); lower=ttk.Frame(pan)
		self.upper_frame=upper; self.lower_frame=lower
		pan.add(upper,weight=0)
		pan.add(lower,weight=1)
		upper.columnconfigure(0,weight=1); upper.rowconfigure(0,weight=1)
		cols=[('stage','阶段',70),('src','源',260),('dst','目标/组',260),('info','信息',200)]
		self.log=ttk.Treeview(upper,columns=[c[0] for c in cols],show='headings',height=12)
		for cid,txt,w in cols: self.log.heading(cid,text=txt); self.log.column(cid,width=w,anchor='w',stretch=True)
		self.log.grid(row=0,column=0,sticky='nsew')
		# 阶段着色 (使用 tag 样式)
		style=ttk.Style(self.root)
		# 尝试设置浅色背景，兼容浅/深色主题用户可自行调整
		self.log.tag_configure('STAGE_DEDUPE', background='#FFF5E6')      # 淡橙 去重
		self.log.tag_configure('STAGE_CONVERT', background='#E6F5FF')     # 淡蓝 转换
		self.log.tag_configure('STAGE_RENAME', background='#F0E6FF')      # 淡紫 重命名
		self.log.tag_configure('STAGE_CLASSIFY', background='#E6FFE6')    # 淡绿 分类
		self.log.tag_configure('STAGE_DELETE', background='#FFE6E6')      # 淡红 删除
		self.log.tag_configure('STAGE_MOVE', background='#E6FFE6')        # 淡绿 移动
		self.log.tag_configure('STAGE_KEEP', background='#F5F5F5')        # 灰白 保留
		self.log.tag_configure('STAGE_INFO', background='#EEEEEE')        # 信息行
		vsb=ttk.Scrollbar(upper,orient='vertical',command=self.log.yview); vsb.grid(row=0,column=1,sticky='ns')
		hsb=ttk.Scrollbar(upper,orient='horizontal',command=self.log.xview); hsb.grid(row=1,column=0,sticky='we')
		self.log.configure(yscrollcommand=vsb.set,xscrollcommand=hsb.set)
		lower.columnconfigure(0,weight=1); lower.rowconfigure(0,weight=1)
		prev=ttk.LabelFrame(lower,text='预览 (前后对比)'); prev.pack(fill='both',expand=True)
		for i in range(2): prev.columnconfigure(i,weight=1)
		prev.rowconfigure(0,weight=1)
		# BEFORE
		before_frame=ttk.Frame(prev,padding=2); before_frame.grid(row=0,column=0,sticky='nsew')
		before_frame.columnconfigure(0,weight=1)
		before_frame.rowconfigure(0,weight=1)
		self.preview_before_label=ttk.Label(before_frame,text='(源)'); self.preview_before_label.grid(row=0,column=0,sticky='n')
		self.preview_before_info=tk.StringVar(value=''); ttk.Label(before_frame,textvariable=self.preview_before_info,foreground='gray').grid(row=1,column=0,sticky='we')
		# AFTER
		after_frame=ttk.Frame(prev,padding=2); after_frame.grid(row=0,column=1,sticky='nsew')
		after_frame.columnconfigure(0,weight=1)
		after_frame.rowconfigure(0,weight=1)
		self.preview_after_label=ttk.Label(after_frame,text='(结果)'); self.preview_after_label.grid(row=0,column=0,sticky='n')
		self.preview_after_info=tk.StringVar(value=''); ttk.Label(after_frame,textvariable=self.preview_after_info,foreground='gray').grid(row=1,column=0,sticky='we')
		# 兼容旧属性引用
		self.preview_label=self.preview_after_label
		self.preview_info=self.preview_after_info
		# 自动调整窗口大小选项
		self.auto_resize_window=tk.BooleanVar(value=True)
		cb_auto=ttk.Checkbutton(prev,text='随图自调',variable=self.auto_resize_window)
		cb_auto.grid(row=2,column=0,columnspan=2,sticky='w',pady=(2,0))  # 跨越两列以保持对称
		self._last_auto_size=None
		self.auto_resize_window.trace_add('write', lambda *a: self._maybe_resize_window())
		# 事件
		self.log.bind('<<TreeviewSelect>>', self._on_select_row)
		self.log.bind('<Motion>', self._on_log_motion)
		self.enable_convert.trace_add('write', lambda *a: self._update_states())
		self.enable_rename.trace_add('write', lambda *a: self._update_states())
		self.enable_dedupe.trace_add('write', lambda *a: self._update_states())
		self.classify_ratio_var.trace_add('write', lambda *a: self._update_states())
		self.dedup_action_var.trace_add('write', lambda *a: self._update_states())
		self.fmt_var.trace_add('write', lambda *a: self._update_states())
		# tooltips
		tips=[
			(ent_in,'输入目录/文件 (支持常见图片)'),(btn_in,'选择输入目录'),(btn_in_file,'选择单个图片文件'),(cb_rec,'是否递归子目录 (单文件时忽略)'),
			(ent_out,'输出目录 (留空=跟随输入目录或文件所在目录)'),(btn_out,'选择输出目录'),(btn_open_out,'打开输出目录'),
			(cb_dedupe,'勾选执行重复检测'),(cb_convert,'勾选执行格式转换'),(cb_rename,'勾选执行重命名'),
			(sp_workers,'并行线程数'),(btn_start,'真实执行'),(btn_preview,'仅预览不写入'),(btn_cancel,'取消执行'),
			(sp_th,'相似阈值 0：严格 |  >0：近似'),(cb_keep,'重复组保留策略'),(cb_action,'重复文件动作'),
			# 回收站提示已移除，自动处理
			(self.move_dir_entry,'重复文件移动目标'),(self.move_dir_btn,'选择移动目录'),
			(cb_fmt,'目标格式'),(sc_q,'拖动调整质量'),(sp_q,'直接输入质量 1-100'),(cb_same,'同格式也重新编码'),(cb_png3,'PNG 高压缩'),
			(ent_ico,'ICO 自定义尺寸: 逗号/空格分隔 例如 24,40'),
		]
		# 补充 ico 勾选尺寸 tips
		for c in self.ico_checks:
			tips.append((c,'勾选加入该尺寸'))
		# 继续追加其余
		more_tips=[
			(self.ico_keep_cb,'仅输出原图尺寸 (忽略其它选择)'),
			(frame_sq,'非方图处理策略 (仅 ICO 格式时有效)'),
			(ent_pattern,'重命名模式: {name}{ext}{index}{fmt} 支持 {index:03} 指定宽度'),(sp_start,'序号起始'),(sp_step,'序号步长'),(sp_indexw,'序号零填充宽度 (0=不填)'),(cb_over,'覆盖策略')
		]
		# 比例分类提示 (新区域)
		if hasattr(self,'frame_ratio'):
			more_tips.append((self.frame_ratio,'按常见比例创建子目录或占位符 {ratio}; 自定义: 16:9,4:3 ...; 吸附=选最近比值'))
			if hasattr(self,'_ratio_snap'): more_tips.append((self._ratio_snap,'未命中容差时是否取最近比值标签'))
			if hasattr(self,'_ratio_sp_rt'): more_tips.append((self._ratio_sp_rt,'相对误差容差, 默认 0.15=±15%'))
			if hasattr(self,'_ratio_ent'): more_tips.append((self._ratio_ent,'自定义列表, 支持 16:9 / 16x9 形式'))
			# 顶部启用按钮为 cb_classify (在 opts), 这里不重复
		tips.extend(more_tips)
		# 补充自动调整窗口提示
		if 'cb_auto' in locals():
			try: tips.append((cb_auto,'根据预览图片尺寸自动调整窗口大小'))
			except Exception: pass
		for w,t in tips: self._bind_tip(w,t)
		self._update_states()
		# 原始日志缓存 (用于筛选)
		self._raw_logs=[]  # list of tuples (stage, src_full, dst_full, info, display_values, tags)
		# 记录基础窗口最小尺寸
		try:
			self.root.update_idletasks(); self._base_win_width=self.root.winfo_width(); self._base_win_height=self.root.winfo_height()
			self._min_window_width = self._base_win_width  # 设置最小窗口宽度
		except Exception:
			self._base_win_width=900; self._base_win_height=600
			self._min_window_width = 900  # 默认最小宽度
		# 捕获日志区初始高度用于锁定
		self._log_fixed_height=None
		self.root.after(400,self._capture_log_height)

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
		# 清空已处理文件记录
		self.processed_source_files.clear()
		self.cache_to_original_map.clear()
		# 在开始时清除缓存
		if dry_run:
			self._clear_cache()
			self._ensure_cache_dir()
		else:
				# 直接清除旧缓存 (取消快速提交逻辑)
				self._clear_cache()
		inp=self.in_var.get().strip()
		if not inp: self.status_var.set('未选择输入'); return
		self.single_file_mode=False
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
			self.single_file_mode=True
		else:
			self.status_var.set('输入不存在'); return
		if not self._all_files: self.status_var.set('无图片'); return
		for i in self.log.get_children(): self.log.delete(i)
		self.progress['value']=0; self.progress['maximum']=len(self._all_files)
		self.status_var.set('开始...' if not dry_run else '预览模式 (不修改文件)')
		self.stop_flag.clear(); self.last_out_dir=out_dir
		self.worker=threading.Thread(target=self._pipeline,daemon=True); self.worker.start()

	def _cancel(self):
		self.stop_flag.set(); self.status_var.set('请求取消...')

	def _open_last_out(self):
		# 预览模式下无条件优先打开缓存目录 (更符合“查看预览结果”需求)
		if self.dry_run:
			try:
				self._ensure_cache_dir()
			except Exception:
				pass
			if self.cache_dir and os.path.isdir(self.cache_dir):
				path=self.cache_dir
			else:
				path=self.last_out_dir or self.out_var.get().strip()
		else:
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

	def _open_program_log(self):
		"""打开程序日志文件查看详细错误信息"""
		try:
			self._ensure_cache_dir()
			if not self.cache_dir:
				self.status_var.set('缓存目录未创建'); return
			log_path = os.path.join(self.cache_dir, 'program.log')
			if not os.path.exists(log_path):
				self.status_var.set('日志文件不存在'); return
			
			if sys.platform.startswith('win'):
				os.startfile(log_path)  # type: ignore
			elif sys.platform=='darwin':
				subprocess.Popen(['open', log_path])
			else:
				subprocess.Popen(['xdg-open', log_path])
			self.status_var.set('已打开程序日志')
		except Exception as e:
			self.status_var.set(f'打开日志失败:{e}')

	def _set_hidden_attribute(self, path):
		"""设置文件/文件夹的隐藏属性"""
		try:
			if sys.platform.startswith('win'):
				# Windows系统使用attrib命令设置隐藏属性
				import subprocess
				subprocess.run(['attrib', '+H', path], capture_output=True, check=False)
			else:
				# 非Windows系统，文件名以.开头通常被认为是隐藏的
				# 这里不需要额外操作，因为.preview_cache已经是以.开头
				pass
		except Exception:
			# 设置隐藏属性失败不影响主要功能
			pass

	def _ensure_cache_dir(self):
		if self.cache_dir and os.path.exists(self.cache_dir):
			return
		out_dir = self.out_var.get().strip() or os.getcwd()
		self.cache_dir = os.path.join(out_dir, '.preview_cache')
		# 若错误指向 _final, 回退
		if os.path.basename(self.cache_dir)=='_final':
			self.cache_dir=os.path.dirname(self.cache_dir)
		os.makedirs(self.cache_dir, exist_ok=True)
		
		# 设置缓存目录为隐藏属性
		self._set_hidden_attribute(self.cache_dir)
		
		# 同时建立模拟回收站目录
		self.cache_trash_dir=os.path.join(self.cache_dir,'_trash')
		os.makedirs(self.cache_trash_dir, exist_ok=True)
		# 建立最终结果目录（避免嵌套 _final/_final）
		candidate_final=os.path.join(self.cache_dir,'_final')
		if os.path.basename(self.cache_dir) == '_final':  # 已经指向 final
			self.cache_final_dir=self.cache_dir
		else:
			self.cache_final_dir=candidate_final
		if not os.path.exists(self.cache_final_dir):
			os.makedirs(self.cache_final_dir, exist_ok=True)
		# 清除内层重复 _final
		inner=os.path.join(self.cache_final_dir,'_final')
		if os.path.isdir(inner):
			try: shutil.rmtree(inner)
			except Exception: pass

	def _clear_cache(self):
		if self.cache_dir and os.path.exists(self.cache_dir):
			try:
				shutil.rmtree(self.cache_dir)
				self.cache_dir = None
				self.cache_trash_dir=None
				self.cache_final_dir=None
			except Exception:
				pass

	def _on_close(self):
		# 停止预览线程
		if hasattr(self, 'preview_thread') and self.preview_thread:
			self.preview_thread.stop()
		
		# 清理动画定时器
		for label in [getattr(self, 'preview_before_label', None), getattr(self, 'preview_after_label', None)]:
			if label and hasattr(label, '_animation_timer'):
				try:
					label.after_cancel(label._animation_timer)
				except Exception:
					pass
		self._clear_cache()
		self.root.destroy()

	def _preview(self):
		if self.worker and self.worker.is_alive():
			messagebox.showinfo('提示','任务运行中'); return
		self._start(dry_run=True)
		self.status_var.set('预览模式 (不修改文件)')

	def _update_preview_ui(self, src_data, result_data):
		"""在主线程中更新预览UI"""
		try:
			# 清理之前的动画和引用
			for label in [self.preview_before_label, self.preview_after_label]:
				if hasattr(label, '_animation_timer'):
					try:
						label.after_cancel(label._animation_timer)
						delattr(label, '_animation_timer')
					except Exception:
						pass
			
			# 处理源图片
			if src_data:
				self._apply_image_to_label(self.preview_before_label, self.preview_before_info, src_data)
			else:
				self.preview_before_label.configure(text='(无源)', image='')
				self.preview_before_label._img_ref = None
				self.preview_before_info.set('')
			
			# 处理结果图片
			if result_data:
				self._apply_image_to_label(self.preview_after_label, self.preview_after_info, result_data)
			else:
				self.preview_after_label.configure(text='(无结果)', image='')
				self.preview_after_label._img_ref = None
				self.preview_after_info.set('')
			
			self._maybe_resize_window()
		except Exception as e:
			print(f"Preview UI update error: {e}")

	def _apply_image_to_label(self, label, info_var, image_data):
		"""将图片数据应用到标签"""
		try:
			# 清除文本模式标记
			if hasattr(label, '_text_mode'):
				label._text_mode = False
			# 停止现有动画
			if hasattr(label, '_animation_timer'):
				label.after_cancel(label._animation_timer)
				delattr(label, '_animation_timer')
			# 清除图片引用
			label._img_ref = None
			
			if image_data['type'] == 'static':
				# 静态图片
				photo = ImageTk.PhotoImage(image_data['image'])
				label.configure(image=photo, text='')
				label._img_ref = photo
				
				# 设置信息
				size_mb = image_data['size'] / (1024 * 1024)
				w, h = image_data['image'].size
				
				# 计算相对路径
				base_root = (self.cache_final_dir or self.cache_dir) if self.dry_run else (self.out_var.get().strip() or self.in_var.get().strip())
				try:
					rel = os.path.relpath(image_data['path'], base_root)
				except Exception:
					rel = os.path.basename(image_data['path'])
				
				size_txt = self._format_size(image_data['size'])
				info_var.set(f'{w}x{h} {size_txt} {rel}')
				
			elif image_data['type'] == 'animated':
				# 动态图片
				frames = []
				for frame in image_data['frames']:
					frames.append(ImageTk.PhotoImage(frame))
				
				# 设置动画
				label._frames = frames
				label._frame_index = 0
				label._img_ref = frames[0] if frames else None
				
				def animate():
					if hasattr(label, '_frames') and label._frames:
						label._frame_index = (label._frame_index + 1) % len(label._frames)
						label.configure(image=label._frames[label._frame_index])
						label._animation_timer = label.after(50, animate)  # 50ms间隔，约20fps
				
				if len(frames) > 1:
					label.configure(image=frames[0], text='')
					label._animation_timer = label.after(50, animate)
				else:
					label.configure(image=frames[0] if frames else '', text='')
				
				# 设置信息
				size_mb = image_data['size'] / (1024 * 1024)
				w, h = image_data['frames'][0].size if image_data['frames'] else (0, 0)
				
				# 计算相对路径
				base_root = (self.cache_final_dir or self.cache_dir) if self.dry_run else (self.out_var.get().strip() or self.in_var.get().strip())
				try:
					rel = os.path.relpath(image_data['path'], base_root)
				except Exception:
					rel = os.path.basename(image_data['path'])
				
				size_txt = self._format_size(image_data['size'])
				info_var.set(f'{w}x{h} {size_txt} {rel} (动图 {len(frames)} 帧)')
				
			elif image_data['type'] == 'error':
				# 错误情况
				label.configure(text=f'加载失败: {image_data["error"]}', image='')
				label._img_ref = None
				info_var.set('加载失败')
				
		except Exception as e:
			print(f"Apply image error: {e}")
			label.configure(text=f'显示失败: {e}', image='')
			label._img_ref = None
			info_var.set('显示失败')

	def _format_size(self, size_bytes):
		"""格式化文件大小"""
		try:
			if size_bytes < 1024:
				return f"{size_bytes}B"
			elif size_bytes < 1024 * 1024:
				return f"{size_bytes / 1024:.1f}KB"
			else:
				return f"{size_bytes / (1024 * 1024):.2f}MB"
		except Exception:
			return "未知大小"

	# 管线
	def _copy_files_to_final(self, files):
		"""当没有启用任何处理功能时，将输入文件复制到final目录"""
		try:
			self._ensure_cache_dir()
			for src_file in files:
				if self.stop_flag.is_set():
					break
				if not os.path.exists(src_file):
					continue
				
				# 计算相对路径，保持目录结构
				if self.single_file_mode:
					rel_path = os.path.basename(src_file)
				else:
					# 计算相对于缓存输入目录的路径
					cache_input_dir = os.path.join(self.cache_dir, 'input')
					try:
						rel_path = os.path.relpath(src_file, cache_input_dir)
					except ValueError:
						rel_path = os.path.basename(src_file)
				
				# 目标路径
				dest_file = os.path.join(self.cache_final_dir, rel_path)
				
				# 确保目标目录存在
				os.makedirs(os.path.dirname(dest_file), exist_ok=True)
				
				# 复制文件
				try:
					shutil.copy2(src_file, dest_file)
					self.q.put(f'LOG\tCOPY_FINAL\t{src_file}\t{dest_file}\t复制到最终目录')
				except Exception as e:
					self.q.put(f'LOG\tCOPY_FINAL\t{src_file}\t{dest_file}\t复制失败: {e}')
		except Exception as e:
			import traceback
			error_detail = f"{str(e)} | Traceback: {traceback.format_exc().replace(chr(10), ' | ')}"
			self.q.put(f'LOG\tCOPY_FINAL\t\t\t失败: {error_detail}')

	def _copy_input_to_cache(self, files):
		"""将输入文件复制到缓存文件夹下的输入目录，返回新的文件路径列表"""
		try:
			# 创建缓存输入目录
			cache_input_dir = os.path.join(self.cache_dir, 'input')
			os.makedirs(cache_input_dir, exist_ok=True)
			
			input_dir = self.in_var.get().strip()
			copied_files = []
			
			# 清空原始文件映射
			self.cache_to_original_map = {}
			
			# 不显示复制过程的详细日志
			
			for i, file_path in enumerate(files):
				if self.stop_flag.is_set():
					break
				
				# 计算相对路径
				if self.single_file_mode:
					# 单文件模式，直接使用文件名
					relative_path = os.path.basename(file_path)
				else:
					# 多文件模式，保持相对路径结构
					relative_path = os.path.relpath(file_path, input_dir)
				
				# 目标路径
				cache_file_path = os.path.join(cache_input_dir, relative_path)
				
				# 确保目标目录存在
				os.makedirs(os.path.dirname(cache_file_path), exist_ok=True)
				
				# 复制文件
				try:
					shutil.copy2(file_path, cache_file_path)
					copied_files.append(cache_file_path)
					# 记录缓存文件到原始文件的映射
					self.cache_to_original_map[cache_file_path] = file_path
					# 不再显示每个文件的复制日志
				except Exception as e:
					self.q.put(f'LOG\tCOPY_INPUT\t{relative_path}\t\t复制失败: {e}')
			
			# 只显示总结信息
			if copied_files:
				self.q.put(f'STATUS 已准备 {len(copied_files)} 个文件进行处理')
			return copied_files
			
		except Exception as e:
			import traceback
			error_detail = f"{str(e)} | Traceback: {traceback.format_exc().replace(chr(10), ' | ')}"
			self.q.put(f'LOG\tCOPY_INPUT\t\t\t失败: {error_detail}')
			return files  # 返回原始文件列表作为备用

	def _pipeline(self):
		"""执行顺序: 0复制输入到缓存 -> 1分类(多文件且启用) -> 2转换 -> 3去重(多文件且启用) -> 4重命名 -> 5复制到最终输出(仅正常模式)"""
		try:
			files=self._all_files
			# 确保缓存目录已初始化
			self._ensure_cache_dir()
			# 0 复制输入文件到缓存 (新增步骤)
			files = self._copy_input_to_cache(files)
			if self.stop_flag.is_set(): return
			# 1 分类 (仅多文件; 单文件跳过) 提前, 影响后续路径结构
			if not self.single_file_mode and self.classify_ratio_var.get():
				files=self._ratio_classify_stage(files)
			if self.stop_flag.is_set(): return
			# 2 转换 (保持结构) 返回最终文件列表
			if self.enable_convert.get():
				files=self._convert_stage_only(files)
			if self.stop_flag.is_set(): return
			# 3 去重 (转换后, 可能减少文件) 单文件模式跳过
			if not self.single_file_mode and self.enable_dedupe.get():
				files=self._dedupe_stage(files)
			if self.stop_flag.is_set(): return
			# 4 重命名 (最后, 保证最终命名基于已分类/去重结果)
			if self.enable_rename.get():
				self._rename_stage_only(files)
			if self.stop_flag.is_set(): return
			
			# 4.5 如果没有启用任何处理功能，需要将文件复制到final目录
			if not self.dry_run and not any([
				not self.single_file_mode and self.classify_ratio_var.get(),
				self.enable_convert.get(),
				not self.single_file_mode and self.enable_dedupe.get(),
				self.enable_rename.get()
			]):
				self._copy_files_to_final(files)
			
			# 5 正常模式：将缓存中的最终结果复制到真正的输出目录
			remove_info = ""
			if not self.dry_run:
				remove_info = self._finalize_to_output()
			
			# 合并完成状态信息
			if self.dry_run:
				self.q.put('STATUS 预览完成')
			else:
				self.q.put(f'STATUS 完成{remove_info}')
			# 生成预览签名
			if self.dry_run and not self.stop_flag.is_set():
				self._last_preview_signature=self._calc_preview_signature()
				self._last_preview_files=[(p, os.path.getmtime(p), os.path.getsize(p)) for p in self._all_files if os.path.isfile(p)]
		except Exception as e:
			import traceback
			full_error = f"{str(e)} | {traceback.format_exc()}"
			self.q.put(f'STATUS 失败: {full_error}')
			print(f"[CRITICAL ERROR] Pipeline failed: {full_error}")
		finally:
			self.dry_run=False

	def is_animated_image(self, path: str) -> bool:
		"""检测图片是否为动图 (GIF, WebP, APNG)"""
		try:
			with Image.open(path) as im:
				# 检查是否有多帧
				if hasattr(im, 'is_animated') and im.is_animated:
					return True
				
				# 对于一些较老版本的PIL，手动检查帧数
				if im.format in ('GIF', 'WEBP'):
					try:
						im.seek(1)  # 尝试移动到第二帧
						return True
					except (AttributeError, EOFError):
						pass
				
				# 检查PNG是否为APNG (动态PNG)
				if im.format == 'PNG':
					# APNG会有特殊的chunk标识
					if hasattr(im, 'info') and 'transparency' in im.info:
						# 简单检查，更完整的检查需要解析PNG chunk
						try:
							frames = list(ImageSequence.Iterator(im))
							return len(frames) > 1
						except:
							pass
			
			return False
		except Exception:
			return False

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
						self._simulate_delete(o.path)
					else:
						ok,msg = safe_delete(o.path)
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
		ico_sizes=None
		if hasattr(self,'ico_keep_orig') and self.ico_keep_orig.get():
			ico_sizes=None  # Pillow 会用原图尺寸
		else:
			# 勾选尺寸 + 自定义输入合并
			chosen=[]
			if hasattr(self,'ico_size_vars'):
				for s,var in self.ico_size_vars.items():
					if var.get(): chosen.append(s)
			custom=self.ico_sizes_var.get().strip() if hasattr(self,'ico_sizes_var') else ''
			if custom:
				for token in custom.replace('；',';').replace(',', ' ').replace(';',' ').split():
					if token.isdigit():
						v=int(token)
						if 1<=v<=1024: chosen.append(v)
			if chosen:
				# 去重排序
				uniq=[]
				for v in sorted(set(chosen)):
					uniq.append(v)
				ico_sizes=uniq[:10]
		out_dir=self.out_var.get().strip() or self.in_var.get().strip()
		workers=max(1,self.workers_var.get())
		tasks=[]; idx=start
		# 如果目标是 ico 并且存在非方图，预先统计给予提示（仅一次）
		if fmt=='ico':
			warn_needed=False
			for f in files[:50]:  # 采样前50避免过慢
				try:
					with Image.open(f) as im:
						if im.size[0]!=im.size[1]:
							warn_needed=True; break
				except Exception: pass
			if warn_needed and self.ico_square_mode.get()=='keep':
				self.q.put('STATUS 检测到非方图, ICO 可能被拉伸, 可选择裁切/填充方式')
		# 按目录分组以实现“分类后每个目录独立排序编号”
		if self.enable_rename.get():
			from collections import defaultdict
			dir_map=defaultdict(list)
			for f in files:
				dir_map[os.path.dirname(f)].append(f)
			# 每个目录单独起始 start，并递增
			for d,flist in dir_map.items():
				local_idx=start
				for f in flist:
					src_ext=norm_ext(f)
					tgt_fmt=fmt if self.enable_convert.get() else src_ext
					need_convert=self.enable_convert.get() and (src_ext!=fmt or process_same)
					orig_stem=os.path.splitext(os.path.basename(f))[0]
					default_basename=f"{orig_stem}.{tgt_fmt}"
					name_raw=pattern
					pad_width=self.index_width_var.get() if hasattr(self,'index_width_var') else 0
					def repl_index(m):
						w=m.group(1); w_int=0
						if w:
							try: w_int=int(w)
							except ValueError: w_int=0
						use_w=w_int or pad_width
						return str(local_idx).zfill(use_w) if use_w>0 else str(local_idx)
					name_raw=re.sub(r'\{index:(\d+)\}', repl_index, name_raw)
					if '{index}' in name_raw:
						use_w=pad_width
						name_raw=name_raw.replace('{index}', str(local_idx).zfill(use_w) if use_w>0 else str(local_idx))
					name=name_raw.replace('{name}',orig_stem).replace('{ext}',src_ext).replace('{fmt}',tgt_fmt)
					if '.' not in os.path.basename(name): name+=f'.{tgt_fmt}'
					final_basename=os.path.basename(name)
					
					# 重命名应该在文件当前所在目录进行，而不是移动到其他地方
					# 删源选项只控制是否删除输入文件夹的原始文件，不影响输出文件夹的处理
					current_dir = os.path.dirname(f)
					final_path = os.path.join(current_dir, final_basename)
					
					if os.path.exists(final_path):
						if overwrite=='skip':
							self.q.put(f'LOG\tCONVERT\t{f}\t{final_path}\t跳过(存在)'); local_idx+=step; continue
						elif overwrite=='rename':
							final_path=next_non_conflict(final_path) if not self.dry_run else final_path+"(预览改名)"
					will_rename = (final_basename != default_basename)
					if need_convert:
						convert_basename = default_basename if will_rename else final_basename
						convert_path = os.path.join(current_dir, convert_basename)
						if will_rename and os.path.exists(convert_path) and convert_path!=final_path:
							convert_path = next_non_conflict(convert_path)
					else:
						if will_rename:
							convert_basename = default_basename
							convert_path = os.path.join(current_dir, convert_basename)
							if os.path.exists(convert_path) and convert_path!=final_path:
								convert_path = next_non_conflict(convert_path)
								convert_path = next_non_conflict(convert_path)
						else:
							convert_basename = final_basename
							convert_path = final_path
					tasks.append((f,need_convert,tgt_fmt,convert_path,final_path,will_rename,convert_basename,final_basename,local_idx))
					local_idx+=step
		else:
			# 不启用重命名则保持原逻辑（全局顺序，但不使用 pattern）
			for f in files:
				src_ext=norm_ext(f)
				tgt_fmt=fmt if self.enable_convert.get() else src_ext
				need_convert=self.enable_convert.get() and (src_ext!=fmt or process_same)
				orig_stem=os.path.splitext(os.path.basename(f))[0]
				default_basename=f"{orig_stem}.{tgt_fmt}"
				name=default_basename
				final_basename=os.path.basename(name)
				final_path=os.path.join(out_dir,final_basename)
				if os.path.exists(final_path):
					if overwrite=='skip':
						self.q.put(f'LOG\tCONVERT\t{f}\t{final_path}\t跳过(存在)'); idx+=step; continue
					elif overwrite=='rename':
						final_path=next_non_conflict(final_path) if not self.dry_run else final_path+"(预览改名)"
				will_rename=False
				if need_convert:
					convert_basename = final_basename
					convert_path = os.path.join(out_dir, convert_basename)
				else:
					convert_basename = final_basename
					convert_path = final_path
				tasks.append((f,need_convert,tgt_fmt,convert_path,final_path,will_rename,convert_basename,final_basename,idx))
				idx+=step
		total=len(tasks); self.q.put(f'STATUS 转换/重命名 共{total}')
		done=0; lock=threading.Lock(); final_paths=[]
		def job(spec):
			nonlocal done
			src,need_convert,tgt,convert_path,final_path,will_rename,convert_basename,final_basename,_idx=spec
			if self.stop_flag.is_set(): return
			# 预览时使用缓存路径
			if self.dry_run:
				# 将所有目标映射到缓存；区分转换/复制与重命名阶段
				if need_convert:
					convert_path = os.path.join(self.cache_dir, convert_basename)
				else:
					convert_path = os.path.join(self.cache_dir, convert_basename)
				if will_rename:
					# 重命名应该在同一个目录内进行，不创建_final文件夹
					final_path = os.path.join(os.path.dirname(convert_path), final_basename)
			else:
				os.makedirs(os.path.dirname(convert_path),exist_ok=True)
			msg_convert=''; ok_convert=True
			if need_convert:
				if self.dry_run:
					# 实际执行一次到缓存，保证可预览
					ok_convert,msg_convert=convert_one(src,convert_path,tgt,quality if tgt in ('jpg','png','webp') else None,png3 if tgt=='png' else False, ico_sizes if tgt=='ico' else None, self.ico_square_mode_code() if tgt=='ico' else None)
					if ok_convert:
						msg_convert = '转换(预览)'
					else:
						msg_convert = f'转换失败(预览):{msg_convert}'
				else:
					ok_convert,msg_convert=convert_one(src,convert_path,tgt,quality if tgt in ('jpg','png','webp') else None,png3 if tgt=='png' else False, ico_sizes if tgt=='ico' else None, self.ico_square_mode_code() if tgt=='ico' else None)
					if not ok_convert:
						msg_convert = f'转换失败:{msg_convert}'
						# 转换失败时：处理失败文件
						failed_path = self._handle_failed_file(src, msg_convert, True)
						if failed_path:
							msg_convert += f" (文件已移至失败文件夹)"
				# dry_run 时不执行删源操作
			else:
				# 纯重命名/复制路径
				if self.dry_run:
					# 预览: 复制源到中间(或最终)缓存，保证存在
					try:
						if os.path.abspath(src)!=os.path.abspath(convert_path):
							shutil.copy2(src, convert_path)
						ok_convert=True
						if os.path.abspath(src)==os.path.abspath(convert_path):
							msg_convert='保持(预览)'
						else:
							msg_convert='复制(预览)'
					except Exception as e:
						import traceback
						error_detail = f"{str(e)} | {traceback.format_exc().replace(chr(10), ' | ')}"
						ok_convert=False; msg_convert=f'复制失败(预览):{error_detail}'
				else:
					try:
						if os.path.abspath(src)==os.path.abspath(convert_path):
							ok_convert=True; msg_convert='保持'
						else:
							shutil.copy2(src,convert_path); ok_convert=True; msg_convert='复制'
					except Exception as e:
						import traceback
						error_detail = f"{str(e)} | {traceback.format_exc().replace(chr(10), ' | ')}"
						ok_convert=False; msg_convert=f'复制失败:{error_detail}'
			# 如果需要重命名(第二阶段)
			msg_rename=''; ok_rename=True
			if will_rename:
				if self.dry_run:
					try:
						if convert_path!=final_path:
							# 确保目标目录存在
							os.makedirs(os.path.dirname(final_path), exist_ok=True)
							if os.path.exists(convert_path):
								# 在同一目录内重命名，删除原文件
								os.rename(convert_path, final_path)
						ok_rename=True; msg_rename='命名(预览)'
					except Exception as e:
						ok_rename=False; msg_rename=f'命名失败(预览):{e}'
				else:
					try:
						os.replace(convert_path,final_path)
						ok_rename=True; msg_rename='命名'
					except Exception as e:
						ok_rename=False; msg_rename=f'命名失败:{e}'
			with lock:
				done+=1
				# 纯重命名（无转换）且需要重命名时，合并日志为一条
				if will_rename and not need_convert:
					# 判定物理操作 (只使用复制)
					if self.dry_run:
						op='复制(预览)'
					else:
						op='复制'
					if ok_convert and ok_rename:
						info_line=f'重命名 - {op}'
					else:
						# 如果任一失败，组合失败信息
						fail_msg = ('' if ok_convert else ('步骤失败:'+msg_convert)) + (';' if (not ok_convert and not ok_rename) else '') + ('' if ok_rename else ('命名失败:'+msg_rename))
						info_line=f'失败:{fail_msg or "重命名"}'
					self.q.put(f'LOG\tRENAME\t{src}\t{final_path}\t{info_line}')
				else:
					# 正常记录第一阶段
					stage1='CONVERT' if need_convert else 'RENAME'
					self.q.put(f'LOG\t{stage1}\t{src}\t{convert_path}\t{msg_convert if ok_convert else "失败:"+msg_convert}')
					# 第二阶段命名
					if will_rename:
						if ok_rename:
							self.q.put(f'LOG\tRENAME\t{convert_path}\t{final_path}\t重命名')
						else:
							self.q.put(f'LOG\tRENAME\t{convert_path}\t{final_path}\t{msg_rename}')
				self.q.put(f'PROG {done} {total}')
				# 成功的最终文件加入列表 (失败转换不加入)
				if (not need_convert or ok_convert) and (not will_rename or ok_rename):
					final_paths.append(final_path if not self.dry_run else final_path)
					# 记录成功处理的源文件（仅在正常模式下）
					if not self.dry_run:
						# 如果是从缓存处理的文件，记录对应的原始文件
						original_file = self.cache_to_original_map.get(src, src)
						self.processed_source_files.add(original_file)
		if workers>1:
			with ThreadPoolExecutor(max_workers=workers) as ex:
				futs=[ex.submit(job,t) for t in tasks]
				for _ in as_completed(futs):
					if self.stop_flag.is_set(): break
		else:
			for t in tasks: job(t)
		return final_paths

	# ===== 新阶段函数 (顺序版) =====
	def _parse_custom_ratios(self)->list[tuple[int,int,str]]:
		text=self.ratio_custom_var.get().strip() if hasattr(self,'ratio_custom_var') else ''
		if not text:
			# 若未自定义则使用默认一组
			text='16:9,3:2,4:3,1:1,21:9'
		pairs=[]
		for token in re.split(r'[;,\s]+',text):
			if not token: continue
			token=token.lower().replace('x',':')
			if ':' not in token: continue
			a,b=token.split(':',1)
			if a.isdigit() and b.isdigit():
				w=int(a); h=int(b)
				if w>0 and h>0 and w<=10000 and h<=10000:
					pairs.append((w,h,f'{w}x{h}'))
		# 去重按宽高
		uniq={}
		for w,h,label in pairs:
			uniq[(w,h)]=label
		return [(w,h,lbl) for (w,h),lbl in uniq.items()]

	def _ratio_classify_stage(self, file_list:list[str])->list[str]:
		"""严格按自定义比例分类: 仅命中自定义集合(±tol)的进入对应目录, 其余进入 other。
		所有模式都使用缓存目录进行中间处理，确保处理链完整。
		返回新路径列表 (分类后路径)。"""
		COMMON=self._parse_custom_ratios()
		if not COMMON: return file_list
		tol=self.ratio_tol_var.get() if hasattr(self,'ratio_tol_var') else 0.15
		preview=self.dry_run
		# 确保缓存目录已初始化，统一使用缓存目录进行中间处理
		self._ensure_cache_dir()
		# 根据模式选择输出目录：预览模式用缓存目录，正常模式用final目录
		base_out = self.cache_dir if preview else self.cache_final_dir
		workers=max(1,self.workers_var.get())
		result=[]; lock=threading.Lock(); done=0; total=len(file_list)
		def classify_one(p:str):
			nonlocal done
			if self.stop_flag.is_set(): return None
			if not os.path.isfile(p):
				with lock: done+=1; return p
			
			# 检查是否为动图并获取尺寸信息
			is_animated = self.is_animated_image(p)
			try:
				with Image.open(p) as im:
					w,h=im.size
			except Exception:
				with lock: done+=1; return p
			if h==0:
				with lock: done+=1; return p
			
			# 计算比例分类
			ratio=w/h; ratio_label='other'
			for rw,rh,lab in COMMON:
				ideal=rw/rh
				if ideal!=0 and abs(ratio-ideal)/ideal <= tol:
					ratio_label=lab; break
			
			# 根据是否为动图确定最终分类
			if is_animated:
				# 动图进行二次分类：AM/比例分类
				label = f'AM/{ratio_label}'
			else:
				# 静态图片直接按比例分类
				label = ratio_label
			
			dir_ratio=os.path.join(base_out,label)
			if not os.path.isdir(dir_ratio):
				try: os.makedirs(dir_ratio,exist_ok=True)
				except Exception: pass
			dest=os.path.join(dir_ratio, os.path.basename(p))
			if os.path.abspath(dest)==os.path.abspath(p):
				with lock:
					done+=1
				return p
			if os.path.exists(dest):
				if not preview:
					dest=next_non_conflict(dest)
				else:
					base_no,ext=os.path.splitext(dest); i=1
					alt=f"{base_no}_{i}{ext}"
					while os.path.exists(alt):
						i+=1; alt=f"{base_no}_{i}{ext}"
					dest=alt
			try:
				# 统一使用复制到缓存目录，保持源文件不变
				shutil.copy2(p,dest)
				self.q.put(f'LOG\tCLASSIFY\t{p}\t{dest}\t比例分类->{label}')
				res_path=dest
			except Exception as e:
				import traceback
				error_detail = f"{str(e)} | Traceback: {traceback.format_exc().replace(chr(10), ' | ')}"
				self.q.put(f'LOG\tCLASSIFY\t{p}\t{p}\t比例分类失败:{error_detail}')
				res_path=p
			with lock:
				done+=1
				self.q.put(f'PROG {done} {total}')
			return res_path
		if workers>1:
			with ThreadPoolExecutor(max_workers=workers) as ex:
				for fut in as_completed([ex.submit(classify_one,p) for p in file_list]):
					r=fut.result();
					if r: result.append(r)
		else:
			for p in file_list:
				r=classify_one(p); 
				if r: result.append(r)
		return result

	def _calc_preview_signature(self):
		parts=[]
		def add(k,v): parts.append(f"{k}={v}")
		add('classify', int(self.classify_ratio_var.get()))
		add('convert', int(self.enable_convert.get()))
		add('dedupe', int(self.enable_dedupe.get()))
		add('rename', int(self.enable_rename.get()))
		# 分类参数
		add('rtol', getattr(self,'ratio_tol_var',tk.DoubleVar(value=0)).get())
		add('rcustom', getattr(self,'ratio_custom_var',tk.StringVar(value='')).get())
		add('rsnap', getattr(self,'ratio_snap_var',tk.BooleanVar(value=False)).get())
		# 转换
		add('fmt', self.fmt_var.get() if hasattr(self,'fmt_var') else '')
		add('q', self.quality_var.get() if hasattr(self,'quality_var') else '')
		add('same', self.process_same_var.get() if hasattr(self,'process_same_var') else '')
		add('png3', self.png3_var.get() if hasattr(self,'png3_var') else '')
		add('rmcvt', self.convert_remove_src.get() if hasattr(self,'convert_remove_src') else '')
		# 重命名
		add('pattern', self.pattern_var.get() if hasattr(self,'pattern_var') else '')
		add('start', self.start_var.get() if hasattr(self,'start_var') else '')
		add('step', self.step_var.get() if hasattr(self,'step_var') else '')
		add('width', self.index_width_var.get() if hasattr(self,'index_width_var') else '')
		add('overwrite', self.overwrite_var.get() if hasattr(self,'overwrite_var') else '')
		# 去重
		add('th', self.threshold_var.get() if hasattr(self,'threshold_var') else '')
		add('keep', self.keep_var.get() if hasattr(self,'keep_var') else '')
		add('action', self.dedup_action_var.get() if hasattr(self,'dedup_action_var') else '')
		# 输入文件列表 + mtime + size
		files=[]
		for p in sorted(self._all_files):
			try:
				st=os.stat(p); files.append(f"{p}|{int(st.st_mtime)}|{st.st_size}")
			except Exception:
				files.append(f"{p}|0|0")
		parts.extend(files)
		digest=hashlib.md5('\n'.join(map(str,parts)).encode('utf-8','ignore')).hexdigest()
		return digest

	def _convert_stage_only(self, files:list[str])->list[str]:
		fmt=FMT_MAP.get(self.fmt_var.get(),'png')
		process_same=self.process_same_var.get(); quality=self.quality_var.get(); png3=self.png3_var.get()
		workers=max(1,self.workers_var.get())
		real_out=self.out_var.get().strip() or self.in_var.get().strip()
		# 确保缓存目录已初始化，统一使用缓存目录进行中间处理
		self._ensure_cache_dir()
		out_dir = (self.cache_final_dir or self.cache_dir)
		ico_sizes=None
		if hasattr(self,'ico_keep_orig') and self.ico_keep_orig.get():
			ico_sizes=None
		else:
			chosen=[]
			if hasattr(self,'ico_size_vars'):
				for s,var in self.ico_size_vars.items():
					if var.get(): chosen.append(s)
			custom=self.ico_sizes_var.get().strip() if hasattr(self,'ico_sizes_var') else ''
			if custom:
				for token in custom.replace('；',';').replace(',', ' ').replace(';',' ').split():
					if token.isdigit():
						v=int(token)
						if 1<=v<=1024: chosen.append(v)
			if chosen:
				ico_sizes=sorted(set(chosen))[:10]
		preview=self.dry_run
		class_root = (self.cache_dir if preview else real_out)
		results=[None]*len(files)
		lock=threading.Lock(); done=0; total=len(files)
		def do_one(i,f):
			nonlocal done
			if self.stop_flag.is_set(): return
			src_ext=norm_ext(f)
			tgt_fmt=fmt if self.enable_convert.get() else src_ext
			need_convert=self.enable_convert.get() and (src_ext!=fmt or process_same)
			if not need_convert:
				try:
					rel_dir=os.path.relpath(os.path.dirname(f), class_root)
					if rel_dir=='.': rel_dir=''
					dst_dir=os.path.join(out_dir, rel_dir)
					os.makedirs(dst_dir, exist_ok=True)
					dest_placeholder=os.path.join(dst_dir, os.path.basename(f))
					if not os.path.exists(dest_placeholder): 
						shutil.copy2(f, dest_placeholder)
						if not preview:
							self.q.put(f'LOG\tCONVERT\t{f}\t{dest_placeholder}\t无需转换')
				except Exception as e:
					if not preview:
						self.q.put(f'LOG\tCONVERT\t{f}\t\t复制失败: {e}')
				with lock:
					results[i]=dest_placeholder if 'dest_placeholder' in locals() else f
					done+=1; self.q.put(f'PROG {done} {total}')
				return
			basename=os.path.splitext(os.path.basename(f))[0]
			out_name=f"{basename}.{tgt_fmt}"
			rel_dir=os.path.relpath(os.path.dirname(f), class_root)
			if rel_dir=='.': rel_dir=''
			dest_dir=os.path.join(out_dir, rel_dir)
			try: os.makedirs(dest_dir, exist_ok=True)
			except Exception: pass
			dest=os.path.join(dest_dir,out_name)
			ok,msg=convert_one(f,dest,tgt_fmt,quality if tgt_fmt in ('jpg','png','webp') else None,png3 if tgt_fmt=='png' else False,ico_sizes if tgt_fmt=='ico' else None,self.ico_square_mode_code() if tgt_fmt=='ico' else None)
			with lock:
				if ok:
					self.q.put(f'LOG\tCONVERT\t{f}\t{dest}\t转换')
				else:
					self.q.put(f'LOG\tCONVERT\t{f}\t{dest}\t转换失败:{msg}')
				results[i]= dest if ok else None  # 失败的文件不传递到下一阶段
				done+=1; self.q.put(f'PROG {done} {total}')
		if workers>1 and len(files)>1:
			with ThreadPoolExecutor(max_workers=workers) as ex:
				futs=[ex.submit(do_one,i,f) for i,f in enumerate(files)]
				for _ in as_completed(futs):
					if self.stop_flag.is_set(): break
		else:
			for i,f in enumerate(files):
				do_one(i,f)
		return [r for r in results if r]

	def _rename_stage_only(self, files:list[str]):
		pattern=self.pattern_var.get().strip()
		if not pattern: return
		start=self.start_var.get(); step=self.step_var.get()
		pad_width=self.index_width_var.get(); overwrite=OVERWRITE_MAP.get(self.overwrite_var.get(),'overwrite')
		preview=self.dry_run
		real_out=self.out_var.get().strip() or self.in_var.get().strip()
		# 确保缓存目录已初始化，统一使用缓存目录进行中间处理
		self._ensure_cache_dir()
		out_dir=(self.cache_final_dir or self.cache_dir)
		# 若文件在分类子目录内，保持相对目录
		class_root=self.cache_dir
		
		# 按目录分组，每个目录独立编号（如果启用了分类）
		if self.classify_ratio_var.get():
			from collections import defaultdict
			dir_groups = defaultdict(list)
			for f in files:
				dir_key = os.path.dirname(f)
				dir_groups[dir_key].append(f)
			
			# 对每个目录单独处理
			for dir_path, dir_files in dir_groups.items():
				idx = start  # 每个目录从起始序号开始
				for f in dir_files:
					if self.stop_flag.is_set(): break
					if not os.path.isfile(f): continue
					idx = self._process_rename_file(f, pattern, idx, step, pad_width, overwrite, preview, out_dir, class_root)
		else:
			# 未启用分类时保持原逻辑
			idx = start
			for f in files:
				if self.stop_flag.is_set(): break
				if not os.path.isfile(f): continue
				idx = self._process_rename_file(f, pattern, idx, step, pad_width, overwrite, preview, out_dir, class_root)

	def _process_rename_file(self, f, pattern, idx, step, pad_width, overwrite, preview, out_dir, class_root):
		"""处理单个文件的重命名，返回下一个索引值"""
		ext=norm_ext(f); stem=os.path.splitext(os.path.basename(f))[0]
		name_raw=pattern
		def repl_index(m):
			w=int(m.group(1)); return str(idx).zfill(w)
		name_raw=re.sub(r'\{index:(\d+)\}', repl_index, name_raw)
		if '{index}' in name_raw:
			name_raw=name_raw.replace('{index}', str(idx).zfill(pad_width) if pad_width>0 else str(idx))
		# ratio 占位
		ratio_label=''
		parent=os.path.basename(os.path.dirname(f))
		if re.match(r'^\d+x\d+$', parent): ratio_label=parent
		if not ratio_label:
			match=re.search(r'(\d+)x(\d+)', stem)
			if match:
				ratio_label=f"{match.group(1)}x{match.group(2)}"
		name_raw=name_raw.replace('{ratio}', ratio_label or 'ratio')
		final_name=(name_raw.replace('{name}',stem).replace('{ext}',f'.{ext}').replace('{fmt}',ext))
		if '.' not in os.path.basename(final_name):
			final_name+=f'.{ext}'
		# 分类相对目录
		rel_dir=os.path.relpath(os.path.dirname(f), class_root)
		if rel_dir=='.': rel_dir=''
		target_dir=os.path.join(out_dir, rel_dir)
		os.makedirs(target_dir, exist_ok=True)
		dest=os.path.join(target_dir, final_name)
		if os.path.abspath(dest)==os.path.abspath(f):
			self.q.put(f'LOG\tRENAME\t{f}\t{dest}\t跳过(路径相同)')
			return idx + step
		if os.path.exists(dest):
			if overwrite=='skip':
				self.q.put(f'LOG\tRENAME\t{f}\t{dest}\t跳过(存在)')
				return idx + step
			elif overwrite=='rename':
				if not preview:
					dest=next_non_conflict(dest)
				else:
					dest=dest+'(预览改名)'
		try:
			# 在缓存目录内始终使用复制，保持文件链完整，便于预览查看
			shutil.copy2(f,dest)
			self.q.put(f'LOG\tRENAME\t{f}\t{dest}\t重命名')
		except Exception as e:
			import traceback
			error_detail = f"{str(e)} | Traceback: {traceback.format_exc().replace(chr(10), ' | ')}"
			# 重命名失败处理
			self.q.put(f'LOG\tRENAME\t{f}\t{dest}\t失败:{error_detail}')
		return idx + step

	def _finalize_to_output(self):
		"""正常模式：将缓存目录中的最终结果复制到真正的输出目录"""
		try:
			real_out = self.out_var.get().strip() or self.in_var.get().strip()
			if not real_out or not os.path.exists(self.cache_dir):
				return
			
			# 确保输出目录存在
			os.makedirs(real_out, exist_ok=True)
			
			# 清理输出目录中的文件，但保留缓存目录
			if os.path.exists(real_out):
				for item in os.listdir(real_out):
					item_path = os.path.join(real_out, item)
					# 跳过缓存目录
					if item == '.preview_cache':
						continue
					# 删除其他文件和目录
					try:
						if os.path.isdir(item_path):
							shutil.rmtree(item_path)
						else:
							os.remove(item_path)
					except Exception:
						pass  # 忽略删除错误
			
			# 使用 _final 目录作为源（如果存在），否则使用缓存目录
			source_dir = self.cache_final_dir if (self.cache_final_dir and os.path.exists(self.cache_final_dir)) else self.cache_dir
			
			# 复制所有文件到输出目录
			file_count = 0
			for root, dirs, files in os.walk(source_dir):
				# 跳过 _trash 目录
				if '_trash' in root:
					continue
					
				for file in files:
					if self.stop_flag.is_set():
						break
						
					src_path = os.path.join(root, file)
					if not os.path.isfile(src_path):
						continue
					
					# 计算相对路径
					rel_path = os.path.relpath(src_path, source_dir)
					dest_path = os.path.join(real_out, rel_path)
					
					# 确保目标目录存在
					os.makedirs(os.path.dirname(dest_path), exist_ok=True)
					
					# 复制文件到输出目录
					try:
						shutil.copy2(src_path, dest_path)
						file_count += 1
						self.q.put(f'LOG\tFINALIZE\t{src_path}\t{dest_path}\t复制到输出')
					except Exception as e:
						self.q.put(f'LOG\tFINALIZE\t{src_path}\t{dest_path}\t复制失败:{e}')
			
			# 内部状态信息，不显示在日志框中
			
			# 最后一步：如果启用删源功能，删除输入文件夹中的原始文件
			remove_info = ""
			if self.global_remove_src.get():
				deleted_count, failed_count = self._remove_source_files()
				if deleted_count > 0 or failed_count > 0:
					remove_info = f"，删源：删除 {deleted_count} 个文件，失败 {failed_count} 个"
				else:
					remove_info = "，无文件需要删除"
			
			return remove_info
			
		except Exception as e:
			import traceback
			error_detail = f"{str(e)} | Traceback: {traceback.format_exc().replace(chr(10), ' | ')}"
			self.q.put(f'LOG\tFINALIZE\t\t\t失败: {error_detail}')

	def _remove_source_files(self):
		"""删除输入文件夹中已成功处理的原始文件，返回删除统计"""
		try:
			input_dir = self.in_var.get().strip()
			if not input_dir or not os.path.exists(input_dir):
				self.q.put(f'LOG\tREMOVE_SRC\t\t\t输入目录无效或不存在')
				return 0, 0
			
			# 获取所有原始文件路径（从映射表中）
			original_files = set(self.cache_to_original_map.values())
			
			# 删除原始文件
			deleted_count = 0
			failed_count = 0
			
			for source_file in original_files:
				if self.stop_flag.is_set():
					break
					
				if not os.path.exists(source_file):
					continue  # 文件可能已经被删除
				
				# 确保要删除的文件确实在输入目录中
				try:
					# 检查文件是否在输入目录下
					rel_path = os.path.relpath(source_file, input_dir)
					if rel_path.startswith('..'):
						continue  # 文件不在输入目录下，跳过
				except ValueError:
					continue  # 路径无法计算相对路径，跳过
				
				try:
					os.remove(source_file)
					deleted_count += 1
					self.q.put(f'LOG\tREMOVE_SRC\t{source_file}\t\t删除原始文件')
				except Exception as e:
					failed_count += 1
					self.q.put(f'LOG\tREMOVE_SRC\t{source_file}\t\t删除失败: {e}')
			
			return deleted_count, failed_count
			
		except Exception as e:
			import traceback
			error_detail = f"{str(e)} | Traceback: {traceback.format_exc().replace(chr(10), ' | ')}"
			self.q.put(f'LOG\tREMOVE_SRC\t\t\t失败: {error_detail}')
			return 0, 0

	# (删除重复的旧 _ratio_classify_stage 定义)

	# 队列 + 预览
	def _drain(self):
		try:
			while True:
				m=self.q.get_nowait()
				# 错误行写入缓存 log.txt 并打印控制台
				try:
					self._append_cache_program_log(m)
				except Exception:
					pass
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
						# 根据 stage 推断 tag
						stag='STAGE_INFO'
						if stage=='DEDUP': stag='STAGE_DEDUPE'
						elif stage=='CONVERT': stag='STAGE_CONVERT'
						elif stage=='RENAME': stag='STAGE_RENAME'
						elif stage=='CLASSIFY': stag='STAGE_CLASSIFY'
						elif '删' in info or '删除' in info: stag='STAGE_DELETE'
						elif '移动' in info: stag='STAGE_MOVE'
						# 如果是重命名合并行(含 '重命名 - 移动/复制') 保持 RENAME 颜色
						if stage=='RENAME' and info.startswith('重命名 - '):
							stag='STAGE_RENAME'
						elif '保留' in info: stag='STAGE_KEEP'
						
						# 对于失败消息，在控制台打印完整信息
						if '失败' in info:
							print(f"[ERROR] {stage} | {src} -> {dst} | {info}")
						
						vals=(stage_disp, os.path.basename(src), os.path.basename(dst), info)
						row_tags=(src,dst,stag)
						self._raw_logs.append((stage,src,dst,info,vals,row_tags))
						if self._log_row_visible(stage,info,vals):
							self.log.insert('', 'end', values=vals, tags=row_tags)
					except Exception:
						pass
		except queue.Empty:
			pass
		finally:
			self.root.after(150,self._drain)

	def _on_select_row(self,_=None):
		sel=self.log.selection();
		if not sel: return
		values = self.log.item(sel[0],'values')
		if len(values) < 3: return
		stage_disp, src_basename, dst_basename, info = values[:4]
		tags = self.log.item(sel[0],'tags') or []  # (src_full, dst_full, stage_tag)
		src_full = tags[0] if len(tags)>=1 else ''
		dst_full_logged = tags[1] if len(tags)>=2 else ''
		
		# 检测失败项并显示错误信息
		if "失败" in info:
			self._show_error_in_preview(src_basename, info)
			return
		
		# 源与结果路径推断
		if self.dry_run:
			# 缓存中的结果
			dst_candidates=[os.path.join(self.cache_dir,dst_basename)]
			if not os.path.splitext(dst_basename)[1]: # 去重组行
				dst_candidates.insert(0, os.path.join(self.cache_dir, os.path.basename(src_full)))
		else:
			out_dir = self.out_var.get().strip() or self.in_var.get().strip()
			dst_candidates=[]
			if dst_full_logged and os.path.isfile(dst_full_logged): dst_candidates.append(dst_full_logged)
			dst_candidates.append(os.path.join(out_dir,dst_basename))
			if not os.path.splitext(dst_basename)[1]: dst_candidates.append(src_full)
		# 源候选
		src_candidates=[src_full]
		# 取存在的源与结果
		def first_exist(lst):
			for p in lst:
				if p and os.path.exists(p): return p
			return None
		src_path=first_exist(src_candidates)
		result_path=first_exist(dst_candidates)
		
		# 预览根基准: 真实执行=输出目录; 预览=cache_final_dir (若存在) 否则 cache_dir
		base_root = (self.cache_final_dir or self.cache_dir) if self.dry_run else (self.out_var.get().strip() or self.in_var.get().strip())
		
		# 使用预览线程处理图片加载
		self.preview_thread.add_preview_task(src_path, result_path)

	def _maybe_resize_window(self):
		if not getattr(self,'auto_resize_window',None): return
		if not self.auto_resize_window.get(): return
		self.root.update_idletasks()
		photo_b=getattr(self.preview_before_label,'_img_ref',None)
		photo_a=getattr(self.preview_after_label,'_img_ref',None)
		bw = photo_b.width() if photo_b else 0
		bh = photo_b.height() if photo_b else 0
		aw = photo_a.width() if photo_a else 0
		ah = photo_a.height() if photo_a else 0
		
		# 检查是否为文本模式（显示错误信息）
		text_mode = getattr(self.preview_after_label, '_text_mode', False)
		
		if (bw==0 and aw==0) and not text_mode:
			return
		
		# 计算内容高度
		if text_mode:
			# 文本模式：估算文本高度
			text_content = self.preview_after_label.cget('text')
			if text_content:
				# 根据文本行数和换行宽度估算高度
				lines = text_content.count('\n') + 1
				# 考虑自动换行的影响
				char_per_line = 50  # 大致每行字符数
				total_chars = len(text_content)
				wrapped_lines = max(lines, total_chars // char_per_line + 1)
				estimated_height = min(wrapped_lines * 18, 300)  # 每行约18像素，最大300像素
				img_h = max(estimated_height, 150)  # 最小150像素高度
			else:
				img_h = 150  # 默认高度
		else:
			# 图片模式：使用图片高度
			img_h=max(bh,ah)
		
		# 只调整高度: 计算需要的总高度
		root_y0=self.root.winfo_rooty()
		preview_top = self.preview_before_label.winfo_rooty()-root_y0
		extra_h=110  # info 行 + 边距
		desired_h=preview_top+img_h+extra_h
		sh=self.root.winfo_screenheight(); margin=50
		desired_h=min(desired_h, sh-margin)
		
		# 确保宽度不会自动调整：使用窗口的当前宽度或最小宽度
		cur_w=self.root.winfo_width()  # 保持当前宽度
		min_w = getattr(self, '_min_window_width', 800)  # 最小宽度800像素
		final_w = max(cur_w, min_w)  # 确保不会变得太小
		
		last=self._last_auto_size
		# last[0] 存旧宽度, last[1] 旧高度
		if not (last and abs(last[1]-desired_h)<10):
			self.root.geometry(f"{int(final_w)}x{int(desired_h)}")
			self._last_auto_size=(final_w,desired_h)
		# 固定日志区高度
		if self._log_fixed_height and hasattr(self,'paned') and hasattr(self,'upper_frame'):
			try:
				self.paned.paneconfigure(self.upper_frame,minsize=self._log_fixed_height)
			except Exception:
				pass
		# 调整分隔条, 让预览能完全显示
		# 不再强制设置 sash，以允许用户手动调整日志 / 预览比例

	def _show_error_in_preview(self, src_basename, error_info):
		"""在预览区域显示错误信息"""
		# 清除图片引用
		self.preview_before_label._img_ref = None
		self.preview_after_label._img_ref = None
		
		# 在左侧显示源文件名
		self.preview_before_label.configure(
			text=f"源文件: {src_basename}",
			image='',
			wraplength=380,  # 设置文本换行宽度
			justify='left'   # 左对齐
		)
		self.preview_before_info.set('')
		
		# 在右侧显示错误详情
		# 处理长错误信息，适当换行
		error_text = f"错误详情:\n{error_info}"
		if len(error_text) > 500:
			# 对于很长的错误信息，进行适当截断并保留重要部分
			lines = error_text.split('\n')
			if len(lines) > 10:
				error_text = '\n'.join(lines[:5] + ['...'] + lines[-3:])
			elif len(error_text) > 500:
				error_text = error_text[:500] + '...'
		
		self.preview_after_label.configure(
			text=error_text,
			image='',
			wraplength=380,  # 设置文本换行宽度
			justify='left',  # 左对齐
			anchor='nw'      # 内容对齐到左上角
		)
		self.preview_after_info.set('处理失败')
		
		# 标记为文本模式，并调用窗口调整
		self.preview_after_label._text_mode = True
		self._maybe_resize_window()

	def _handle_failed_file(self, src_path, reason, should_remove_src=False):
		"""处理失败的文件：如果设置了删源，将失败文件移动到失败文件夹"""
		if not should_remove_src:
			return  # 不需要删源就不处理
		
		try:
			# 确定失败文件夹路径
			if self.dry_run:
				# 预览模式：放到缓存目录下的failed文件夹
				self._ensure_cache_dir()
				failed_dir = os.path.join(self.cache_dir, 'failed')
			else:
				# 实际模式：放到输出目录下的failed文件夹
				out_dir = self.out_var.get().strip() or self.in_var.get().strip()
				failed_dir = os.path.join(out_dir, 'failed')
			
			os.makedirs(failed_dir, exist_ok=True)
			
			# 避免文件名冲突
			basename = os.path.basename(src_path)
			dst_path = os.path.join(failed_dir, basename)
			if os.path.exists(dst_path):
				base_no, ext = os.path.splitext(basename)
				i = 1
				while os.path.exists(dst_path):
					dst_path = os.path.join(failed_dir, f"{base_no}_{i}{ext}")
					i += 1
			
			if self.dry_run:
				# 预览模式：复制到失败文件夹
				shutil.copy2(src_path, dst_path)
				# 同时模拟删除原文件
				self._simulate_delete(src_path)
			else:
				# 实际模式：移动到失败文件夹
				shutil.move(src_path, dst_path)
			
			return dst_path
		except Exception as e:
			# 如果移动失败文件也失败了，至少记录一下
			print(f"[ERROR] Failed to handle failed file {src_path}: {e}")
			return None

	def _simulate_delete(self, path:str):
		"""预览模式: 将“删除”文件复制到缓存模拟回收站目录 (_trash)。"""
		try:
			self._ensure_cache_dir()
			if not self.cache_trash_dir:
				return
			os.makedirs(self.cache_trash_dir, exist_ok=True)
			base=os.path.basename(path)
			target=os.path.join(self.cache_trash_dir, base)
			# 避免同名覆盖
			if os.path.exists(target):
				base_no,ext=os.path.splitext(base); i=1
				while os.path.exists(target):
					target=os.path.join(self.cache_trash_dir, f"{base_no}_{i}{ext}"); i+=1
			shutil.move(path, target)
		except Exception:
			pass

	def _append_cache_program_log(self, line:str):
		"""将程序级日志(队列中的所有消息)写入缓存 log.txt, 带时间戳。用于排查内部问题。
		包含 HASH/PROG/STATUS/LOG 等。"""
		if not line:
			return
		try:
			self._ensure_cache_dir()
			if not self.cache_dir:
				return
			log_path=os.path.join(self.cache_dir,'program.log')
			stamp=time.strftime('%Y-%m-%d %H:%M:%S')
			with open(log_path,'a',encoding='utf-8',errors='ignore') as fw:
				fw.write(f'[{stamp}] {line}\n')
		except Exception:
			pass

	def _capture_log_height(self):
		try:
			if hasattr(self,'upper_frame') and self._log_fixed_height is None:
				self.root.update_idletasks()
				h=self.upper_frame.winfo_height()
				if h>60:
					self._log_fixed_height=h
					if hasattr(self,'paned'):
						self.paned.paneconfigure(self.upper_frame,minsize=h)
		except Exception:
			pass

	def _log_row_visible(self,stage:str,info:str,vals:tuple)->bool:
		stage_map={'DEDUP':'去重','CONVERT':'转换','RENAME':'重命名','CLASSIFY':'分类'}
		stage_ch=stage_map.get(stage,'信息')
		want=self.log_filter_stage.get() if hasattr(self,'log_filter_stage') else '全部'
		if want!='全部':
			if want=='删除' and ('删' in info or '删除' in info): pass
			elif want=='移动' and '移动' in info: pass
			elif want=='保留' and '保留' in info: pass
			elif want==stage_ch: pass
			elif want=='信息' and stage_ch=='信息': pass
			else: return False
		if hasattr(self,'log_filter_fail') and self.log_filter_fail.get():
			if '失败' not in info and '错' not in info:
				return False
		if hasattr(self,'log_filter_kw'):
			kw=self.log_filter_kw.get().strip()
			if kw:
				joined=' '.join(str(x) for x in vals)
				if kw.lower() not in joined.lower(): return False
		return True

	def _on_change_log_filter(self,*a):
		if not hasattr(self,'_raw_logs'): return
		for iid in self.log.get_children(): self.log.delete(iid)
		for stage,src,dst,info,vals,tags in self._raw_logs:
			if self._log_row_visible(stage,info,vals):
				self.log.insert('', 'end', values=vals, tags=tags)

	def ico_square_mode_code(self):
		return self.ico_square_mode.get() if hasattr(self,'ico_square_mode') else 'keep'

	def _reset_log_filter(self):
		if hasattr(self,'log_filter_stage'): self.log_filter_stage.set('全部')
		if hasattr(self,'log_filter_kw'): self.log_filter_kw.set('')
		if hasattr(self,'log_filter_fail'): self.log_filter_fail.set(False)
		self._on_change_log_filter()

	def _update_states(self):
		# 去重区
		# 分类区
		try:
			if hasattr(self,'frame_ratio') and self.frame_ratio:
				enabled=self.classify_ratio_var.get()
				# 基础控件
				for widget in (getattr(self,'_ratio_sp_rt',None), getattr(self,'_ratio_ent',None), getattr(self,'_ratio_btn_reset',None), getattr(self,'_ratio_snap',None), getattr(self,'_ratio_lbl_input',None), getattr(self,'_ratio_lbl_tol',None), getattr(self,'_classify_rm_src',None)):
					if widget:
						state='normal' if enabled else 'disabled'
						try: widget.configure(state=state)
						except Exception: pass
				# 预设按钮及清空
				if hasattr(self,'_ratio_preset_buttons'):
					for b in self._ratio_preset_buttons:
						try: b.configure(state='normal' if enabled else 'disabled')
						except Exception: pass
				if hasattr(self,'_ratio_btn_clear') and self._ratio_btn_clear:
					try: self._ratio_btn_clear.configure(state='normal' if enabled else 'disabled')
					except Exception: pass
		except Exception:
			pass
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
			# 仅当目标格式为 ico 时尺寸输入启用
			try:
				fmt_cur=FMT_MAP.get(self.fmt_var.get(),'')
				ico_enabled=(fmt_cur=='ico') and enabled
				state='normal' if ico_enabled else 'disabled'
				if hasattr(self,'ico_label'): self.ico_label.configure(state=state)
				if hasattr(self,'ico_custom_entry'): self.ico_custom_entry.configure(state=state)
				if hasattr(self,'ico_keep_cb'): self.ico_keep_cb.configure(state=state)
				if hasattr(self,'ico_checks'):
					for cb in self.ico_checks: cb.configure(state=state)
				# 非方图策略行
				if hasattr(self,'frame_sq'):
					for ch in self.frame_sq.winfo_children():
						try: ch.configure(state=state)
						except Exception: pass
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
		# 回收站复选框已移除

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
	def _on_out_dir_change(self, *args):
		# 输出目录改变时清除缓存
		self._clear_cache()
		self.root.bind('<Leave>',lambda e: self._hide_tooltip(),add='+')

# 启动
def launch():
	if tk is None or Image is None:
		print('缺少 Tkinter 或 Pillow'); return 2
	root=tk.Tk(); ImageToolApp(root); root.mainloop(); return 0

if __name__=='__main__':
	launch()
