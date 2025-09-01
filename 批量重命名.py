import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from dataclasses import dataclass
from typing import List, Optional, Tuple

# ================= 工具函数 =================

def format_sequence(idx: int, fmt: str) -> str:
	"""根据格式字符串生成序号文本。
	fmt 取值:
	  1   -> 1,2,3
	  01  -> 01,02,03
	  001 -> 001,002,003
	  A   -> A,B,...,Z,AA,AB,... (26 进制)
	  a   -> a,b,...
	  I   -> 罗马数字 (大写)
	  i   -> 罗马数字 (小写)
	"""
	n = idx
	if fmt == '1':
		return str(n)
	if fmt == '01':
		return f"{n:02d}"
	if fmt == '001':
		return f"{n:03d}"
	if fmt in ('A', 'a'):
		# Excel 列号式
		letters = []
		x = n
		while x > 0:
			x -= 1
			x, r = divmod(x, 26)
			letters.append(chr(ord('A') + r))
		s = ''.join(reversed(letters))
		return s if fmt == 'A' else s.lower()
	if fmt in ('I', 'i'):
		return to_roman(n, lower=(fmt == 'i'))
	return str(n)

def to_roman(num: int, lower: bool = False) -> str:
	if num <= 0:
		return str(num)
	vals = [
		(1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'),
		(100, 'C'), (90, 'XC'), (50, 'L'), (40, 'XL'),
		(10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')
	]
	res = []
	n = num
	for v, sym in vals:
		while n >= v:
			res.append(sym)
			n -= v
	s = ''.join(res)
	return s.lower() if lower else s

@dataclass
class RenameItem:
	path: str
	is_dir: bool
	original_name: str  # 含扩展
	base: str           # 不含扩展
	ext: str            # 不含点

@dataclass
class PreviewResult:
	item: RenameItem
	new_name: str
	new_path: str
	conflict: bool
	skip: bool

# ================= 主类 =================
class BatchRenamerApp:
	def __init__(self, root: tk.Tk):
		self.root = root
		self.root.title("批量重命名工具")
		self.root.geometry("880x560")

		self.dir_var = tk.StringVar()
		self.pattern_var = tk.StringVar(value="文件_{num}")
		self.start_var = tk.IntVar(value=1)
		self.step_var = tk.IntVar(value=1)
		self.seq_fmt_var = tk.StringVar(value='001')
		self.position_var = tk.StringVar(value='pattern')  # pattern / prefix / suffix
		self.include_files = tk.BooleanVar(value=True)
		self.include_dirs = tk.BooleanVar(value=False)
		self.recursive_var = tk.BooleanVar(value=False)
		self.keep_ext_var = tk.BooleanVar(value=True)
		self.conflict_strategy_var = tk.StringVar(value='stop')  # stop / skip

		self.preview: List[PreviewResult] = []

		self._build_ui()

	# ---------- UI ----------
	def _build_ui(self):
		frm = ttk.Frame(self.root, padding=10)
		frm.pack(fill=tk.BOTH, expand=True)

		# 目录选择
		row = 0
		ttk.Label(frm, text="目标目录:").grid(row=row, column=0, sticky='w')
		ttk.Entry(frm, textvariable=self.dir_var, width=60).grid(row=row, column=1, sticky='we', padx=4)
		ttk.Button(frm, text="选择...", command=self.select_dir).grid(row=row, column=2, padx=2)
		ttk.Button(frm, text="刷新", command=self.generate_preview).grid(row=row, column=3, padx=2)
		frm.columnconfigure(1, weight=1)

		# 选项
		row += 1
		opt_frame = ttk.LabelFrame(frm, text="选项")
		opt_frame.grid(row=row, column=0, columnspan=4, sticky='we', pady=6)
		for i in range(8):
			opt_frame.columnconfigure(i, weight=0)
		opt_frame.columnconfigure(7, weight=1)

		ttk.Checkbutton(opt_frame, text="包含文件", variable=self.include_files, command=self.generate_preview).grid(row=0, column=0, sticky='w')
		ttk.Checkbutton(opt_frame, text="包含文件夹", variable=self.include_dirs, command=self.generate_preview).grid(row=0, column=1, sticky='w')
		ttk.Checkbutton(opt_frame, text="递归", variable=self.recursive_var, command=self.generate_preview).grid(row=0, column=2, sticky='w')
		ttk.Checkbutton(opt_frame, text="保留原扩展", variable=self.keep_ext_var, command=self.generate_preview).grid(row=0, column=3, sticky='w')

		ttk.Label(opt_frame, text="序号格式:").grid(row=1, column=0, sticky='e')
		ttk.Combobox(opt_frame, textvariable=self.seq_fmt_var, values=['1','01','001','A','a','I','i'], width=5, state='readonly').grid(row=1, column=1, sticky='w')
		ttk.Label(opt_frame, text="起始:").grid(row=1, column=2, sticky='e')
		ttk.Spinbox(opt_frame, from_=1, to=999999, textvariable=self.start_var, width=6).grid(row=1, column=3, sticky='w')
		ttk.Label(opt_frame, text="步长:").grid(row=1, column=4, sticky='e')
		ttk.Spinbox(opt_frame, from_=1, to=9999, textvariable=self.step_var, width=5).grid(row=1, column=5, sticky='w')

		ttk.Label(opt_frame, text="模式:").grid(row=2, column=0, sticky='e')
		ttk.Entry(opt_frame, textvariable=self.pattern_var, width=30).grid(row=2, column=1, columnspan=3, sticky='we', padx=2)
		ttk.Label(opt_frame, text="位置:").grid(row=2, column=4, sticky='e')
		ttk.Combobox(opt_frame, textvariable=self.position_var, values=['pattern','prefix','suffix'], width=8, state='readonly').grid(row=2, column=5, sticky='w')

		ttk.Label(opt_frame, text="冲突:").grid(row=2, column=6, sticky='e')
		ttk.Combobox(opt_frame, textvariable=self.conflict_strategy_var, values=['stop','skip'], width=6, state='readonly').grid(row=2, column=7, sticky='w')

		ttk.Label(opt_frame, text="占位符: {num} 序号, {original} 原名(不含扩展), {ext} 原扩展").grid(row=3, column=0, columnspan=8, sticky='w', pady=2)

		# 操作按钮
		row += 1
		btn_frame = ttk.Frame(frm)
		btn_frame.grid(row=row, column=0, columnspan=4, sticky='we', pady=4)
		ttk.Button(btn_frame, text="生成预览", command=self.generate_preview).pack(side=tk.LEFT, padx=4)
		ttk.Button(btn_frame, text="执行重命名", command=self.do_rename).pack(side=tk.LEFT, padx=4)

		# 预览表
		row += 1
		columns = ('original','new','status')
		self.tree = ttk.Treeview(frm, columns=columns, show='headings', height=18)
		self.tree.grid(row=row, column=0, columnspan=4, sticky='nsew')
		frm.rowconfigure(row, weight=1)
		self.tree.heading('original', text='原名称')
		self.tree.heading('new', text='新名称')
		self.tree.heading('status', text='状态')
		self.tree.column('original', width=260)
		self.tree.column('new', width=260)
		self.tree.column('status', width=120)

		vsb = ttk.Scrollbar(frm, orient='vertical', command=self.tree.yview)
		vsb.grid(row=row, column=4, sticky='ns')
		self.tree.configure(yscrollcommand=vsb.set)

		# 底部状态
		row += 1
		self.status_var = tk.StringVar(value='准备就绪')
		ttk.Label(frm, textvariable=self.status_var, anchor='w').grid(row=row, column=0, columnspan=4, sticky='we', pady=4)

	# ---------- 事件 ----------
	def select_dir(self):
		path = filedialog.askdirectory()
		if path:
			self.dir_var.set(path)
			self.generate_preview()

	def gather_items(self) -> List[RenameItem]:
		root_dir = self.dir_var.get().strip()
		items: List[RenameItem] = []
		if not root_dir or not os.path.isdir(root_dir):
			return items
		include_files = self.include_files.get()
		include_dirs = self.include_dirs.get()
		recursive = self.recursive_var.get()

		def handle_dir(cur):
			for name in os.listdir(cur):
				full = os.path.join(cur, name)
				is_dir = os.path.isdir(full)
				if (is_dir and include_dirs) or (not is_dir and include_files):
					base, ext = os.path.splitext(name)
					if ext.startswith('.'):
						ext = ext[1:]
					items.append(RenameItem(path=full, is_dir=is_dir, original_name=name, base=base, ext=ext))
				if is_dir and recursive:
					handle_dir(full)
		handle_dir(root_dir)
		# 为稳定性排序：目录深度 + 原名称
		items.sort(key=lambda r: (r.is_dir, r.original_name.lower()))
		return items

	def build_new_name(self, item: RenameItem, seq_text: str) -> str:
		pattern = self.pattern_var.get()
		keep_ext = self.keep_ext_var.get()
		position = self.position_var.get()
		base = item.base
		ext = item.ext
		# 判断是否使用 pattern 占位
		has_placeholder = any(x in pattern for x in ('{num}','{original}','{ext}'))
		new_base = ''
		if has_placeholder:
			new_base = pattern.replace('{num}', seq_text).replace('{original}', base).replace('{ext}', ext)
		else:
			# 未使用占位，根据位置模式组装
			if position == 'prefix':
				new_base = f"{seq_text}_{pattern}" if pattern else seq_text
			elif position == 'suffix':
				new_base = f"{pattern}_{seq_text}" if pattern else seq_text
			else:  # pattern 模式但无占位 => 附加序号到末尾
				new_base = f"{pattern}_{seq_text}" if pattern else seq_text
		# 处理扩展
		if item.is_dir:
			return new_base
		# 如果 pattern 中已经显式包含扩展 (有点 或 {ext}) 则不再追加
		if has_placeholder and ('{ext}' in pattern or '.' in os.path.basename(pattern)):
			return new_base
		if keep_ext and ext:
			return f"{new_base}.{ext}"
		return new_base

	def generate_preview(self):
		self.tree.delete(*self.tree.get_children())
		items = self.gather_items()
		if not items:
			self.status_var.set('未找到项目或目录无效')
			self.preview = []
			return
		seq_fmt = self.seq_fmt_var.get()
		start = self.start_var.get()
		step = self.step_var.get()
		cur = start
		seen_new_names = set()
		root_dir = self.dir_var.get().strip()
		results: List[PreviewResult] = []
		for item in items:
			seq_text = format_sequence(cur, seq_fmt)
			new_name = self.build_new_name(item, seq_text)
			cur += step
			new_path = os.path.join(os.path.dirname(item.path), new_name)
			conflict = False
			skip = False
			# 冲突判定：目标名重复 或 已存在不同路径
			if new_name in seen_new_names:
				conflict = True
			elif os.path.exists(new_path) and os.path.abspath(new_path) != os.path.abspath(item.path):
				conflict = True
			if conflict and self.conflict_strategy_var.get() == 'skip':
				skip = True
			seen_new_names.add(new_name)
			results.append(PreviewResult(item=item, new_name=new_name, new_path=new_path, conflict=conflict, skip=skip))
		self.preview = results
		conflicts = sum(1 for r in results if r.conflict)
		skips = sum(1 for r in results if r.skip)
		for r in results:
			status_parts = []
			if r.conflict:
				status_parts.append('冲突')
			if r.skip:
				status_parts.append('跳过')
			status = '/'.join(status_parts) if status_parts else 'OK'
			self.tree.insert('', tk.END, values=(r.item.original_name, r.new_name, status))
		self.status_var.set(f'共 {len(results)} 项, 冲突 {conflicts}, 将跳过 {skips}')

	def do_rename(self):
		if not self.preview:
			messagebox.showwarning('提示', '请先生成预览')
			return
		conflicts = [r for r in self.preview if r.conflict and not r.skip]
		if conflicts:
			messagebox.showerror('错误', f'存在 {len(conflicts)} 个未解决的命名冲突 (策略=停止)。可改策略为 skip 或修改参数。')
			return
		count_total = 0
		count_done = 0
		errors: List[Tuple[str,str]] = []
		for r in self.preview:
			count_total += 1
			if r.skip:
				continue
			if os.path.abspath(r.item.path) == os.path.abspath(r.new_path):
				continue  # 名称未变
			try:
				os.rename(r.item.path, r.new_path)
				count_done += 1
			except Exception as e:
				errors.append((r.item.original_name, str(e)))
		if errors:
			msg = f'完成 {count_done}/{count_total}，失败 {len(errors)}\n' + '\n'.join(f'{n}: {e}' for n,e in errors[:10])
			messagebox.showerror('结果', msg)
		else:
			messagebox.showinfo('结果', f'成功重命名 {count_done} 项')
		self.generate_preview()  # 刷新

# ================= 入口 =================

def run_gui():
	root = tk.Tk()
	app = BatchRenamerApp(root)
	root.mainloop()

if __name__ == '__main__':
	run_gui()
