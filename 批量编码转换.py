# -*- coding: utf-8 -*-
"""
批量文本编码转换工具
=====================
支持：
  * 目录/单文件批量转换
  * 自动探测原始编码 (charset-normalizer)，低置信度回退多个常见及不常见编码
  * 指定源编码 (--from-enc) / 目标编码 (--to-enc)
  * 递归/扩展名/包含/排除 过滤
  * 可选原地覆盖 / 输出到新目录 / 生成 .bak 备份
  * UTF-8 BOM 增加/移除
  * 错误策略 (strict|ignore|replace)
  * 干跑 (--dry-run) 查看将会执行的操作
  * 统计汇总、详细日志、失败原因
  * 尽量兼容不常见东亚编码 (GBK/GB18030/Big5/Shift_JIS/EUC-JP/EUC-KR/ISO-8859-x 等)

依赖: charset-normalizer (自动检测). 若未安装, 会提示安装, 仍尝试回退检测。

示例:
  python 批量编码转换.py -i ./data -r --ext .txt,.csv --to-enc utf-8 --in-place --backup .bak
  python 批量编码转换.py -i demo.txt -o out/ --to-enc gbk --add-bom
  python 批量编码转换.py -i src -r --include "*.py" --skip-same --dry-run
"""
from __future__ import annotations
import argparse
import os
import sys
import shutil
import glob
import fnmatch
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import queue
import platform
from dataclasses import dataclass
from typing import Optional, List, Tuple, Iterable
# 尝试导入 charset_normalizer
try:
	from charset_normalizer import from_path as cn_from_path  # type: ignore
except Exception:  # pragma: no cover
	cn_from_path = None

FALLBACK_ENCODINGS = [
	# 常见
	'utf-8', 'utf-8-sig', 'gb18030', 'gbk', 'gb2312', 'big5', 'shift_jis', 'euc_jp', 'euc_kr',
	# 单字节西文
	'cp1252', 'latin-1', 'iso-8859-1', 'iso-8859-15', 'macroman',
	# 其他
	'utf-16', 'utf-16le', 'utf-16be'
]
BOM_MAP = {
	'utf-8-sig': b'\xef\xbb\xbf',
	'utf-16': None,  # Python 会自动处理
	'utf-16le': b'\xff\xfe',
	'utf-16be': b'\xfe\xff'
}

TEXT_EXT_GUESS = {'.txt', '.csv', '.tsv', '.md', '.json', '.py', '.xml', '.html', '.htm', '.css', '.js', '.yml', '.yaml', '.ini', '.cfg', '.bat', '.sh', '.sql', '.log'}

@dataclass
class DetectResult:
	encoding: str
	confidence: float
	used: str  # 'specified' | 'charset-normalizer' | 'fallback' | 'forced-latin1'
	bom: bool


def iter_files(root: str, recursive: bool) -> Iterable[str]:
	if os.path.isfile(root):
		yield root
		return
	for dirpath, dirs, files in os.walk(root):
		for f in files:
			yield os.path.join(dirpath, f)
		if not recursive:
			break


def match_filters(path: str, include: List[str], exclude: List[str], exts: List[str]) -> bool:
	name = os.path.basename(path)
	if exts:
		if os.path.splitext(name)[1].lower() not in exts:
			return False
	if include:
		if not any(fnmatch.fnmatch(name, pat) for pat in include):
			return False
	if exclude:
		if any(fnmatch.fnmatch(name, pat) for pat in exclude):
			return False
	return True


def quick_binary_check(sample: bytes) -> bool:
	if b'\x00' in sample:
		return True
	# 若不可见控制字符占比高, 视为可能非文本
	ctrl = sum(1 for b in sample if b < 9 or (13 < b < 32))
	if len(sample) > 0 and ctrl / len(sample) > 0.20:
		return True
	return False


def detect_encoding(path: str, specified: Optional[str]) -> DetectResult:
	with open(path, 'rb') as f:
		raw = f.read()
	if specified:
		return DetectResult(specified, 1.0, 'specified', raw.startswith(b'\xef\xbb\xbf'))

	if quick_binary_check(raw[:1024]):
		# 仍尝试 utf-8 / fallback 解码
		pass

	# charset-normalizer 优先
	if cn_from_path is not None:
		try:
			matches = cn_from_path(path)
			if matches:  # 取首个最佳
				best = matches.best()
				if best and best.encoding:
					enc = best.encoding.lower()
					conf = float(best.fingerprint.get('chaos', 1.0))
					# chaos 值越低越好, 做一个转换为置信度的近似
					confidence = max(0.0, min(1.0, 1.0 - conf))
					return DetectResult(enc, confidence, 'charset-normalizer', raw.startswith(b'\xef\xbb\xbf'))
		except Exception:
			pass

	# fallback 尝试
	for enc in FALLBACK_ENCODINGS:
		try:
			raw.decode(enc)
			return DetectResult(enc, 0.3, 'fallback', raw.startswith(b'\xef\xbb\xbf'))
		except Exception:
			continue

	# 最后强制 latin-1
	return DetectResult('latin-1', 0.0, 'forced-latin1', raw.startswith(b'\xef\xbb\xbf'))


def write_text(path: str, text: str, encoding: str, add_bom: bool, errors: str):
	# 处理 UTF-8 BOM
	if encoding.lower() == 'utf-8' and add_bom:
		with open(path, 'wb') as f:
			f.write(BOM_MAP['utf-8-sig'])
			f.write(text.encode('utf-8', errors=errors))
		return
	# 其他编码直接写
	with open(path, 'w', encoding=encoding, errors=errors, newline='') as f:
		f.write(text)


def convert_file(src: str, dst: str, from_enc: Optional[str], to_enc: str, errors: str, add_bom: bool, remove_bom: bool, skip_same: bool, dry_run: bool) -> Tuple[str, str, bool]:
	"""Return (status, message, changed) status in {OK,SKIP,FAIL}"""
	try:
		det = detect_encoding(src, from_enc)
		with open(src, 'rb') as f:
			raw = f.read()
		text: str
		# 对 BOM -> utf-8-sig 交给 Python 处理
		read_enc = det.encoding
		try:
			text = raw.decode(read_enc, errors='strict')
		except Exception:
			# 回退宽松模式
			text = raw.decode(read_enc, errors='replace')
		orig_bom = raw.startswith(b'\xef\xbb\xbf')
		target_enc = to_enc.lower()
		need_bom = add_bom
		if remove_bom:
			need_bom = False
		# 是否需要转换
		same_enc = (read_enc.replace('-sig','') == target_enc.replace('-sig',''))
		same_bom_state = (orig_bom == need_bom)
		if skip_same and same_enc and same_bom_state:
			return 'SKIP', f'skip (same {read_enc}{"+BOM" if orig_bom else ""})', False
		# dry run
		if dry_run:
			return 'OK', f'dry-run {read_enc}{"+BOM" if orig_bom else ""} -> {target_enc}{"+BOM" if need_bom else ""}', True
		# 确保目录
		out_dir = os.path.dirname(dst)
		if out_dir and not os.path.exists(out_dir):
			os.makedirs(out_dir, exist_ok=True)
		write_text(dst, text, target_enc, need_bom, errors)
		return 'OK', f'{read_enc}{"+BOM" if orig_bom else ""} -> {target_enc}{"+BOM" if need_bom else ""} ({det.used}:{det.confidence:.2f})', True
	except Exception as e:
		return 'FAIL', str(e), False


def parse_args(argv: List[str]) -> argparse.Namespace:
	p = argparse.ArgumentParser(description='批量文本编码转换')
	p.add_argument('-i', '--input', required=True, help='输入文件或目录')
	p.add_argument('-o', '--output', help='输出文件或目录 (不指定且 --in-place 时原地)')
	p.add_argument('-r', '--recursive', action='store_true', help='递归处理子目录')
	p.add_argument('--from-enc', help='指定源编码(跳过检测)')
	p.add_argument('--to-enc', default='utf-8', help='目标编码 (默认 utf-8)')
	p.add_argument('--ext', help='仅处理这些扩展名, 逗号分隔, 例如 .txt,.csv')
	p.add_argument('--include', help='仅包含匹配的文件(逗号; glob 模式)')
	p.add_argument('--exclude', help='排除匹配的文件(逗号; glob 模式)')
	p.add_argument('--in-place', action='store_true', help='原地转换')
	p.add_argument('--backup', default='', help='原地转换时生成备份扩展 (例如 .bak), 空则不备份')
	p.add_argument('--errors', default='strict', choices=['strict','ignore','replace'], help='写入时错误策略')
	p.add_argument('--add-bom', action='store_true', help='写入 UTF-8 时添加 BOM')
	p.add_argument('--remove-bom', action='store_true', help='移除 BOM (若存在)')
	p.add_argument('--skip-same', action='store_true', help='编码+BOM 状态相同则跳过')
	p.add_argument('--dry-run', action='store_true', help='仅显示将要执行的转换, 不写入')
	p.add_argument('--force', action='store_true', help='输出存在时强制覆盖')
	p.add_argument('--list-encodings', action='store_true', help='列出 Python 已知编码别名并退出')
	p.add_argument('--workers', type=int, default=0, help='并行线程数(0=自动)')
	return p.parse_args(argv)


def list_encodings():
	import encodings.aliases
	aliases = encodings.aliases.aliases
	items = sorted(set(v.lower() for v in aliases.values()))
	for enc in items:
		print(enc)
	print(f"共 {len(items)} 个编码别名")


def main(argv: List[str]) -> int:
	args = parse_args(argv)
	if args.list_encodings:
		list_encodings()
		return 0
	inp = args.input
	if not os.path.exists(inp):
		print('输入不存在', file=sys.stderr)
		return 2

	if not args.in_place and not args.output:
		print('未指定输出目录且未设置 --in-place', file=sys.stderr)
		return 2

	if args.in_place and args.output and os.path.isdir(inp) and os.path.abspath(inp) == os.path.abspath(args.output):
		# 允许显式指定同目录
		pass

	exts = []
	if args.ext:
		exts = [e.lower().strip() if e.startswith('.') else f'.{e.lower().strip()}' for e in args.ext.split(',') if e.strip()]
	include = [p.strip() for p in (args.include.split(',') if args.include else []) if p.strip()]
	exclude = [p.strip() for p in (args.exclude.split(',') if args.exclude else []) if p.strip()]

	# 预收集任务
	tasks: List[Tuple[str, str]] = []  # (src, dst)
	for path in iter_files(inp, args.recursive):
		if os.path.isdir(path):
			continue
		rel = os.path.relpath(path, inp) if os.path.isdir(inp) else os.path.basename(path)
		# 未指定扩展且目录模式时按猜测集过滤
		if not exts and os.path.isdir(inp) and os.path.splitext(path)[1].lower() not in TEXT_EXT_GUESS:
			continue
		if not match_filters(path, include, exclude, exts):
			continue
		if args.in_place:
			dst = path
		else:
			base_out = args.output if args.output else inp
			dst = base_out if os.path.isfile(inp) else os.path.join(base_out, rel)
		tasks.append((path, dst))

	total = len(tasks)
	if total == 0:
		print('无匹配文件')
		return 0
	print(f'待处理文件: {total} (并行: {args.workers or os.cpu_count()})')

	converted = skipped = failed = 0
	lock = threading.Lock()

	def process_one(idx_path_dst: Tuple[int, Tuple[str,str]]):
		idx, (src, dst) = idx_path_dst
		rel = os.path.relpath(src, inp) if os.path.isdir(inp) else os.path.basename(src)
		# 目标存在判断
		if not args.force and not args.in_place and os.path.exists(dst) and not args.dry_run:
			return idx, 'SKIP', rel, '存在且未覆盖', False
		# 备份
		if args.in_place and args.backup and not args.dry_run:
			bak_path = src + args.backup
			if not os.path.exists(bak_path):
				try:
					shutil.copy2(src, bak_path)
				except Exception:
					pass
		status, msg, changed = convert_file(src, dst, args.from_enc, args.to_enc, args.errors, args.add_bom, args.remove_bom, args.skip_same, args.dry_run)
		return idx, status, rel, msg, changed

	workers = (args.workers if args.workers > 0 else (os.cpu_count() or 4)) or 4
	results: List[Tuple[int,str,str,str,bool]] = []
	results.extend([(-1,'','','',False)] * total)
	done = 0
	with ThreadPoolExecutor(max_workers=workers) as ex:
		futures = {ex.submit(process_one, (i, t)): i for i, t in enumerate(tasks)}
		for fut in as_completed(futures):
			idx, status, rel, msg, changed = fut.result()
			with lock:
				if status == 'OK':
					if changed:
						converted += 1
					else:
						skipped += 1
				elif status == 'SKIP':
					skipped += 1
				else:
					failed += 1
				done += 1
				pct = int(done/total*100)
				tag = 'OK ' if status == 'OK' else status
				print(f'[{tag:4}] {rel} :: {msg}  ({pct}% {done}/{total})')
	print('-'*60)
	print(f'文件总数: {total}  转换: {converted}  跳过: {skipped}  失败: {failed}')
	if args.dry_run:
		print('(dry-run 未做实际写入)')
	return 0 if failed == 0 else 1


############################################
# GUI 部分 (无参数运行时启动)
############################################
try:
	import tkinter as tk
	from tkinter import ttk, filedialog, messagebox
except Exception:  # pragma: no cover
	tk = None


class GUIApp:
	def __init__(self, root):
		if tk is None:
			raise RuntimeError('Tkinter 不可用')
		self.root = root
		self.root.title('批量文本编码转换')
		self.queue = queue.Queue()
		self.worker = None
		self.stop_flag = threading.Event()
		# DPI / 字体缩放初始化
		self.ui_scale = 1.0
		self.current_font_size = None
		self._init_scaling()
		# 变量
		self.var_input = tk.StringVar()
		self.var_output = tk.StringVar()
		self.var_recursive = tk.BooleanVar(value=True)
		self.var_inplace = tk.BooleanVar(value=False)
		self.var_from = tk.StringVar()
		self.var_to = tk.StringVar(value='utf-8')
		self.var_ext = tk.StringVar()
		self.var_include = tk.StringVar()
		self.var_exclude = tk.StringVar()
		self.var_backup = tk.StringVar(value='.bak')
		self.var_add_bom = tk.BooleanVar(value=False)
		self.var_remove_bom = tk.BooleanVar(value=False)
		self.var_skip_same = tk.BooleanVar(value=True)
		self.var_dry = tk.BooleanVar(value=False)
		self.var_force = tk.BooleanVar(value=False)
		self.var_errors = tk.StringVar(value='strict')
		self.var_workers = tk.IntVar(value=max(1, (os.cpu_count() or 4)//2))
		self.progress_var = tk.IntVar(value=0)
		self.status_var = tk.StringVar(value='就绪')
		self.progress = None  # type: ignore
		self.log = None  # type: ignore
		self._build()
		self.root.after(120, self._drain_queue)

	def _build(self):
		frm = ttk.Frame(self.root, padding=10)
		frm.pack(fill='both', expand=True)
		for i in range(6):
			frm.columnconfigure(i, weight=1)
		r = 0
		ttk.Label(frm, text='输入(文件或目录):').grid(row=r, column=0, sticky='w')
		ttk.Entry(frm, textvariable=self.var_input, width=50).grid(row=r, column=1, columnspan=3, sticky='we', padx=4)
		ttk.Button(frm, text='文件', command=self._pick_file).grid(row=r, column=4, padx=2)
		ttk.Button(frm, text='目录', command=self._pick_dir).grid(row=r, column=5, padx=2)
		r += 1
		ttk.Checkbutton(frm, text='原地', variable=self.var_inplace, command=self._toggle_inplace).grid(row=r, column=0, sticky='w')
		ttk.Label(frm, text='输出目录:').grid(row=r, column=1, sticky='e')
		self.out_entry = ttk.Entry(frm, textvariable=self.var_output, width=40)
		self.out_entry.grid(row=r, column=2, columnspan=3, sticky='we', padx=4)
		ttk.Button(frm, text='选择', command=self._pick_output).grid(row=r, column=5, padx=2)
		r += 1
		ttk.Label(frm, text='源编码(空自动):').grid(row=r, column=0, sticky='w')
		ttk.Entry(frm, textvariable=self.var_from, width=12).grid(row=r, column=1, sticky='w')
		ttk.Label(frm, text='目标:').grid(row=r, column=2, sticky='e')
		ttk.Combobox(frm, textvariable=self.var_to, width=14, values=['utf-8','utf-8-sig','gbk','gb18030','big5','shift_jis','euc_jp','euc_kr','latin-1','utf-16','utf-16le','utf-16be']).grid(row=r, column=3, sticky='w')
		ttk.Label(frm, text='错误:').grid(row=r, column=4, sticky='e')
		ttk.Combobox(frm, textvariable=self.var_errors, width=10, values=['strict','ignore','replace'], state='readonly').grid(row=r, column=5, sticky='w')
		r += 1
		ttk.Label(frm, text='并行线程:').grid(row=r, column=0, sticky='w')
		try:
			ttk.Spinbox(frm, from_=1, to=max(64, (os.cpu_count() or 8)), textvariable=self.var_workers, width=8).grid(row=r, column=1, sticky='w')
		except Exception:  # 兼容无 ttk.Spinbox 的环境
			ttk.Entry(frm, textvariable=self.var_workers, width=8).grid(row=r, column=1, sticky='w')
		r += 1
		ttk.Button(frm, text='编码说明', command=self._show_encoding_info).grid(row=r, column=0, sticky='w', pady=2)
		ttk.Button(frm, text='选项说明', command=self._show_option_info).grid(row=r, column=1, sticky='w', pady=2)
		ttk.Button(frm, text='A-', width=3, command=lambda: self._adjust_font(-1)).grid(row=r, column=4, sticky='e')
		ttk.Button(frm, text='A+', width=3, command=lambda: self._adjust_font(1)).grid(row=r, column=5, sticky='w')
		r += 1
		for (txt, var) in [
			('递归', self.var_recursive),
			('添加BOM', self.var_add_bom),
			('移除BOM', self.var_remove_bom),
			('跳过相同', self.var_skip_same),
			('dry-run', self.var_dry),
			('覆盖存在', self.var_force)
		]:
			col = ['递归','添加BOM','移除BOM','跳过相同','dry-run','覆盖存在'].index(txt)
			ttk.Checkbutton(frm, text=txt, variable=var).grid(row=r, column=col, sticky='w')
		r += 1
		ttk.Label(frm, text='扩展(.txt,逗号):').grid(row=r, column=0, sticky='w')
		ttk.Entry(frm, textvariable=self.var_ext, width=18).grid(row=r, column=1, sticky='w')
		ttk.Label(frm, text='包含:').grid(row=r, column=2, sticky='e')
		ttk.Entry(frm, textvariable=self.var_include, width=16).grid(row=r, column=3, sticky='w')
		ttk.Label(frm, text='排除:').grid(row=r, column=4, sticky='e')
		ttk.Entry(frm, textvariable=self.var_exclude, width=12).grid(row=r, column=5, sticky='w')
		r += 1
		ttk.Label(frm, text='备份扩展:').grid(row=r, column=0, sticky='w')
		ttk.Entry(frm, textvariable=self.var_backup, width=10).grid(row=r, column=1, sticky='w')
		ttk.Button(frm, text='开始', command=self._start).grid(row=r, column=3, pady=4)
		ttk.Button(frm, text='取消', command=self._cancel).grid(row=r, column=4, pady=4)
		r += 1
		self.progress = ttk.Progressbar(frm, maximum=100, variable=self.progress_var)  # type: ignore
		self.progress.grid(row=r, column=0, columnspan=6, sticky='we', pady=4)  # type: ignore
		r += 1
		ttk.Label(frm, textvariable=self.status_var, foreground='blue').grid(row=r, column=0, columnspan=6, sticky='w')
		r += 1
		self.log = tk.Text(frm, height=16, wrap='none')  # type: ignore
		self.log.grid(row=r, column=0, columnspan=6, sticky='nsew')  # type: ignore
		frm.rowconfigure(r, weight=1)
		try:
			import tkinter.font as tkfont
			self.log.configure(font=tkfont.nametofont('TkTextFont'))
		except Exception:
			pass

	def _pick_file(self):
		p = filedialog.askopenfilename()
		if p:
			self.var_input.set(p)

	def _pick_dir(self):
		d = filedialog.askdirectory()
		if d:
			self.var_input.set(d)

	def _pick_output(self):
		d = filedialog.askdirectory()
		if d:
			self.var_output.set(d)

	# ---------------- DPI / 字体 -----------------
	def _init_scaling(self):
		try:
			# Windows 下获取 DPI
			if platform.system() == 'Windows':
				try:
					from ctypes import windll
					try:
						windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor V2
					except Exception:
						try:
							windll.user32.SetProcessDPIAware()
						except Exception:
							pass
					hwnd = windll.user32.GetForegroundWindow()
					if hasattr(windll.user32, 'GetDpiForWindow'):
						dpi = windll.user32.GetDpiForWindow(hwnd)
					else:
						# 回退: 96 * scaling
						dpi = 96
				except Exception:
					dpi = self.root.winfo_fpixels('1i')
			else:
				dpi = self.root.winfo_fpixels('1i')
		except Exception:
			dpi = 96
		try:
			# Tk 缺省 72dpi scaling 基于 96 需要换算
			scale = float(dpi) / 96.0
			if scale < 0.9:
				scale = 1.0
			self.ui_scale = scale
			if scale != 1.0:
				self.root.tk.call('tk', 'scaling', scale)
			import tkinter.font as tkfont
			for name in ("TkDefaultFont","TkTextFont","TkMenuFont","TkHeadingFont","TkTooltipFont","TkFixedFont"):
				try:
					f = tkfont.nametofont(name)
					base = f.cget('size')
					new_size = max(8, int(base * scale))
					f.configure(size=new_size)
				except Exception:
					pass
			# 记录当前字号(使用 TkDefaultFont)
			try:
				self.current_font_size = tkfont.nametofont('TkDefaultFont').cget('size')
			except Exception:
				self.current_font_size = 10
		except Exception:
			self.ui_scale = 1.0
			self.current_font_size = 10

	def _adjust_font(self, delta: int):
		if self.current_font_size is None:
			return
		import tkinter.font as tkfont
		new_size = min(32, max(8, self.current_font_size + delta))
		if new_size == self.current_font_size:
			return
		for name in ("TkDefaultFont","TkTextFont","TkMenuFont","TkHeadingFont","TkTooltipFont","TkFixedFont"):
			try:
				f = tkfont.nametofont(name)
				f.configure(size=new_size)
			except Exception:
				pass
		self.current_font_size = new_size

	def _show_encoding_info(self):
		# 分组+空行提升可读性
		sections = [
			('Unicode / UTF', [
				'UTF-8  可变长1~4字节, 通用推荐, 无 BOM 兼容最好。',
				'UTF-8-SIG  带 BOM 形式; 仅在旧软件或需显式区分时使用。',
				'UTF-16/LE/BE  定长/可含 BOM 指示字节序, 英文文件体积偏大。'
			]),
			('中文相关', [
				'GBK  兼容 GB2312, 不覆盖全部新汉字。',
				'GB18030  覆盖所有 Unicode, 向下兼容 GBK (最大覆盖首选)。',
				'Big5  繁体传统编码, 不含简体及部分扩展。'
			]),
			('日/韩', [
				'Shift_JIS  旧日文编码, 可能有二义性。',
				'EUC-JP  日文多字节更规则。',
				'EUC-KR  韩文旧编码。'
			]),
			('西文', [
				'Latin-1(ISO-8859-1)  单字节西欧, 易被误判, 无中文。',
				'cp1252  Windows 西文扩展, 与 Latin-1 接近。'
			]),
			('BOM / 建议', [
				'BOM 在 UTF-8 中可选; 跨平台通常不加。',
				'默认统一: UTF-8(无 BOM)。',
				'最大中文兼容迁移: 先检测/GB18030 -> 转 UTF-8。'
			])
		]
		win = tk.Toplevel(self.root)  # type: ignore
		win.title('编码格式简介')
		win.geometry('620x480')
		import tkinter.font as tkfont
		base = tkfont.nametofont('TkTextFont')
		heading = base.copy(); heading.configure(weight='bold', size=max(base.cget('size')+1, base.cget('size')))
		textw = tk.Text(win, wrap='word', padx=12, pady=8)  # type: ignore
		textw.pack(fill='both', expand=True)
		textw.tag_configure('heading', font=heading, spacing1=6, spacing3=4)
		textw.tag_configure('item', spacing1=2, spacing3=2)
		for title, items in sections:
			textw.insert('end', title + '\n', 'heading')
			for line in items:
				textw.insert('end', '  • ' + line + '\n', 'item')
			textw.insert('end', '\n')
		textw.configure(state='disabled')
		ttk.Button(win, text='关闭', command=win.destroy).pack(pady=6)

	def _show_option_info(self):
		groups = [
			('基本', [
				'递归  遍历子目录; 取消仅处理当前目录/文件。',
				'扩展  仅匹配这些后缀 (逗号)。空=内置文本后缀列表。',
				'包含 / 排除  glob 通配过滤(先包含后排除)。'
			]),
			('编码 / BOM', [
				'源编码(空自动)  自动检测失败再回退常见编码。',
				'目标编码  全部输出统一到此编码。',
				'添加BOM / 移除BOM  不建议同时勾选; UTF-8 默认不加。'
			]),
			('转换逻辑', [
				'跳过相同  编码+BOM 状态一致时不写入。',
				'dry-run  只显示计划, 不改文件 (强烈建议先试)。',
				'覆盖存在  输出目录模式下允许覆盖同名文件。',
				'备份扩展  原地模式首次写入前生成 .bak 等备份。'
			]),
			('错误策略', [
				'strict  出错抛异常 (安全)。',
				'ignore  丢弃非法字节。',
				'replace  用 � 替换非法字节。'
			]),
			('建议流程', [
				'1. dry-run 预览  2. 小批样本实际转换  3. 全量执行。',
				'尽量逐步统一到 UTF-8 无 BOM 便于版本控制。'
			])
		]
		win = tk.Toplevel(self.root)  # type: ignore
		win.title('选项说明')
		win.geometry('640x500')
		import tkinter.font as tkfont
		base = tkfont.nametofont('TkTextFont')
		head = base.copy(); head.configure(weight='bold', size=max(base.cget('size')+1, base.cget('size')))
		txt = tk.Text(win, wrap='word', padx=12, pady=8)  # type: ignore
		txt.pack(fill='both', expand=True)
		txt.tag_configure('heading', font=head, spacing1=8, spacing3=4)
		txt.tag_configure('item', spacing1=1, spacing3=2)
		for title, items in groups:
			txt.insert('end', title + '\n', 'heading')
			for line in items:
				txt.insert('end', '  • ' + line + '\n', 'item')
			txt.insert('end', '\n')
		txt.configure(state='disabled')
		ttk.Button(win, text='关闭', command=win.destroy).pack(pady=6)

	def _toggle_inplace(self):
		self.out_entry.configure(state=('disabled' if self.var_inplace.get() else 'normal'))

	def _start(self):
		if self.worker and self.worker.is_alive():
			messagebox.showinfo('提示','任务正在执行')
			return
		inp = self.var_input.get().strip()
		if not inp:
			messagebox.showwarning('提示','请选择输入')
			return
		if not self.var_inplace.get() and not self.var_output.get().strip():
			messagebox.showwarning('提示','需指定输出目录或勾选原地')
			return
		self.stop_flag.clear()
		if self.log:
			self.log.delete('1.0','end')
		self.progress_var.set(0)
		self.status_var.set('开始...')
		self.worker = threading.Thread(target=self._run_worker, daemon=True)
		self.worker.start()

	def _cancel(self):
		if self.worker and self.worker.is_alive():
			self.stop_flag.set()
			self.status_var.set('取消中...')

	def _run_worker(self):
		try:
			inp = self.var_input.get().strip()
			recursive = self.var_recursive.get()
			exts = []
			if self.var_ext.get().strip():
				exts = [e.strip() if e.strip().startswith('.') else '.'+e.strip() for e in self.var_ext.get().split(',') if e.strip()]
				exts = [e.lower() for e in exts]
			include = [p.strip() for p in self.var_include.get().split(',') if p.strip()]
			exclude = [p.strip() for p in self.var_exclude.get().split(',') if p.strip()]
			files = []
			for pth in iter_files(inp, recursive):
				if os.path.isdir(pth):
					continue
				if exts:
					if os.path.splitext(pth)[1].lower() not in exts:
						continue
				elif os.path.isdir(inp):
					if os.path.splitext(pth)[1].lower() not in TEXT_EXT_GUESS:
						continue
				if not match_filters(pth, include, exclude, exts):
					continue
				files.append(pth)
			total = len(files)
			self.queue.put(f'PROG TOTAL {total}')
			if total == 0:
				self.queue.put('STATUS 无文件')
				return
			from_enc = self.var_from.get().strip() or None
			to_enc = self.var_to.get().strip()
			add_bom = self.var_add_bom.get()
			remove_bom = self.var_remove_bom.get()
			skip_same = self.var_skip_same.get()
			dry_run = self.var_dry.get()
			force = self.var_force.get()
			errors_mode = self.var_errors.get()
			inplace = self.var_inplace.get()
			backup = self.var_backup.get().strip()
			outdir = self.var_output.get().strip()
			converted = skipped = failed = 0
			workers = max(1, self.var_workers.get())
			# 预构建任务
			task_specs: List[Tuple[str,str,str]] = []  # (src,dst,rel)
			for fpath in files:
				if inplace:
					dst = fpath
				else:
					rel = os.path.relpath(fpath, inp) if os.path.isdir(inp) else os.path.basename(fpath)
					dst = os.path.join(outdir, rel)
				relname = os.path.relpath(fpath, inp) if os.path.isdir(inp) else os.path.basename(fpath)
				task_specs.append((fpath, dst, relname))

			prog_lock = threading.Lock()
			done = 0
			def job(spec: Tuple[str,str,str]):
				nonlocal converted, skipped, failed, done
				fpath, dst, relname = spec
				if self.stop_flag.is_set():
					return
				# 覆盖判断
				if (not inplace) and (not force) and os.path.exists(dst) and not dry_run:
					with prog_lock:
						skipped += 1; done += 1
						self.queue.put(f'LOG [SKIP] {relname} :: 目标存在')
						self.queue.put(f'PROG {done} {total}')
					return
				if inplace and backup and not dry_run:
					bak = fpath + backup
					if not os.path.exists(bak):
						try:
							shutil.copy2(fpath, bak)
						except Exception:
							pass
				status, msg, changed = convert_file(fpath, dst, from_enc, to_enc, errors_mode, add_bom, remove_bom, skip_same, dry_run)
				with prog_lock:
					if status == 'OK':
						if changed:
							converted += 1
						else:
							skipped += 1
					elif status == 'SKIP':
						skipped += 1
					else:
						failed += 1
					done += 1
					self.queue.put(f'LOG [{status}] {relname} :: {msg}')
					self.queue.put(f'PROG {done} {total}')

			with ThreadPoolExecutor(max_workers=workers) as ex:
				for spec in task_specs:
					ex.submit(job, spec)
				# 等待线程池结束 (隐式 join upon exit)
			if self.stop_flag.is_set():
				self.queue.put('STATUS 已取消')
				return
			self.queue.put(f'SUM 转换{converted} 跳过{skipped} 失败{failed}')
		except Exception as e:  # pragma: no cover
			self.queue.put(f'STATUS 失败: {e}')

	def _drain_queue(self):
		try:
			while True:
				msg = self.queue.get_nowait()
				if msg.startswith('LOG '):
					if self.log:
						self.log.insert('end', msg[4:] + '\n')
						self.log.see('end')
				elif msg.startswith('PROG TOTAL '):
					try:
						total = int(msg.split()[-1])
						if self.progress:
							self.progress.configure(maximum=total if total>0 else 1)  # type: ignore
						self.progress_var.set(0)
						self.status_var.set(f'总数: {total}')
					except Exception:
						pass
				elif msg.startswith('PROG '):
					try:
						_, cur, total = msg.split()
						cur = int(cur); total = int(total)
						self.progress_var.set(cur)
						pct = int(cur/total*100) if total else 0
						self.status_var.set(f'{pct}% ({cur}/{total})')
					except Exception:
						pass
				elif msg.startswith('STATUS '):
					self.status_var.set(msg[7:])
				elif msg.startswith('SUM '):
					self.status_var.set(msg[4:])
		except queue.Empty:
			pass
		finally:
			self.root.after(120, self._drain_queue)


def launch_gui():  # pragma: no cover (manual)
	if tk is None:
		print('未安装 Tkinter，无法启动 GUI', file=sys.stderr)
		sys.exit(2)
	root = tk.Tk()
	GUIApp(root)
	root.mainloop()


if __name__ == '__main__':
	if len(sys.argv) == 1:
		launch_gui()
	else:
		sys.exit(main(sys.argv[1:]))
