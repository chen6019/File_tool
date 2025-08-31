"""重复图片清理工具

依据图片内容(感知哈希)分组检测相同/相似图片, 支持自动删除或移动冗余文件。

特性:
1. 支持 aHash + dHash 组合, 提升碰撞难度; 可设相似阈值(汉明距离)判定近似。
2. 可选递归遍历, 最小文件大小过滤, 指定扩展过滤。
3. 分组后按策略选择保留文件: first / largest (分辨率) / largest-file (字节) / newest / oldest。
4. 操作模式: list (仅列出) / delete (删除冗余) / move (移动冗余到目录) + dry-run 预演。
5. 支持输出报告 (txt) 方便复核。

阈值建议:
- threshold = 0: 仅哈希完全相同(严格重复)。
- 1~8: 允许轻微编辑(旋转裁剪后通常差异较大, 轻微压缩/重新保存差别小)。
  风险: 阈值越大误判越多, 请先 dry-run 观察。

示例:
python 重复图片清理.py -i D:/Photos --recursive --threshold 4 --action move --move-dir D:/Dupes --keep-strategy largest --dry-run

"""
from __future__ import annotations
import os
import sys
import argparse
import time
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Iterable

try:
    from PIL import Image
except ImportError:
    print('需要安装 Pillow: pip install Pillow')
    sys.exit(1)

SUPPORTED_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tif', '.tiff'}

@dataclass
class ImageInfo:
    path: str
    size_bytes: int
    width: int
    height: int
    ahash: int
    dhash: int
    combined: int  # (aHash << 64) | dHash
    mtime: float

    @property
    def resolution(self) -> int:
        return self.width * self.height

    def to_line(self) -> str:
        return f"{self.path} | {self.width}x{self.height} | {self.size_bytes}B"


def iter_files(root: str, recursive: bool) -> Iterable[str]:
    if os.path.isfile(root):
        yield root
        return
    for base, dirs, files in os.walk(root):
        for f in files:
            yield os.path.join(base, f)
        if not recursive:
            break


def is_image(path: str, exts: set[str]) -> bool:
    return os.path.splitext(path)[1].lower() in exts


def ahash(img: Image.Image, size: int = 8) -> int:
    # 平均哈希: 缩放灰度 -> 均值 -> 位图
    im = img.convert('L').resize((size, size), Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS)
    pixels = list(im.getdata())
    avg = sum(pixels) / len(pixels)
    bits = 0
    for p in pixels:
        bits = (bits << 1) | (1 if p >= avg else 0)
    return bits


def dhash(img: Image.Image, size: int = 8) -> int:
    # 差值哈希: (size+1)x size 灰度，比较相邻
    im = img.convert('L').resize((size + 1, size), Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS)
    pixels = list(im.getdata())
    bits = 0
    for row in range(size):
        row_pixels = pixels[row * (size + 1):(row + 1) * (size + 1)]
        for x in range(size):
            bits = (bits << 1) | (1 if row_pixels[x] > row_pixels[x + 1] else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def compute_info(path: str) -> ImageInfo | None:
    try:
        with Image.open(path) as im:
            w, h = im.size
            ah = ahash(im)
            dh = dhash(im)
        st = os.stat(path)
        combined = (ah << 64) | dh
        return ImageInfo(path=path, size_bytes=st.st_size, width=w, height=h, ahash=ah, dhash=dh, combined=combined, mtime=st.st_mtime)
    except Exception:
        return None


def group_duplicates(infos: List[ImageInfo], threshold: int) -> List[List[ImageInfo]]:
    # 简单聚类: 线性扫描, 每个加入已有组或新建
    groups: List[List[ImageInfo]] = []
    for info in infos:
        placed = False
        for g in groups:
            # 与组第一个比较(中心代表)
            rep = g[0]
            # 先快速判断: 低位截断前缀相同再细算(优化)
            if threshold == 0:
                if info.ahash == rep.ahash and info.dhash == rep.dhash:
                    g.append(info)
                    placed = True
                    break
            else:
                dist_a = hamming(info.ahash, rep.ahash)
                if dist_a > threshold:  # 粗过滤
                    continue
                dist_d = hamming(info.dhash, rep.dhash)
                if dist_a + dist_d <= threshold:
                    g.append(info)
                    placed = True
                    break
        if not placed:
            groups.append([info])
    # 只返回大小>1 的重复组
    return [g for g in groups if len(g) > 1]


def choose_keep(group: List[ImageInfo], strategy: str) -> ImageInfo:
    if strategy == 'largest':
        return max(group, key=lambda x: x.resolution)
    if strategy == 'largest-file':
        return max(group, key=lambda x: x.size_bytes)
    if strategy == 'newest':
        return max(group, key=lambda x: x.mtime)
    if strategy == 'oldest':
        return min(group, key=lambda x: x.mtime)
    return group[0]  # first


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description='重复图片清理 (基于感知哈希)')
    p.add_argument('-i', '--input', required=True, help='输入目录或单个文件')
    p.add_argument('-r', '--recursive', action='store_true', help='递归遍历')
    p.add_argument('--ext', help='逗号扩展名过滤(默认常见图片)')
    p.add_argument('--min-size', type=int, default=0, help='最小文件大小(字节)过滤')
    p.add_argument('--threshold', type=int, default=0, help='相似阈值(0=完全相同; 建议 <=8)')
    p.add_argument('--action', choices=['list', 'delete', 'move'], default='list', help='对冗余文件的操作')
    p.add_argument('--move-dir', help='action=move 时目标目录')
    p.add_argument('--keep-strategy', choices=['first','largest','largest-file','newest','oldest'], default='largest', help='重复组保留策略')
    p.add_argument('--dry-run', action='store_true', help='仅模拟不执行删除/移动')
    p.add_argument('--report', help='输出报告 txt 文件')
    return p.parse_args(argv)


def main(argv: List[str] | None = None):
    # 若无参数 -> GUI
    if argv is None and len(sys.argv) == 1:
        return launch_gui()

    ns = parse_args(argv or sys.argv[1:])
    if not os.path.exists(ns.input):
        print('输入不存在')
        return 1

    exts = SUPPORTED_EXT.copy()
    if ns.ext:
        exts = {'.' + e.lower().lstrip('.') for e in ns.ext.split(',') if e.strip()}

    # 收集文件
    all_files = []
    for p in iter_files(ns.input, ns.recursive):
        if not is_image(p, exts):
            continue
        try:
            if ns.min_size and os.path.getsize(p) < ns.min_size:
                continue
        except OSError:
            continue
        all_files.append(p)
    if not all_files:
        print('未找到图片文件')
        return 0
    print(f'扫描 {len(all_files)} 个文件, 计算哈希...')

    infos: List[ImageInfo] = []
    skipped = 0
    start = time.time()
    for idx, f in enumerate(all_files, 1):
        info = compute_info(f)
        if info:
            infos.append(info)
        else:
            skipped += 1
        if idx % 200 == 0:
            print(f'  进度 {idx}/{len(all_files)}')
    dur = time.time() - start
    print(f'哈希完成, 有效 {len(infos)}, 失败 {skipped}, 用时 {dur:.2f}s')

    groups = group_duplicates(infos, ns.threshold)
    if not groups:
        print('未发现重复/相似图片')
        return 0

    print(f'发现重复/相似组 {len(groups)} (threshold={ns.threshold})')

    lines: List[str] = []
    removed_total = 0
    for gi, g in enumerate(sorted(groups, key=lambda x: -len(x)), 1):
        keep = choose_keep(g, ns.keep_strategy)
        others = [x for x in g if x is not keep]
        lines.append(f'组#{gi}  共{len(g)}  保留: {keep.to_line()}')
        for o in others:
            rel_action = '保持' if ns.action == 'list' else ('删除' if ns.action == 'delete' else '移动')
            lines.append(f'    -> {rel_action}: {o.to_line()}')
        # 执行动作
        if ns.action in ('delete', 'move') and not ns.dry_run:
            for o in others:
                try:
                    if ns.action == 'delete':
                        os.remove(o.path)
                        removed_total += 1
                    else:  # move
                        if not ns.move_dir:
                            print('缺少 --move-dir (已跳过移动)')
                            break
                        os.makedirs(ns.move_dir, exist_ok=True)
                        base = os.path.basename(o.path)
                        target = os.path.join(ns.move_dir, base)
                        # 防冲突
                        if os.path.exists(target):
                            root_name, ext = os.path.splitext(base)
                            k = 1
                            while True:
                                cand = f"{root_name}_dup{k}{ext}"
                                cand_path = os.path.join(ns.move_dir, cand)
                                if not os.path.exists(cand_path):
                                    target = cand_path
                                    break
                                k += 1
                        os.replace(o.path, target)
                        removed_total += 1
                except Exception as e:
                    lines.append(f'       (失败: {e})')

    report_text = '\n'.join(lines)
    print(report_text)
    if ns.action in ('delete','move'):
        print(f'实际处理文件: {removed_total} (dry-run={ns.dry_run})')

    if ns.report:
        try:
            with open(ns.report, 'w', encoding='utf-8') as f:
                f.write(report_text)
            print(f'报告写入: {ns.report}')
        except Exception as e:
            print(f'报告写入失败: {e}')
    return 0


# ---------------- GUI -----------------
def launch_gui():  # type: ignore
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
    except Exception as e:
        print('无法导入 Tkinter:', e)
        return 1
    import threading, queue, math

    class App:
        def __init__(self, root: 'tk.Tk'):
            self.root = root
            self.root.title('重复图片清理 (感知哈希)')
            # 扩大默认窗口尺寸以避免列内容被截断
            try:
                sw = self.root.winfo_screenwidth()
                sh = self.root.winfo_screenheight()
                w = 1180 if sw > 1400 else 1020
                h = 720 if sh > 900 else 640
                self.root.geometry(f'{w}x{h}')
            except Exception:
                self.root.geometry('1100x680')
            self.q: 'queue.Queue[str]' = queue.Queue()
            self.stop_flag = threading.Event()
            # Vars
            self.input_var = tk.StringVar()
            self.recursive_var = tk.BooleanVar(value=True)
            self.ext_var = tk.StringVar()
            self.min_size_var = tk.IntVar(value=0)
            self.threshold_var = tk.IntVar(value=0)
            self.keep_strategy_var = tk.StringVar(value='largest')
            self.action_var = tk.StringVar(value='list')
            self.move_dir_var = tk.StringVar()
            self.dry_run_var = tk.BooleanVar(value=True)
            # UI
            self._build()
            self.root.after(120, self._drain)

        def _build(self):
            frm = ttk.Frame(self.root, padding=10)
            frm.pack(fill='both', expand=True)
            for i in range(8):
                # 统一列宽以减少因控件内容变化导致的跳动
                frm.columnconfigure(i, weight=1, minsize=80)
            r = 0
            ttk.Label(frm, text='输入目录:').grid(row=r, column=0, sticky='e')
            ttk.Entry(frm, textvariable=self.input_var, width=50).grid(row=r, column=1, columnspan=4, sticky='we', padx=4)
            ttk.Button(frm, text='选择', command=self._pick_input).grid(row=r, column=5, padx=2)
            ttk.Checkbutton(frm, text='递归', variable=self.recursive_var).grid(row=r, column=6, sticky='w')
            ttk.Button(frm, text='说明', command=self._show_help).grid(row=r, column=7, sticky='e')
            r += 1
            ttk.Label(frm, text='扩展过滤(逗号):').grid(row=r, column=0, sticky='e')
            ttk.Entry(frm, textvariable=self.ext_var, width=30).grid(row=r, column=1, sticky='w', padx=4)
            ttk.Label(frm, text='最小大小(B):').grid(row=r, column=2, sticky='e')
            ttk.Entry(frm, textvariable=self.min_size_var, width=12).grid(row=r, column=3, sticky='w')
            ttk.Label(frm, text='阈值:').grid(row=r, column=4, sticky='e')
            ttk.Scale(frm, from_=0, to=16, variable=self.threshold_var, orient='horizontal').grid(row=r, column=5, sticky='we', padx=4)
            # 固定宽度显示当前值
            ttk.Label(frm, textvariable=self.threshold_var, width=3, anchor='w').grid(row=r, column=6, sticky='w')
            # 手动输入(Spinbox) 支持直接键入数值
            try:
                spin = ttk.Spinbox(frm, from_=0, to=16, textvariable=self.threshold_var, width=4)
            except Exception:
                # 某些旧 Tk 版本可能无 ttk.Spinbox, 回退普通 Entry
                from tkinter import Entry
                spin = Entry(frm, textvariable=self.threshold_var, width=4)
            spin.grid(row=r, column=7, sticky='w')
            r += 1
            ttk.Label(frm, text='保留策略:').grid(row=r, column=0, sticky='e')
            ttk.Combobox(frm, textvariable=self.keep_strategy_var, values=['first','largest','largest-file','newest','oldest'], state='readonly', width=12).grid(row=r, column=1, sticky='w')
            ttk.Label(frm, text='动作:').grid(row=r, column=2, sticky='e')
            ttk.Combobox(frm, textvariable=self.action_var, values=['list','delete','move'], state='readonly', width=10).grid(row=r, column=3, sticky='w')
            ttk.Label(frm, text='移动目录:').grid(row=r, column=4, sticky='e')
            ttk.Entry(frm, textvariable=self.move_dir_var, width=22).grid(row=r, column=5, sticky='we', padx=4)
            ttk.Button(frm, text='选择', command=self._pick_move_dir).grid(row=r, column=6, padx=2)
            ttk.Checkbutton(frm, text='dry-run', variable=self.dry_run_var).grid(row=r, column=7, sticky='w')
            r += 1
            self.progress = ttk.Progressbar(frm, maximum=100)
            self.progress.grid(row=r, column=0, columnspan=8, sticky='we', pady=4)
            r += 1
            self.status_var = tk.StringVar(value='就绪')
            ttk.Label(frm, textvariable=self.status_var, foreground='blue').grid(row=r, column=0, columnspan=8, sticky='w')
            r += 1
            # Tree
            # 结果 & 预览分栏
            result_container = ttk.Frame(frm)
            result_container.grid(row=r, column=0, columnspan=8, sticky='nsew')
            frm.rowconfigure(r, weight=1)
            result_container.columnconfigure(0, weight=5)
            result_container.columnconfigure(1, weight=2)

            cols = ('group','keep','path','res','size','action')
            tree_frame = ttk.Frame(result_container)
            tree_frame.grid(row=0, column=0, sticky='nsew')
            tree_frame.columnconfigure(0, weight=1)
            tree_frame.rowconfigure(0, weight=1)
            self.tree = ttk.Treeview(tree_frame, columns=cols, show='headings', height=18)
            headers = {'group':'组','keep':'保留','path':'路径','res':'分辨率','size':'大小KB','action':'处理'}
            for c in cols:
                self.tree.heading(c, text=headers[c])
                if c == 'path':
                    self.tree.column(c, anchor='w', width=560, stretch=True)
                elif c == 'group':
                    self.tree.column(c, anchor='w', width=60, stretch=False)
                elif c == 'keep':
                    self.tree.column(c, anchor='w', width=50, stretch=False)
                elif c == 'res':
                    self.tree.column(c, anchor='w', width=110, stretch=False)
                elif c == 'size':
                    self.tree.column(c, anchor='w', width=90, stretch=False)
                else:  # action
                    self.tree.column(c, anchor='w', width=80, stretch=False)
            vsb = ttk.Scrollbar(tree_frame, orient='vertical', command=self.tree.yview)
            self.tree.configure(yscrollcommand=vsb.set)
            self.tree.grid(row=0, column=0, sticky='nsew')
            vsb.grid(row=0, column=1, sticky='ns')

            # 预览面板
            preview_frame = ttk.LabelFrame(result_container, text='预览')
            preview_frame.grid(row=0, column=1, sticky='nsew', padx=(8,0))
            preview_frame.columnconfigure(0, weight=1)
            preview_frame.rowconfigure(0, weight=1)
            self.preview_label_img = ttk.Label(preview_frame, text='(选择一行查看)')
            self.preview_label_img.grid(row=0, column=0, sticky='nsew', padx=4, pady=4)
            self.preview_info_var = tk.StringVar(value='')
            ttk.Label(preview_frame, textvariable=self.preview_info_var, foreground='gray').grid(row=1, column=0, sticky='we', padx=4, pady=2)
            ttk.Button(preview_frame, text='打开所在目录', command=self._open_in_explorer).grid(row=2, column=0, pady=4)
            self.tree.bind('<<TreeviewSelect>>', self._on_select_row)
            r += 1
            btnf = ttk.Frame(frm)
            btnf.grid(row=r, column=0, columnspan=8, sticky='we', pady=4)
            ttk.Button(btnf, text='开始扫描', command=self._start).pack(side='left', padx=4)
            ttk.Button(btnf, text='取消', command=self._cancel).pack(side='left', padx=4)
            ttk.Button(btnf, text='清空', command=lambda: self.tree.delete(*self.tree.get_children())).pack(side='left', padx=4)
            self.summary_var = tk.StringVar(value='')
            ttk.Label(btnf, textvariable=self.summary_var, foreground='green').pack(side='right')

        def _pick_input(self):
            from tkinter import filedialog
            d = filedialog.askdirectory()
            if d:
                self.input_var.set(d)

        def _pick_move_dir(self):
            from tkinter import filedialog
            d = filedialog.askdirectory()
            if d:
                self.move_dir_var.set(d)

        def _show_help(self):
            import tkinter as tk
            win = tk.Toplevel(self.root)
            win.title('阈值与策略说明')
            txt = tk.Text(win, wrap='word', width=70, height=18)
            txt.pack(fill='both', expand=True)
            txt.insert('end', '阈值(0~16):\n0=仅完全重复; 1~8=轻度近似(建议先 dry-run); >8 风险较高。\n\n保留策略:\n largest=最高分辨率  largest-file=文件字节最大  newest/oldest=修改时间  first=扫描顺序。\n\n动作:\n list=仅列出  delete=删除非保留  move=移动非保留到指定目录。\n')
            txt.configure(state='disabled')
            tk.Button(win, text='关闭', command=win.destroy).pack(pady=4)

        def _start(self):
            if getattr(self, 'worker', None) and self.worker.is_alive():
                messagebox.showinfo('提示','正在运行')
                return
            root_dir = self.input_var.get().strip()
            if not root_dir or not os.path.isdir(root_dir):
                self.status_var.set('请输入有效目录')
                return
            if self.action_var.get() == 'move' and not self.move_dir_var.get().strip():
                self.status_var.set('请选择移动目录')
                return
            self.tree.delete(*self.tree.get_children())
            self.stop_flag.clear()
            self.status_var.set('收集文件...')
            recursive = self.recursive_var.get()
            threshold = self.threshold_var.get()
            keep_strategy = self.keep_strategy_var.get()
            action = self.action_var.get()
            dry_run = self.dry_run_var.get()
            min_size = self.min_size_var.get() or 0
            exts = SUPPORTED_EXT.copy()
            if self.ext_var.get().strip():
                exts = {'.' + e.lower().lstrip('.') for e in self.ext_var.get().split(',') if e.strip()}
            move_dir = self.move_dir_var.get().strip()

            def worker():
                files = []
                for p in iter_files(root_dir, recursive):
                    if self.stop_flag.is_set():
                        break
                    if not is_image(p, exts):
                        continue
                    try:
                        if min_size and os.path.getsize(p) < min_size:
                            continue
                    except OSError:
                        continue
                    files.append(p)
                total = len(files)
                if not total:
                    self.q.put('STATUS 没有文件')
                    return
                self.q.put(f'STATUS 计算哈希... 共{total}')
                infos: List[ImageInfo] = []
                for idx, fpath in enumerate(files, 1):
                    if self.stop_flag.is_set():
                        break
                    info = compute_info(fpath)
                    if info:
                        infos.append(info)
                    if idx % 50 == 0 or idx == total:
                        pct = int(idx/total*100)
                        self.q.put(f'HASH {idx} {total} {pct}')
                if self.stop_flag.is_set():
                    self.q.put('STATUS 已取消')
                    return
                self.q.put('STATUS 分组...')
                groups = group_duplicates(infos, threshold)
                if not groups:
                    self.q.put('STATUS 未发现重复')
                    self.q.put('SUMMARY groups=0')
                    return
                self.q.put(f'STATUS 发现 {len(groups)} 组, 填充结果表...')
                gid = 0
                del_actions: List[Tuple[str,str]] = []  # (op,path)
                for g in sorted(groups, key=lambda x: -len(x)):
                    gid += 1
                    keep = choose_keep(g, keep_strategy)
                    for item in g:
                        act = '保留' if item is keep else ('删除' if action=='delete' else ('移动' if action=='move' else '列出'))
                        self.q.put(f'ROW {gid}\t{1 if item is keep else 0}\t{item.path}\t{item.width}x{item.height}\t{int(item.size_bytes/1024)}\t{act}')
                        if item is not keep and action in ('delete','move'):
                            del_actions.append((action, item.path))
                # 执行
                if action in ('delete','move') and not dry_run and del_actions:
                    self.q.put('STATUS 执行清理...')
                    for op, path in del_actions:
                        if self.stop_flag.is_set():
                            break
                        try:
                            if op == 'delete':
                                os.remove(path)
                            else:
                                if not move_dir:
                                    continue
                                os.makedirs(move_dir, exist_ok=True)
                                base = os.path.basename(path)
                                target = os.path.join(move_dir, base)
                                if os.path.exists(target):
                                    rootn, ext = os.path.splitext(base)
                                    k = 1
                                    while True:
                                        cand = f"{rootn}_dup{k}{ext}"
                                        cand_path = os.path.join(move_dir, cand)
                                        if not os.path.exists(cand_path):
                                            target = cand_path
                                            break
                                        k += 1
                                os.replace(path, target)
                        except Exception as e:
                            self.q.put(f'STATUS 处理失败:{e}')
                self.q.put(f'SUMMARY groups={gid}')
                self.q.put('STATUS 完成')

            self.worker = threading.Thread(target=worker, daemon=True)
            self.worker.start()
            self.status_var.set('启动线程...')

        def _cancel(self):
            self.stop_flag.set()
            self.status_var.set('请求取消...')

        def _drain(self):
            try:
                while True:
                    msg = self.q.get_nowait()
                    if msg.startswith('STATUS '):
                        self.status_var.set(msg[7:])
                    elif msg.startswith('HASH '):
                        _, done, total, pct = msg.split()
                        try:
                            done = int(done); total = int(total); pct = int(pct)
                            self.progress['maximum'] = total
                            self.progress['value'] = done
                        except Exception:
                            pass
                    elif msg.startswith('ROW '):
                        _, rest = msg.split(' ',1)
                        gid, keep, path, res, size_kb, act = rest.split('\t')
                        values = (gid, '★' if keep=='1' else '', path, res, size_kb, act)
                        self.tree.insert('', 'end', values=values, tags=('keep' if keep=='1' else 'norm',))
                    elif msg.startswith('SUMMARY '):
                        self.summary_var.set(msg[8:])
                    # else ignore
            except Exception:
                pass
            finally:
                self.root.after(120, self._drain)

        # --- 预览相关 ---
        def _on_select_row(self, event=None):
            sel = self.tree.selection()
            if not sel:
                return
            item_id = sel[0]
            vals = self.tree.item(item_id, 'values')
            if len(vals) < 3:
                return
            path = vals[2]
            if not os.path.exists(path):
                self.preview_label_img.configure(text='文件不存在', image='')
                self.preview_info_var.set('')
                return
            # 加载缩略图
            try:
                from PIL import Image, ImageTk
                with Image.open(path) as im:
                    w, h = im.size
                    max_side = 360
                    scale = min(max_side / w, max_side / h, 1)
                    if scale < 1:
                        im = im.resize((int(w*scale), int(h*scale)), Image.Resampling.LANCZOS if hasattr(Image,'Resampling') else Image.LANCZOS)
                    photo = ImageTk.PhotoImage(im)
                self.preview_label_img.configure(image=photo, text='')
                self._preview_photo_ref = photo  # 保存引用
                self.preview_info_var.set(f'{w}x{h}  {os.path.basename(path)}')
            except Exception as e:
                self.preview_label_img.configure(text=f'预览失败: {e}', image='')
                self.preview_info_var.set(os.path.basename(path))

            self._last_preview_path = path

        def _open_in_explorer(self):
            path = getattr(self, '_last_preview_path', None)
            if not path or not os.path.exists(path):
                return
            try:
                if sys.platform.startswith('win'):
                    import subprocess, shlex, pathlib
                    target = os.path.normpath(path)
                    # 使用 /select, 让资源管理器定位文件；若失败退回目录
                    try:
                        subprocess.run(['explorer', '/select,', target], check=False)
                    except Exception:
                        subprocess.run(['explorer', os.path.dirname(target)], check=False)
                elif sys.platform == 'darwin':  # macOS
                    import subprocess
                    try:
                        subprocess.run(['open', '-R', path], check=False)
                    except Exception:
                        subprocess.run(['open', os.path.dirname(path)], check=False)
                else:  # Linux / others
                    import subprocess
                    subprocess.Popen(['xdg-open', os.path.dirname(path)])
            except Exception as e:
                # 失败静默, 可扩展日志
                self.status_var.set(f'打开目录失败: {e}')

    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
