import argparse
import sys
from tkinter import Tk, IntVar, END, W, E, N, S
from tkinter import ttk, filedialog
from PIL import Image, ImageTk
import os

# Pillow LANCZOS 兼容处理（不同版本常量位置不同）
try:
    RESAMPLE = Image.Resampling.LANCZOS  # Pillow >= 9.1
except Exception:
    # 避免静态检查器报错，逐级回退，最终退到整数 1 (NEAREST)
    RESAMPLE = (
        getattr(Image, 'LANCZOS', None)
        or getattr(Image, 'ANTIALIAS', None)
        or getattr(Image, 'BILINEAR', 1)
    )

def convert_image(input_path, output_path, format, ico_sizes=None):
    try:
        with Image.open(input_path) as img:
            fmt = format.lower()
            if fmt == 'jpg':
                save_format = 'JPEG'
                extension = 'jpg'
                # 转换RGBA模式为RGB
                if img.mode == 'RGBA':
                    img = img.convert('RGB')
            elif fmt == 'ico':
                save_format = 'ICO'
                extension = 'ico'
                # 使用RGBA以保留透明度
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')
                # 处理ICO尺寸
                if ico_sizes is None or len(ico_sizes) == 0:
                    ico_sizes = [256, 128, 64, 48, 32, 16]
                # 归一化、去重并转为 Pillow 需要的二维元组
                ico_sizes = sorted({int(s) for s in ico_sizes if int(s) > 0}, reverse=True)
                ico_sizes_tuples = [(s, s) for s in ico_sizes]
            else:
                save_format = format.upper()
                extension = format.lower()

            dest_path = None
            if output_path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.ico')):
                if not output_path.lower().endswith(f'.{extension}'):
                    output_path = os.path.splitext(output_path)[0] + f'.{extension}'
                if save_format == 'ICO':
                    img.save(output_path, format=save_format,
                             sizes=ico_sizes_tuples)
                else:
                    img.save(output_path, format=save_format)
                dest_path = output_path
            else:
                output_file = os.path.join(
                    output_path,
                    os.path.splitext(os.path.basename(input_path))[0] + f'.{extension}'
                )
                if save_format == 'ICO':
                    img.save(output_file, format=save_format,
                             sizes=ico_sizes_tuples)
                else:
                    img.save(output_file, format=save_format)
                dest_path = output_file
            return f"成功转换: {input_path} -> {dest_path}"
    except Exception as e:
        print(f"转换失败: {input_path} - {str(e)}")

class ImageConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title('图片格式转换器')
        
        # 创建主框架
        mainframe = ttk.Frame(root, padding="30 20 30 20")
        mainframe.grid(column=0, row=0, sticky="nsew", padx=20, pady=20)

        # 创建样式对象
        style = ttk.Style()
        style.configure('TButton', padding=6)
        style.configure('TLabel', padding=5)
        style.configure('TEntry', padding=5)

        # 输入文件选择
        ttk.Label(mainframe, text="输入文件:").grid(column=0, row=0, sticky=W, padx=5, pady=8)
        self.input_entry = ttk.Entry(mainframe, width=40)
        self.input_entry.grid(column=1, row=0, sticky="we", padx=5, pady=8)
        ttk.Button(mainframe, text="浏览", command=self.select_input).grid(column=2, row=0, sticky=W, padx=5, pady=8)

        # 输出格式选择
        ttk.Label(mainframe, text="目标格式:").grid(column=0, row=1, sticky=W, padx=5, pady=8)
        self.format_combo = ttk.Combobox(mainframe, values=['jpg', 'png', 'webp', 'ico'], state='readonly')
        self.format_combo.grid(column=1, row=1, sticky=W, padx=5, pady=8)

        ttk.Label(mainframe, text="陈建金版权所有", foreground='red').grid(column=2, row=1, sticky=W, padx=5, pady=8)

        # ICO 尺寸选择（仅当选择 ico 时显示）
        self.ico_frame = ttk.LabelFrame(mainframe, text="ICO 尺寸")
        self.ico_sizes_list = [16, 32, 48, 64, 128, 256]
        self.ico_size_vars = {}
        for i, s in enumerate(self.ico_sizes_list):
            var = IntVar(value=1 if s in (16, 32, 48, 256) else 0)
            self.ico_size_vars[s] = var
            cb = ttk.Checkbutton(self.ico_frame, text=str(s), variable=var)
            cb.grid(column=i % 6, row=i // 6, padx=4, pady=2, sticky=W)
        self.ico_frame.grid(column=0, row=2, columnspan=3, sticky=W, padx=5, pady=8)
        # 默认隐藏，按格式切换
        self.ico_frame.grid_remove()

        # 输出路径选择（顺延一行）
        ttk.Label(mainframe, text="输出路径:").grid(column=0, row=3, sticky=W, padx=5, pady=8)
        self.output_entry = ttk.Entry(mainframe, width=40)
        self.output_entry.grid(column=1, row=3, sticky="we", padx=5, pady=8)
        ttk.Button(mainframe, text="浏览", command=self.select_output).grid(column=2, row=3, sticky=W, padx=5, pady=8)

        # 图片预览区域
        self.preview_label = ttk.Label(mainframe)
        self.preview_label.grid(column=0, row=4, columnspan=3, pady=15)

        # 转换按钮
        ttk.Button(mainframe, text="开始转换", command=self.start_conversion).grid(column=1, row=5, pady=15, ipadx=10, ipady=5)

        # 状态提示
        self.status_label = ttk.Label(mainframe, text="")
        self.status_label.grid(column=0, row=6, columnspan=3)

        # 绑定输入文件变化事件
        self.input_entry.bind('<KeyRelease>', self.update_preview)

        # 绑定格式选择变化事件
        self.format_combo.bind('<<ComboboxSelected>>', self.update_output_path)
        # 初始化一次 ICO 尺寸区域显示状态
        self.toggle_ico_options()
        self.last_auto_path = ""

    def select_input(self):
        file_path = filedialog.askopenfilename(filetypes=[("图片文件", ".jpg .jpeg .png .webp .ico")])
        if file_path:
            self.input_entry.delete(0, END)
            self.input_entry.insert(0, file_path)
            self.update_preview()

    def select_output(self):
        path = filedialog.asksaveasfilename(
            defaultextension=f".{self.format_combo.get()}",
            filetypes=[("图片文件", ".jpg .jpeg .png .webp .ico")]
        )
        if path:
            self.output_entry.delete(0, END)
            self.output_entry.insert(0, path)
            self.last_auto_path = path

    def update_output_path(self, event=None):
        input_path = self.input_entry.get()
        if os.path.isfile(input_path):
            base = os.path.splitext(os.path.basename(input_path))[0]
            output_dir = os.path.dirname(input_path)
            fmt = self.format_combo.get()
            new_path = os.path.join(output_dir, f"{base}.{fmt}")
            
            # 保留用户手动修改的路径
            if self.output_entry.get() == self.last_auto_path:
                self.output_entry.delete(0, END)
                self.output_entry.insert(0, new_path)
                self.last_auto_path = new_path
        # 切换 ICO 尺寸面板显示
        self.toggle_ico_options()

    def toggle_ico_options(self):
        try:
            if self.format_combo.get() == 'ico':
                self.ico_frame.grid()
            else:
                self.ico_frame.grid_remove()
        except Exception:
            # 初始化阶段控件尚未创建时忽略
            pass

    def update_preview(self, event=None):
        input_path = self.input_entry.get()
        if os.path.isfile(input_path):
            try:
                # 生成默认输出路径
                base = os.path.splitext(os.path.basename(input_path))[0]
                output_dir = os.path.dirname(input_path)
                fmt = self.format_combo.get() or 'jpg'
                default_path = os.path.join(output_dir, f"{base}.{fmt}")
                
                if not self.output_entry.get() or self.output_entry.get() == self.last_auto_path:
                    self.output_entry.delete(0, END)
                    self.output_entry.insert(0, default_path)
                    self.last_auto_path = default_path
                
                # 原有预览逻辑
                img = Image.open(input_path)
                max_size = 400
                width, height = img.size
                ratio = min(max_size/width, max_size/height)
                new_size = (int(width*ratio), int(height*ratio))
                
                img = img.resize(new_size, RESAMPLE)
                photo = ImageTk.PhotoImage(img)
                self.preview_label.configure(image=photo)
                # 保持引用，避免被 GC 回收
                self._preview_photo = photo
                
                # 触发路径更新
                self.update_output_path()
            
            except Exception as e:
                self.status_label.config(text=f"预览失败: {str(e)}", foreground='red')
        else:
            self.last_auto_path = ""

    # 删除重复定义：select_input 与 select_output 在类后面已有，实现仅保留前面的版本

    def start_conversion(self):
        input_path = self.input_entry.get()
        output_path = self.output_entry.get()
        format = self.format_combo.get()

        if not input_path or not output_path:
            self.status_label.config(text="请填写所有必填项", foreground='red')
            return

        try:
            ico_sizes = None
            if format == 'ico':
                # 收集被勾选的尺寸
                ico_sizes = [size for size, var in self.ico_size_vars.items() if var.get()]
                if not ico_sizes:
                    self.status_label.config(text="请至少选择一个 ICO 尺寸", foreground='red')
                    return

            result = convert_image(input_path, output_path, format, ico_sizes=ico_sizes)
            if result:
                self.status_label.config(text=result, foreground='green')
            else:
                self.status_label.config(text="转换失败，请检查输入与输出路径或图片格式", foreground='red')
        except ValueError as e:
            if 'JPEG' in str(e) and 'RGBA' in str(e):
                self.status_label.config(text="JPEG格式不支持透明背景，请选择PNG格式", foreground='red')
            else:
                self.status_label.config(text=f"转换失败: {str(e)}", foreground='red')
        except Exception as e:
            self.status_label.config(text=f"转换失败: {str(e)}", foreground='red')

def main():
    # 判断是否使用命令行模式
    if len(sys.argv) > 1:
        # 原有命令行逻辑
        parser = argparse.ArgumentParser(description='图片格式转换工具')
        parser.add_argument('-i', '--input', required=True, help='输入文件或目录路径')
        parser.add_argument('-o', '--output', required=True, help='输出文件或目录路径')
        parser.add_argument('-f', '--format', required=True, choices=['jpg', 'png', 'webp', 'ico'], help='目标格式 (jpg/png/webp/ico)')
        parser.add_argument('--ico-sizes', help='当目标格式为 ico 时的尺寸，逗号分隔，例如 16,32,48,64,128,256')
        
        args = parser.parse_args()
        
        if not os.path.exists(args.input):
            print("错误：输入路径不存在")
            return
        
        sizes_cli = None
        if args.format == 'ico':
            if args.ico_sizes:
                try:
                    sizes_cli = [int(x.strip()) for x in args.ico_sizes.split(',') if x.strip()]
                    sizes_cli = [s for s in sizes_cli if s > 0]
                    if not sizes_cli:
                        print("错误：无效的 --ico-sizes 参数")
                        return
                except Exception:
                    print("错误：--ico-sizes 应为以逗号分隔的整数列表，例如 16,32,48")
                    return
            else:
                sizes_cli = [256, 128, 64, 48, 32, 16]

        if os.path.isfile(args.input):
            convert_image(args.input, args.output, args.format, ico_sizes=sizes_cli)
        else:
            for filename in os.listdir(args.input):
                if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.ico')):
                    input_file = os.path.join(args.input, filename)
                    convert_image(input_file, args.output, args.format, ico_sizes=sizes_cli)
    else:
        # 启动GUI界面
        root = Tk()
        app = ImageConverterApp(root)
        root.mainloop()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n操作已取消")