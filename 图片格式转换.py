"""重写版图片格式转换工具

增强点:
1. 更丰富 UI: Notebook 分单文件/批处理，进度条 & 日志输出，质量滑块，ICO 尺寸全选/反选。
2. 可自定义批量输出命名模式: {name} 原文件名(无扩展), {index} 序号(起始/步长), {ext} 原扩展。
3. 递归处理、覆盖策略(覆盖/跳过/改名追加 _newN)、同格式是否强制重新保存。
4. 针对 jpg/webp/png 的质量可调；PNG 质量映射压缩级别；GIF 动画 -> WebP 动画 / APNG；ICO 多尺寸。
5. CLI 向下兼容并新增 pattern / recursive / overwrite-mode。
"""

from __future__ import annotations
import argparse
import os
import sys
import threading
import queue
from dataclasses import dataclass
from typing import List, Optional, Tuple, Iterable
from tkinter import Tk, Toplevel, StringVar, IntVar, BooleanVar, END
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk, ImageSequence

# ---------- 常量/兼容 ----------
SUPPORTED_INPUT_EXT = ('.png', '.jpg', '.jpeg', '.webp', '.ico', '.gif')

try:
    RESAMPLE = Image.Resampling.LANCZOS
except Exception:  # Pillow 旧版本兼容
    RESAMPLE = getattr(Image, 'LANCZOS', getattr(Image, 'ANTIALIAS', 1))


# ---------- 转换核心 ----------
def map_png_quality_to_level(q: int) -> int:
    if q >= 80:
        return 2
    if q >= 40:
        return 4
    return 6


def normalize_ext(p: str) -> str:
    ext = os.path.splitext(p)[1].lower().lstrip('.')
    if ext in ('jpg', 'jpeg'):
        return 'jpg'
    return ext


def ensure_dir(path: str):
    if path and not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


def iter_image_files(root: str, recursive: bool) -> Iterable[str]:
    if os.path.isfile(root):
        if root.lower().endswith(SUPPORTED_INPUT_EXT):
            yield root
        return
    for base, dirs, files in os.walk(root):
        for f in files:
            if f.lower().endswith(SUPPORTED_INPUT_EXT):
                yield os.path.join(base, f)
        if not recursive:
            break


def build_output_filename(pattern: str, src_path: str, index: int, target_fmt: str) -> str:
    base = os.path.splitext(os.path.basename(src_path))[0]
    ext = normalize_ext(src_path)
    name = pattern.replace('{name}', base).replace('{index}', str(index)).replace('{ext}', ext)
    if '.' not in os.path.basename(name):  # 未指定扩展
        name += f'.{target_fmt}'
    return name


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


def convert_one(input_path: str, output_file: str, target_fmt: str,
                ico_sizes: Optional[List[int]] = None,
                quality: Optional[int] = None,
                png3: bool = False) -> Tuple[bool, str]:
    try:
        with Image.open(input_path) as img:
            original_format = (img.format or '').upper()
            is_animated = getattr(img, 'is_animated', False)
            fmt = target_fmt.lower()
            save_format = 'JPEG' if fmt == 'jpg' else fmt.upper()
            params = {}

            # ICO 准备
            if fmt == 'ico':
                if img.mode not in ('RGBA', 'RGB'):
                    img = img.convert('RGBA')
                if not ico_sizes:
                    ico_sizes = [256, 128, 64, 48, 32, 16]
                ico_sizes = sorted({int(s) for s in ico_sizes if 0 < int(s) <= 1024}, reverse=True)
                params['sizes'] = [(s, s) for s in ico_sizes]
            elif fmt == 'jpg':
                if img.mode in ('RGBA', 'LA'):
                    img = img.convert('RGB')
                if quality is None:
                    quality = 85
                params['quality'] = max(1, min(int(quality), 100))
                params['optimize'] = True
                if params['quality'] >= 92:
                    params['subsampling'] = 0
            elif fmt == 'webp':
                if quality is None:
                    quality = 80
                params['quality'] = max(1, min(int(quality), 100))
            elif fmt == 'png':
                if quality is None:
                    quality = 100
                qv = max(1, min(int(quality), 100))
                # 基础压缩映射
                comp_level = map_png_quality_to_level(qv)
                if png3:
                    # 3.0 规范: 更高压缩/优化
                    comp_level = 9
                    params['optimize'] = True
                    # 推荐颜色管理块
                    try:
                        img.info['gamma'] = 0.45455  # 1/2.2
                        img.info['srgb'] = 0  # perceptual rendering intent
                    except Exception:
                        pass
                else:
                    params['optimize'] = True
                params['compress_level'] = comp_level

            # GIF 动画 -> WebP / APNG / 首帧
            if original_format == 'GIF' and is_animated and fmt in ('webp', 'png', 'jpg'):
                frames = []
                durations = []
                for fr in ImageSequence.Iterator(img):
                    frames.append(fr.convert('RGBA'))
                    durations.append(fr.info.get('duration', 100))
                if fmt == 'webp':
                    frames[0].save(output_file, format='WEBP', save_all=True, append_images=frames[1:], loop=0, duration=durations, quality=params.get('quality', 80))
                    return True, 'WebP动画'
                if fmt == 'png':
                    if png3:  # 仅在启用 3.0 规范时尝试 APNG
                        try:
                            frames[0].save(output_file, format='PNG', save_all=True, append_images=frames[1:], loop=0, duration=durations, disposal=2)
                            return True, 'APNG'
                        except Exception:
                            frames[0].save(output_file, format='PNG', **params)
                            return True, 'APNG失败回退首帧'
                    else:
                        frames[0].save(output_file, format='PNG', **params)
                        return True, '首帧'
                if fmt == 'jpg':
                    frames[0].convert('RGB').save(output_file, format='JPEG', **params)
                    return True, '首帧'

            img.save(output_file, format=save_format, **params)
            return True, '成功'
    except Exception as e:
        return False, str(e)


# ---------- CLI ----------
def run_cli():
    parser = argparse.ArgumentParser(description='图片格式转换工具 (增强版)')
    parser.add_argument('-i', '--input', required=True, help='输入文件或目录')
    parser.add_argument('-o', '--output', required=True, help='输出文件或目录')
    parser.add_argument('-f', '--format', required=True, choices=['jpg', 'png', 'webp', 'ico'], help='目标格式')
    parser.add_argument('--ico-sizes', help='ICO 尺寸逗号分隔，如 16,32,64,256')
    parser.add_argument('--process-same', action='store_true', help='源与目标格式相同也重新保存')
    parser.add_argument('--quality', type=int, help='质量(1-100)')
    parser.add_argument('--recursive', action='store_true', help='递归处理子目录')
    parser.add_argument('--pattern', default='{name}.{fmt}', help='批量命名模式，支持 {name} {index} {ext} {fmt}')
    parser.add_argument('--start', type=int, default=1, help='序号起始 (pattern 有 {index} 时)')
    parser.add_argument('--step', type=int, default=1, help='序号步长 (pattern 有 {index} 时)')
    parser.add_argument('--overwrite-mode', choices=['overwrite', 'skip', 'rename'], default='overwrite', help='存在同名文件处理策略')
    parser.add_argument('--png3', action='store_true', help='PNG 3.0 规范: 高压缩+颜色块+APNG')
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print('错误：输入不存在')
        return

    ico_sizes = None
    if args.format == 'ico':
        if args.ico_sizes:
            try:
                ico_sizes = [int(x) for x in args.ico_sizes.split(',') if x.strip()]
                ico_sizes = [s for s in ico_sizes if s > 0]
            except Exception:
                print('错误：--ico-sizes 无效')
                return
        if not ico_sizes:
            ico_sizes = [256, 128, 64, 48, 32, 16]

    files = list(iter_image_files(args.input, args.recursive))
    if not files:
        print('未找到可处理文件')
        return

    # 输出路径判定
    out_is_dir = (not os.path.splitext(args.output)[1]) or os.path.isdir(args.output) or len(files) > 1 or os.path.isdir(args.input)
    if out_is_dir:
        ensure_dir(args.output)

    index = args.start
    converted = skipped = failed = 0
    for f in files:
        src_ext = normalize_ext(f)
        if src_ext == args.format and not args.process_same:
            skipped += 1
            continue
        if out_is_dir:
            pat = args.pattern.replace('{fmt}', args.format)
            name = pat
            if '{index}' in pat:
                name = name.replace('{index}', str(index))
            name = (name
                    .replace('{name}', os.path.splitext(os.path.basename(f))[0])
                    .replace('{ext}', src_ext))
            if '.' not in os.path.basename(name):
                name += f'.{args.format}'
            out_file = os.path.join(args.output, name)
        else:
            out_file = args.output

        if os.path.exists(out_file):
            if args.overwrite_mode == 'skip':
                skipped += 1
                index += args.step
                continue
            if args.overwrite_mode == 'rename':
                out_file = next_non_conflict(out_file)

        ok, msg = convert_one(f, out_file, args.format, ico_sizes=ico_sizes, quality=args.quality, png3=args.png3 if args.format == 'png' else False)
        if ok:
            converted += 1
        else:
            failed += 1
        index += args.step
        print(f"{os.path.basename(f)} -> {os.path.basename(out_file)}: {msg}")

    print(f'完成：转换{converted} 跳过{skipped} 失败{failed}')


# ---------- GUI ----------
class ImageConverterApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title('图片格式转换器 (增强版)')
        self.root.geometry('920x600')

        # 单/目录模式变量
        self.single_input = StringVar()
        self.single_output = StringVar()
        self.single_format = StringVar(value='png')
        self.process_same_var = BooleanVar(value=False)
        self.quality_var = IntVar(value=85)
        self.png3_var = BooleanVar(value=False)
        self.ico_size_vars = {}

        # 批处理变量
        self.batch_input = StringVar()
        self.batch_output = StringVar()
        self.batch_format = StringVar(value='png')
        self.batch_recursive = BooleanVar(value=False)
        self.batch_pattern = StringVar(value='{name}_{index}.{fmt}')
        self.batch_start = IntVar(value=1)
        self.batch_step = IntVar(value=1)
        self.batch_overwrite = StringVar(value='overwrite')

        # 队列与线程控制
        self.queue = queue.Queue()
        self.stop_flag = threading.Event()            # 批处理停止标志
        self.worker = None
        self.single_worker = None  # 单标签目录线程
        self.single_stop_flag = threading.Event()

        self._build_ui()
        self.root.after(120, self._drain_queue)

    # UI 构建
    def _build_ui(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill='both', expand=True)

        frm_single = ttk.Frame(nb, padding=12)
        frm_batch = ttk.Frame(nb, padding=12)
        nb.add(frm_single, text='单文件 / 目录')
        nb.add(frm_batch, text='批处理')

        # --- 单文件 ---
        self._build_single_tab(frm_single)
        # --- 批处理 ---
        self._build_batch_tab(frm_batch)

    def _build_single_tab(self, parent):
        g = parent
        for i in range(6):
            g.columnconfigure(i, weight=1)
        row = 0
        ttk.Label(g, text='输入(文件或目录):').grid(row=row, column=0, sticky='w')
        ttk.Entry(g, textvariable=self.single_input, width=52).grid(row=row, column=1, columnspan=3, sticky='we', padx=4)
        ttk.Button(g, text='选择目录', command=self._pick_single_dir).grid(row=row, column=4, padx=2)
        ttk.Button(g, text='选择文件', command=self._pick_single_file).grid(row=row, column=5, padx=2)
        row += 1

        ttk.Label(g, text='输出路径:').grid(row=row, column=0, sticky='w')
        ttk.Entry(g, textvariable=self.single_output, width=52).grid(row=row, column=1, columnspan=4, sticky='we', padx=4)
        ttk.Button(g, text='浏览', command=self._pick_single_output).grid(row=row, column=5, padx=2)
        row += 1

        ttk.Label(g, text='目标格式:').grid(row=row, column=0, sticky='w')
        fmt_cb = ttk.Combobox(g, textvariable=self.single_format, values=['jpg','png','webp','ico'], state='readonly', width=8)
        fmt_cb.grid(row=row, column=1, sticky='w')
        fmt_cb.bind('<<ComboboxSelected>>', lambda e: self._refresh_ico_frame())
        ttk.Checkbutton(g, text='同格式也重新保存', variable=self.process_same_var).grid(row=row, column=2, columnspan=2, sticky='w')
        ttk.Label(g, text='质量:').grid(row=row, column=4, sticky='e')
        ttk.Scale(g, from_=1, to=100, orient='horizontal', variable=self.quality_var).grid(row=row, column=5, sticky='we', padx=4)
        row += 1
        ttk.Checkbutton(g, text='PNG 3.0 规范(高压缩+颜色块+APNG)', variable=self.png3_var).grid(row=row, column=0, columnspan=6, sticky='w')
        row += 1
        # ICO 尺寸
        self.ico_frame = ttk.LabelFrame(g, text='ICO 尺寸')
        for i, s in enumerate([16,32,48,64,128,256]):
            var = IntVar(value=1 if s in (16,32,48,256) else 0)
            self.ico_size_vars[s] = var
            ttk.Checkbutton(self.ico_frame, text=str(s), variable=var).grid(row=0, column=i, padx=3, pady=2, sticky='w')
        ttk.Button(self.ico_frame, text='全选', command=lambda: self._set_all_ico(True)).grid(row=1, column=0, padx=3, pady=2, sticky='w')
        ttk.Button(self.ico_frame, text='全不选', command=lambda: self._set_all_ico(False)).grid(row=1, column=1, padx=3, pady=2, sticky='w')
        self.ico_frame.grid(row=row, column=0, columnspan=6, sticky='w', pady=4)
        row += 1

        self.preview_label = ttk.Label(g, text='(预览)')
        self.preview_label.grid(row=row, column=0, columnspan=6, pady=8)
        row += 1

        self.btn_single_convert = ttk.Button(g, text='开始转换', command=self._convert_single)
        self.btn_single_convert.grid(row=row, column=2, pady=6)
        ttk.Button(g, text='刷新预览', command=self._update_preview).grid(row=row, column=3, pady=6)
        row += 1

        self.single_progress = ttk.Progressbar(g, maximum=100)
        self.single_progress.grid(row=row, column=0, columnspan=6, sticky='we', pady=4)
        row += 1

        self.single_status = StringVar(value='就绪')
        ttk.Label(g, textvariable=self.single_status, foreground='blue').grid(row=row, column=0, columnspan=6, sticky='w')
        self._refresh_ico_frame()
        self.single_input.trace_add('write', lambda *a: self._auto_output_single())

    def _build_batch_tab(self, parent):
        g = parent
        for i in range(8):
            g.columnconfigure(i, weight=1)
        row = 0
        ttk.Label(g, text='输入目录:').grid(row=row, column=0, sticky='w')
        ttk.Entry(g, textvariable=self.batch_input, width=50).grid(row=row, column=1, columnspan=5, sticky='we', padx=4)
        ttk.Button(g, text='选择', command=self._pick_batch_dir).grid(row=row, column=6, padx=2)
        ttk.Checkbutton(g, text='递归', variable=self.batch_recursive).grid(row=row, column=7, sticky='w')
        row += 1

        ttk.Label(g, text='输出目录:').grid(row=row, column=0, sticky='w')
        ttk.Entry(g, textvariable=self.batch_output, width=50).grid(row=row, column=1, columnspan=5, sticky='we', padx=4)
        ttk.Button(g, text='选择', command=self._pick_batch_output).grid(row=row, column=6, padx=2)
        row += 1

        ttk.Label(g, text='目标格式:').grid(row=row, column=0, sticky='w')
        fmt_cb = ttk.Combobox(g, textvariable=self.batch_format, values=['jpg','png','webp','ico'], width=8, state='readonly')
        fmt_cb.grid(row=row, column=1, sticky='w')
        fmt_cb.bind('<<ComboboxSelected>>', lambda e: self._refresh_batch_ico())
        ttk.Checkbutton(g, text='同格式也重新保存', variable=self.process_same_var).grid(row=row, column=2, columnspan=2, sticky='w')
        ttk.Label(g, text='质量:').grid(row=row, column=4, sticky='e')
        ttk.Scale(g, from_=1, to=100, orient='horizontal', variable=self.quality_var).grid(row=row, column=5, sticky='we', padx=4)
        row += 1
        ttk.Checkbutton(g, text='PNG 3.0 规范(高压缩+颜色块+APNG)', variable=self.png3_var).grid(row=row, column=0, columnspan=8, sticky='w')
        row += 1

        ttk.Label(g, text='命名模式:').grid(row=row, column=0, sticky='e')
        ttk.Entry(g, textvariable=self.batch_pattern, width=34).grid(row=row, column=1, columnspan=2, sticky='we', padx=4)
        ttk.Label(g, text='起始/步长:').grid(row=row, column=3, sticky='e')
        ttk.Spinbox(g, from_=1, to=999999, textvariable=self.batch_start, width=6).grid(row=row, column=4, sticky='w')
        ttk.Spinbox(g, from_=1, to=9999, textvariable=self.batch_step, width=5).grid(row=row, column=5, sticky='w')
        ttk.Label(g, text='覆盖策略:').grid(row=row, column=6, sticky='e')
        ttk.Combobox(g, textvariable=self.batch_overwrite, values=['overwrite','skip','rename'], width=8, state='readonly').grid(row=row, column=7, sticky='w')
        row += 1

        self.batch_ico_frame = ttk.LabelFrame(g, text='ICO 尺寸')
        self.batch_ico_vars = {}
        for i, s in enumerate([16,32,48,64,128,256]):
            v = IntVar(value=1 if s in (16,32,48,256) else 0)
            self.batch_ico_vars[s] = v
            ttk.Checkbutton(self.batch_ico_frame, text=str(s), variable=v).grid(row=0, column=i, padx=3, pady=2)
        ttk.Button(self.batch_ico_frame, text='全选', command=lambda: self._set_all_batch_ico(True)).grid(row=1, column=0, padx=3, sticky='w')
        ttk.Button(self.batch_ico_frame, text='全不选', command=lambda: self._set_all_batch_ico(False)).grid(row=1, column=1, padx=3, sticky='w')
        self.batch_ico_frame.grid(row=row, column=0, columnspan=8, sticky='w', pady=4)
        row += 1

        self.progress = ttk.Progressbar(g, maximum=100)
        self.progress.grid(row=row, column=0, columnspan=8, sticky='we', pady=4)
        row += 1

        self.batch_progress_text = StringVar(value='0% (0/0)')
        self.current_file_text = StringVar(value='当前: -')
        ttk.Label(g, textvariable=self.batch_progress_text, foreground='green').grid(row=row, column=0, columnspan=2, sticky='w')
        ttk.Label(g, textvariable=self.current_file_text, foreground='purple').grid(row=row, column=2, columnspan=6, sticky='w')
        row += 1

        self.log = ttk.Treeview(g, columns=('msg',), show='headings', height=14)
        self.log.heading('msg', text='日志 (文件 -> 结果)')
        self.log.column('msg', anchor='w', width=840)
        self.log.grid(row=row, column=0, columnspan=8, sticky='nsew')
        g.rowconfigure(row, weight=1)
        row += 1

        btn_frame = ttk.Frame(g)
        btn_frame.grid(row=row, column=0, columnspan=8, sticky='we', pady=4)
        ttk.Button(btn_frame, text='开始批量', command=self._start_batch).pack(side='left', padx=4)
        ttk.Button(btn_frame, text='取消', command=self._cancel_batch).pack(side='left', padx=4)
        ttk.Button(btn_frame, text='清空日志', command=lambda: self.log.delete(*self.log.get_children())).pack(side='left', padx=4)
        ttk.Label(btn_frame, text='占位符: {name} {index} {ext} {fmt}').pack(side='right')

        self.batch_status = StringVar(value='就绪')
        ttk.Label(g, textvariable=self.batch_status, foreground='blue').grid(row=row+1, column=0, columnspan=8, sticky='w')
        self._refresh_batch_ico()

    # --- 单文件事件 ---
    def _pick_single_file(self):
        p = filedialog.askopenfilename(filetypes=[("图片文件", ' '.join(SUPPORTED_INPUT_EXT))])
        if p:
            self.single_input.set(p)
            self._auto_output_single()
            self._update_preview()

    def _pick_single_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.single_input.set(d)
            self._auto_output_single()
            self.preview_label.configure(image='', text='(目录)')

    def _pick_single_output(self):
        inp = self.single_input.get().strip()
        # 目录输入 -> 选择目录，不自动加扩展
        if inp and os.path.isdir(inp):
            d = filedialog.askdirectory()
            if d:
                self.single_output.set(d)
        else:
            fmt = self.single_format.get()
            p = filedialog.asksaveasfilename(defaultextension=f'.{fmt}', filetypes=[("图片文件", ' '.join(SUPPORTED_INPUT_EXT))])
            if p:
                self.single_output.set(p)

    def _auto_output_single(self):
        inp = self.single_input.get()
        if os.path.isfile(inp):
            base = os.path.splitext(os.path.basename(inp))[0]
            fmt = self.single_format.get()
            self.single_output.set(os.path.join(os.path.dirname(inp), f'{base}.{fmt}'))

    def _refresh_ico_frame(self):
        if self.single_format.get() == 'ico':
            self.ico_frame.grid()
        else:
            self.ico_frame.grid_remove()
        # 质量禁用逻辑
        pass

    def _set_all_ico(self, val: bool):
        for v in self.ico_size_vars.values():
            v.set(1 if val else 0)

    def _update_preview(self):
        p = self.single_input.get()
        if not os.path.isfile(p):
            return
        try:
            with Image.open(p) as img:
                w, h = img.size
                max_len = 420
                scale = min(max_len / w, max_len / h, 1)
                if scale < 1:
                    img = img.resize((int(w*scale), int(h*scale)), RESAMPLE)
                photo = ImageTk.PhotoImage(img)
                self.preview_label.configure(image=photo, text='')
                self._preview_photo = photo  # 保持引用
        except Exception as e:
            self.single_status.set(f'预览失败: {e}')

    def _convert_single(self):
        inp = self.single_input.get().strip()
        outp = self.single_output.get().strip()
        fmt = self.single_format.get()
        if not inp or not outp:
            self.single_status.set('请输入输入与输出')
            return
        if not os.path.exists(inp):
            self.single_status.set('输入不存在')
            return
        ico_sizes = None
        if fmt == 'ico':
            ico_sizes = [s for s, v in self.ico_size_vars.items() if v.get()]
            if not ico_sizes:
                self.single_status.set('请选择 ICO 尺寸')
                return
        process_same = self.process_same_var.get()
        quality = self.quality_var.get() if fmt in ('jpg','png','webp') else None

        # 单文件 vs 目录
        if os.path.isfile(inp):
            if normalize_ext(inp) == fmt and not process_same:
                self.single_status.set('跳过(同格式)')
                return
            # 输出若为目录
            if os.path.isdir(outp) or not os.path.splitext(outp)[1]:
                ensure_dir(outp)
                out_file = os.path.join(outp, f"{os.path.splitext(os.path.basename(inp))[0]}.{fmt}")
            else:
                out_dir = os.path.dirname(outp)
                if out_dir:
                    ensure_dir(out_dir)
                out_file = outp
            ok, msg = convert_one(inp, out_file, fmt, ico_sizes=ico_sizes, quality=quality, png3=self.png3_var.get() if fmt=='png' else False)
            self.single_status.set('成功' if ok else f'失败:{msg}')
        else:
            if self.single_worker and self.single_worker.is_alive():
                self.single_stop_flag.set()
                self.single_status.set('取消中...')
                return
            files = list(iter_image_files(inp, recursive=False))
            if not files:
                self.single_status.set('目录为空')
                return
            # 如果输出看起来像被错误添加了扩展（目录模式不应带格式后缀），尝试去掉
            outp_candidate = outp
            ext = os.path.splitext(outp_candidate)[1].lower()
            if ext in ('.jpg', '.jpeg', '.png', '.webp', '.ico') and not os.path.isdir(outp_candidate):
                trimmed = os.path.splitext(outp_candidate)[0]
                if trimmed and not os.path.splitext(trimmed)[1]:  # 去掉一次扩展后不再有扩展
                    outp_candidate = trimmed
            outp = outp_candidate
            ensure_dir(outp)
            self.single_progress['value'] = 0
            self.single_progress['maximum'] = len(files)
            self.single_status.set('开始...')
            self.single_stop_flag.clear()
            self.btn_single_convert.configure(text='取消')

            def run_dir():
                converted = skipped = failed = 0
                total = len(files)
                processed = 0
                for f in files:
                    if self.single_stop_flag.is_set():
                        break
                    if normalize_ext(f) == fmt and not process_same:
                        skipped += 1
                        ok_flag = True
                    else:
                        target = os.path.join(outp, f"{os.path.splitext(os.path.basename(f))[0]}.{fmt}")
                        ok_flag, _ = convert_one(f, target, fmt, ico_sizes=ico_sizes, quality=quality, png3=self.png3_var.get() if fmt=='png' else False)
                        if ok_flag:
                            converted += 1
                        else:
                            failed += 1
                    processed += 1
                    pct = int(processed / total * 100)
                    self.queue.put(f"SINGLEPROG\t{processed}\t{total}\t{pct}\t{os.path.basename(f)}")
                if self.single_stop_flag.is_set():
                    self.queue.put("SINGLESUM\t已取消")
                else:
                    self.queue.put(f"SINGLESUM\t转换{converted} 跳过{skipped} 失败{failed}")

            self.single_worker = threading.Thread(target=run_dir, daemon=True)
            self.single_worker.start()

    # --- 批处理事件 ---
    def _pick_batch_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.batch_input.set(d)

    def _pick_batch_output(self):
        d = filedialog.askdirectory()
        if d:
            self.batch_output.set(d)

    def _refresh_batch_ico(self):
        if self.batch_format.get() == 'ico':
            self.batch_ico_frame.grid()
        else:
            self.batch_ico_frame.grid_remove()

    # (重复定义移除，已在前面定义 _set_all_batch_ico)

    def _start_batch(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo('提示', '任务正在进行')
            return
        inp = self.batch_input.get().strip()
        outp = self.batch_output.get().strip()
        fmt = self.batch_format.get()
        if not inp or not outp:
            self.batch_status.set('请输入输入/输出')
            return
        if not os.path.isdir(inp):
            self.batch_status.set('输入目录无效')
            return
        ico_sizes = None
        if fmt == 'ico':
            ico_sizes = [s for s, v in self.batch_ico_vars.items() if v.get()]
            if not ico_sizes:
                self.batch_status.set('请选择 ICO 尺寸')
                return
        process_same = self.process_same_var.get()
        quality = self.quality_var.get() if fmt in ('jpg','png','webp') else None
        recursive = self.batch_recursive.get()
        pattern = self.batch_pattern.get()
        start = self.batch_start.get()
        step = self.batch_step.get()
        overwrite_mode = self.batch_overwrite.get()
        self.stop_flag.clear()

        files = list(iter_image_files(inp, recursive))
        if not files:
            self.batch_status.set('无文件')
            return
        ensure_dir(outp)
        self.progress['value'] = 0
        self.progress['maximum'] = len(files)
        if hasattr(self, 'batch_progress_text'):
            self.batch_progress_text.set(f'0% (0/{len(files)})')
        if hasattr(self, 'current_file_text'):
            self.current_file_text.set('当前: -')
        for item in self.log.get_children():
            self.log.delete(item)

        def worker():
            index = start
            converted = skipped = failed = 0
            processed_count = 0
            total_files = len(files)
            for f in files:
                if self.stop_flag.is_set():
                    break
                src_ext = normalize_ext(f)
                if src_ext == fmt and not process_same:
                    skipped += 1
                    self.queue.put(f"SKIP {os.path.basename(f)}")
                    index += step
                    processed_count += 1
                    self.queue.put(f"PROGRESS\t{processed_count}\t{total_files}\t{os.path.basename(f)}")
                    continue
                name_pat = pattern.replace('{fmt}', fmt)
                final_name = name_pat
                if '{index}' in name_pat:
                    final_name = final_name.replace('{index}', str(index))
                final_name = (final_name
                              .replace('{name}', os.path.splitext(os.path.basename(f))[0])
                              .replace('{ext}', src_ext))
                if '.' not in os.path.basename(final_name):
                    final_name += f'.{fmt}'
                out_file = os.path.join(outp, final_name)
                if os.path.exists(out_file):
                    if overwrite_mode == 'skip':
                        skipped += 1
                        self.queue.put(f"SKIP {os.path.basename(f)}")
                        index += step
                        processed_count += 1
                        self.queue.put(f"PROGRESS\t{processed_count}\t{total_files}\t{os.path.basename(f)}")
                        continue
                    if overwrite_mode == 'rename':
                        out_file = next_non_conflict(out_file)
                ok, msg = convert_one(f, out_file, fmt, ico_sizes=ico_sizes, quality=quality, png3=self.png3_var.get() if fmt=='png' else False)
                if ok:
                    converted += 1
                else:
                    failed += 1
                self.queue.put(f"{os.path.basename(f)} -> {os.path.basename(out_file)}: {msg}")
                index += step
                processed_count += 1
                self.queue.put(f"PROGRESS\t{processed_count}\t{total_files}\t{os.path.basename(f)}")
            self.queue.put(f"SUMMARY 转换{converted} 跳过{skipped} 失败{failed}")

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()
        self.batch_status.set('运行中...')

    def _cancel_batch(self):
        if self.worker and self.worker.is_alive():
            self.stop_flag.set()
            self.batch_status.set('取消中...')

    def _drain_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                if msg.startswith('PROGRESS'):
                    # Support new tab format or old space format
                    if '\t' in msg:
                        try:
                            _tag, processed, total, fname = msg.split('\t', 3)
                            processed = int(processed); total = int(total)
                            self.progress['maximum'] = total
                            self.progress['value'] = processed
                            pct = int(processed / total * 100)
                            self.batch_progress_text.set(f'{pct}% ({processed}/{total})')
                            self.current_file_text.set(f'当前: {fname}')
                        except Exception:
                            pass
                    else:
                        parts = msg.split(' ', 4)
                        if len(parts) >= 4:
                            try:
                                processed = int(parts[1])
                                total = int(parts[2])
                                fname = parts[3]
                                self.progress['maximum'] = total
                                self.progress['value'] = processed
                                pct = int(processed / total * 100)
                                self.batch_progress_text.set(f'{pct}% ({processed}/{total})')
                                self.current_file_text.set(f'当前: {fname}')
                            except Exception:
                                pass
                elif msg.startswith('SUMMARY'):
                    self.batch_status.set(msg.replace('SUMMARY ', ''))
                elif msg.startswith('SINGLEPROG'):
                    try:
                        _, processed, total, pct, fname = msg.split('\t', 4)
                        processed = int(processed); total = int(total); pct = int(pct)
                        if total:
                            self.single_progress['maximum'] = total
                            self.single_progress['value'] = processed
                        self.single_status.set(f'{pct}% 处理 {fname}')
                    except Exception:
                        pass
                elif msg.startswith('SINGLESUM'):
                    self.single_status.set(msg.split('\t',1)[1] if '\t' in msg else msg)
                    # 复位按钮
                    if hasattr(self, 'btn_single_convert'):
                        self.btn_single_convert.configure(text='开始转换')
                else:
                    self.log.insert('', END, values=(msg,))
        except queue.Empty:
            pass
        finally:
            self.root.after(120, self._drain_queue)

    # ICO 批量辅助
    def _set_all_batch_ico(self, val: bool):
        for v in self.batch_ico_vars.values():
            v.set(1 if val else 0)


def launch_gui():
    root = Tk()
    try:
        from tkinter import ttk  # noqa: F401
    except Exception:
        pass
    app = ImageConverterApp(root)
    root.mainloop()


def main():
    if len(sys.argv) > 1 and any(a.startswith('-') for a in sys.argv[1:]):
        run_cli()
    else:
        launch_gui()


if __name__ == '__main__':
    main()