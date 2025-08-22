import argparse
import sys
from tkinter import Tk, IntVar, BooleanVar, END, W, E, N, S
from tkinter import ttk, filedialog
from PIL import Image, ImageTk, ImageSequence
import os

# Pillow LANCZOS 兼容处理（不同版本常量位置不同）
try:
    RESAMPLE = Image.Resampling.LANCZOS  # Pillow >= 9.1
except Exception:
    RESAMPLE = (
        getattr(Image, 'LANCZOS', None)
        or getattr(Image, 'ANTIALIAS', None)
        or getattr(Image, 'BILINEAR', 1)
    )


def convert_image(input_path, output_path, target_fmt, ico_sizes=None, quality=None):
    """转换单个图片文件。

    input_path: 源文件路径
    output_path: 如果是文件夹，则输出到该文件夹；如果带扩展名则直接为输出文件路径
    target_fmt: 目标格式（jpg/png/webp/ico）
    ico_sizes: ico 时的尺寸列表（整数）
    返回：结果描述字符串
    """
    try:
        with Image.open(input_path) as img:
            original_format = (img.format or '').upper()
            is_animated = getattr(img, 'is_animated', False)
            fmt = target_fmt.lower()
            if fmt == 'jpg':
                save_format = 'JPEG'
                extension = 'jpg'
                if img.mode in ('RGBA', 'LA'):
                    # JPEG 不支持透明
                    img = img.convert('RGB')
                if quality is None:
                    quality = 85
            elif fmt == 'ico':
                save_format = 'ICO'
                extension = 'ico'
                if img.mode not in ('RGBA', 'RGB'):
                    img = img.convert('RGBA')
                if not ico_sizes:
                    ico_sizes = [256, 128, 64, 48, 32, 16]
                ico_sizes = sorted({int(s) for s in ico_sizes if int(s) > 0 and int(s) <= 1024}, reverse=True)
                ico_size_tuples = [(s, s) for s in ico_sizes]
            else:
                save_format = fmt.upper()
                extension = fmt
                if save_format == 'WEBP' and quality is None:
                    quality = 80
                if save_format == 'PNG' and quality is None:
                    quality = 100  # PNG 无损，这里仅用于映射压缩等级

            # 判断输出路径是否是文件还是目录
            if os.path.isdir(output_path) or not os.path.splitext(output_path)[1]:
                # 目录
                out_dir = output_path
                if not os.path.exists(out_dir):
                    os.makedirs(out_dir, exist_ok=True)
                base = os.path.splitext(os.path.basename(input_path))[0]
                out_file = os.path.join(out_dir, f"{base}.{extension}")
            else:
                # 明确文件
                root_no_ext, _ = os.path.splitext(output_path)
                out_file = f"{root_no_ext}.{extension}"

            params = {}
            if save_format == 'ICO':
                params['sizes'] = ico_size_tuples
            elif save_format == 'JPEG':
                if quality is not None:
                    params['quality'] = max(1, min(int(quality), 100))
                params['optimize'] = True
                if params.get('quality', 100) >= 92:
                    params['subsampling'] = 0  # 保留更多颜色
            elif save_format == 'WEBP':
                if quality is not None:
                    params['quality'] = max(1, min(int(quality), 100))
            elif save_format == 'PNG':
                # 将“质量”映射到压缩级别(0-9)与优化标志：高质量 -> 更高压缩级别
                qv = 100 if quality is None else max(1, min(int(quality), 100))
                # 映射：0-39 -> level 6, 40-79 -> 4, 80-100 -> 2 (速度与体积折中)
                if qv >= 80:
                    level = 2
                elif qv >= 40:
                    level = 4
                else:
                    level = 6
                params['compress_level'] = level
                params['optimize'] = True

            # GIF 动图特殊处理 (支持 WebP / APNG)
            if original_format == 'GIF' and is_animated:
                if save_format == 'WEBP':
                    frames = []
                    durations = []
                    for frame in ImageSequence.Iterator(img):
                        frames.append(frame.convert('RGBA'))
                        durations.append(frame.info.get('duration', 100))
                    frames[0].save(
                        out_file,
                        format='WEBP',
                        save_all=True,
                        append_images=frames[1:],
                        loop=0,
                        duration=durations,
                        quality=params.get('quality', 80)
                    )
                    return f"成功(动图): {os.path.basename(input_path)} -> {os.path.basename(out_file)} (WebP动画)"
                elif save_format == 'PNG':  # APNG
                    frames = []
                    durations = []
                    for frame in ImageSequence.Iterator(img):
                        frames.append(frame.convert('RGBA'))
                        durations.append(frame.info.get('duration', 100))
                    try:
                        frames[0].save(
                            out_file,
                            format='PNG',
                            save_all=True,
                            append_images=frames[1:],
                            loop=0,
                            duration=durations,
                            disposal=2  # 尽量保持每帧独立
                        )
                        return f"成功(动图): {os.path.basename(input_path)} -> {os.path.basename(out_file)} (APNG)"
                    except Exception:
                        # 回退首帧
                        img.seek(0)
                        first = img.convert('RGBA')
                        first.save(out_file, format='PNG', **params)
                        return f"成功(首帧): {os.path.basename(input_path)} -> {os.path.basename(out_file)} (APNG失败回退)"
                elif save_format == 'JPEG':
                    # JPEG 不支持动画，取首帧
                    img.seek(0)
                    first = img.convert('RGB')
                    first.save(out_file, format='JPEG', **params)
                    return f"成功(首帧): {os.path.basename(input_path)} -> {os.path.basename(out_file)}"

            # 普通保存
            img.save(out_file, format=save_format, **params)
            return f"成功: {os.path.basename(input_path)} -> {os.path.basename(out_file)}"
    except Exception as e:
        return f"失败: {os.path.basename(input_path)} ({e})"


class ImageConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title('图片格式转换器')

        mainframe = ttk.Frame(root, padding="18 14 18 14")
        mainframe.grid(column=0, row=0, sticky="nsew")
        for i in range(4):
            mainframe.columnconfigure(i, weight=1)

        # 行 0: 输入
        ttk.Label(mainframe, text="输入路径:").grid(column=0, row=0, sticky=W, padx=4, pady=4)
        self.input_entry = ttk.Entry(mainframe, width=46)
        self.input_entry.grid(column=1, row=0, sticky="we", padx=4, pady=4)
        ttk.Button(mainframe, text="文件", command=self.select_input).grid(column=2, row=0, padx=2, pady=4, sticky=W)
        ttk.Button(mainframe, text="文件夹", command=self.select_input_dir).grid(column=3, row=0, padx=2, pady=4, sticky=W)

        # 行 1: 格式 & 版权
        ttk.Label(mainframe, text="目标格式:").grid(column=0, row=1, sticky=W, padx=4, pady=4)
        self.format_combo = ttk.Combobox(mainframe, values=['jpg', 'png', 'webp', 'ico'], state='readonly', width=12)
        self.format_combo.grid(column=1, row=1, sticky=W, padx=4, pady=4)
        self.format_combo.bind('<<ComboboxSelected>>', self.update_output_path)

        # 行 2: 同格式处理可选 + 质量
        self.process_same_var = BooleanVar(value=False)
        ttk.Checkbutton(mainframe, text="同格式也重新保存", variable=self.process_same_var).grid(column=0, row=2, columnspan=2, sticky=W, padx=4, pady=2)
        ttk.Label(mainframe, text="质量:").grid(column=2, row=2, sticky=E, padx=4, pady=2)
        self.quality_var = IntVar(value=85)
        self.quality_spin = ttk.Spinbox(mainframe, from_=1, to=100, textvariable=self.quality_var, width=6)
        self.quality_spin.grid(column=3, row=2, sticky=W, padx=4, pady=2)

        # 行 3: ICO 尺寸
        self.ico_frame = ttk.LabelFrame(mainframe, text="ICO 尺寸")
        self.ico_sizes_list = [16, 32, 48, 64, 128, 256]
        self.ico_size_vars = {}
        for i, s in enumerate(self.ico_sizes_list):
            var = IntVar(value=1 if s in (16, 32, 48, 256) else 0)
            self.ico_size_vars[s] = var
            ttk.Checkbutton(self.ico_frame, text=str(s), variable=var).grid(column=i % 6, row=i // 6, padx=3, pady=2, sticky=W)
        self.ico_frame.grid(column=0, row=3, columnspan=4, sticky=W, padx=4, pady=4)
        self.ico_frame.grid_remove()  # 默认隐藏

        # 行 4: 输出
        ttk.Label(mainframe, text="输出路径(文件或文件夹):").grid(column=0, row=4, sticky=W, padx=4, pady=4)
        self.output_entry = ttk.Entry(mainframe, width=46)
        self.output_entry.grid(column=1, row=4, columnspan=2, sticky="we", padx=4, pady=4)
        ttk.Button(mainframe, text="浏览", command=self.select_output).grid(column=3, row=4, padx=2, pady=4, sticky=W)

        # 行 5: 预览
        self.preview_label = ttk.Label(mainframe)
        self.preview_label.grid(column=0, row=5, columnspan=4, pady=10)

        # 行 6: 操作
        ttk.Button(mainframe, text="开始转换", command=self.start_conversion).grid(column=1, row=6, pady=10, ipadx=12)

        # 行 7: 状态
        self.status_label = ttk.Label(mainframe, text="", foreground='')
        self.status_label.grid(column=0, row=7, columnspan=4, sticky=W, padx=4)

        # 事件
        self.input_entry.bind('<KeyRelease>', self.update_preview)
        self.last_auto_path = ''
        self.toggle_ico_options()

    # 选择源文件
    def select_input(self):
        path = filedialog.askopenfilename(filetypes=[("图片文件", ".png .jpg .jpeg .webp .ico .gif")])
        if path:
            self.input_entry.delete(0, END)
            self.input_entry.insert(0, path)
            self.update_preview()

    # 选择源目录
    def select_input_dir(self):
        directory = filedialog.askdirectory()
        if directory:
            self.input_entry.delete(0, END)
            self.input_entry.insert(0, directory)
            self.preview_label.configure(image='')
            self._preview_photo = None
            self.status_label.config(text=f"已选择目录: {directory}")

    # 选择输出
    def select_output(self):
        fmt = self.format_combo.get() or 'png'
        path = filedialog.asksaveasfilename(defaultextension=f'.{fmt}',
                                            filetypes=[("图片文件", ".png .jpg .jpeg .webp .ico .gif")])
        if path:
            self.output_entry.delete(0, END)
            self.output_entry.insert(0, path)
            self.last_auto_path = path

    def update_output_path(self, _evt=None):
        inp = self.input_entry.get()
        if os.path.isfile(inp):
            base = os.path.splitext(os.path.basename(inp))[0]
            fmt = self.format_combo.get()
            if fmt:
                auto = os.path.join(os.path.dirname(inp), f"{base}.{fmt}")
                if not self.output_entry.get() or self.output_entry.get() == self.last_auto_path:
                    self.output_entry.delete(0, END)
                    self.output_entry.insert(0, auto)
                    self.last_auto_path = auto
        self.toggle_ico_options()

    def toggle_ico_options(self):
        current = (self.format_combo.get() or '').lower()
        if current == 'ico':
            self.ico_frame.grid()
        else:
            self.ico_frame.grid_remove()
        # 质量输入显示控制
        if current in ('jpg', 'webp', 'png'):
            self.quality_spin.state(['!disabled'])
        else:
            self.quality_spin.state(['disabled'])

    def update_preview(self, _evt=None):
        path = self.input_entry.get()
        if os.path.isfile(path):
            try:
                img = Image.open(path)
                max_len = 420
                w, h = img.size
                scale = min(max_len / w, max_len / h, 1)
                if scale < 1:
                    img = img.resize((int(w * scale), int(h * scale)), RESAMPLE)
                photo = ImageTk.PhotoImage(img)
                self.preview_label.configure(image=photo)
                self._preview_photo = photo
                self.update_output_path()
            except Exception as e:
                self.status_label.config(text=f"预览失败: {e}", foreground='red')

    def start_conversion(self):
        inp = self.input_entry.get().strip()
        outp = self.output_entry.get().strip()
        fmt = (self.format_combo.get() or '').lower()

        if not inp or not outp or not fmt:
            self.status_label.config(text='请填写完整信息', foreground='red')
            return

        def norm_ext(p):
            ext = os.path.splitext(p)[1].lower().lstrip('.')
            return 'jpg' if ext in ('jpg', 'jpeg') else ext

        ico_sizes = None
        if fmt == 'ico':
            ico_sizes = [s for s, var in self.ico_size_vars.items() if var.get()]
            if not ico_sizes:
                self.status_label.config(text='请至少选择一个 ICO 尺寸', foreground='red')
                return

        process_same = self.process_same_var.get()

        try:
            if os.path.isfile(inp):
                if norm_ext(inp) == fmt and not process_same:
                    self.status_label.config(text='跳过：源与目标格式相同 (未勾选重新保存)', foreground='orange')
                    return
                qual = self.quality_var.get() if fmt in ('jpg','webp','png') else None
                msg = convert_image(inp, outp, fmt, ico_sizes=ico_sizes, quality=qual)
                self.status_label.config(text=msg, foreground='green' if msg.startswith('成功') else 'red')
            else:
                if not os.path.isdir(inp):
                    self.status_label.config(text='输入路径无效', foreground='red')
                    return
                if not os.path.exists(outp):
                    os.makedirs(outp, exist_ok=True)
                converted = skipped = failed = 0
                for name in os.listdir(inp):
                    if name.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.ico', '.gif')):
                        src = os.path.join(inp, name)
                        if norm_ext(src) == fmt and not process_same:
                            skipped += 1
                            continue
                        qual = self.quality_var.get() if fmt in ('jpg','webp','png') else None
                        r = convert_image(src, outp, fmt, ico_sizes=ico_sizes, quality=qual)
                        if r.startswith('成功'):
                            converted += 1
                        else:
                            failed += 1
                self.status_label.config(
                    text=f'完成：转换{converted} 跳过{skipped} 失败{failed}',
                    foreground='green' if failed == 0 else 'orange'
                )
        except Exception as e:
            self.status_label.config(text=f'转换失败: {e}', foreground='red')


def main():
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(description='图片格式转换工具')
        parser.add_argument('-i', '--input', required=True, help='输入文件或目录路径')
        parser.add_argument('-o', '--output', required=True, help='输出文件或目录路径 (单文件时可为文件，全为目录时必须存在或可创建)')
        parser.add_argument('-f', '--format', required=True, choices=['jpg', 'png', 'webp', 'ico'], help='目标格式')
        parser.add_argument('--ico-sizes', help='ICO 尺寸列表，逗号分隔，如 16,32,48,256')
        parser.add_argument('--process-same', action='store_true', help='源格式与目标格式相同时仍重新保存')
        parser.add_argument('--quality', type=int, help='质量 (1-100)，适用于 jpg/webp/png')
        args = parser.parse_args()

        if not os.path.exists(args.input):
            print('错误：输入路径不存在')
            return

        def norm_ext(p):
            e = os.path.splitext(p)[1].lower().lstrip('.')
            return 'jpg' if e in ('jpg', 'jpeg') else e

        ico_sizes = None
        if args.format == 'ico':
            if args.ico_sizes:
                try:
                    ico_sizes = [int(x.strip()) for x in args.ico_sizes.split(',') if x.strip()]
                    ico_sizes = [s for s in ico_sizes if s > 0]
                    if not ico_sizes:
                        print('错误：无效的 --ico-sizes 参数')
                        return
                except Exception:
                    print('错误：--ico-sizes 需为逗号分隔整数，例如 16,32,64')
                    return
            else:
                ico_sizes = [256, 128, 64, 48, 32, 16]

        if os.path.isfile(args.input):
            if norm_ext(args.input) != args.format or args.process_same:
                print(convert_image(args.input, args.output, args.format, ico_sizes=ico_sizes, quality=args.quality))
            else:
                print('跳过：源与目标格式相同 (未指定 --process-same)')
        else:
            if not os.path.exists(args.output):
                os.makedirs(args.output, exist_ok=True)
            converted = skipped = failed = 0
            for name in os.listdir(args.input):
                if name.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.ico', '.gif')):
                    src = os.path.join(args.input, name)
                    if norm_ext(src) == args.format and not args.process_same:
                        skipped += 1
                        continue
                    r = convert_image(src, args.output, args.format, ico_sizes=ico_sizes, quality=args.quality)
                    if r.startswith('成功'):
                        converted += 1
                    else:
                        failed += 1
            print(f'完成：转换{converted} 跳过{skipped} 失败{failed}')
    else:
        root = Tk()
        app = ImageConverterApp(root)
        root.mainloop()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n已取消')