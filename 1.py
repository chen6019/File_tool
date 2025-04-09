import argparse
from PIL import Image
import os

def convert_image(input_path, output_path, format):
    try:
        with Image.open(input_path) as img:
            # 处理格式参数
            if format.lower() == 'jpg':
                save_format = 'JPEG'
                extension = 'jpg'
            else:
                save_format = format.upper()
                extension = format.lower()
            
            # 处理输出路径
            if output_path.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                # 确保输出文件扩展名正确
                if not output_path.lower().endswith(f'.{extension}'):
                    output_path = os.path.splitext(output_path)[0] + f'.{extension}'
                img.save(output_path, format=save_format)
            else:
                output_file = os.path.join(output_path, os.path.splitext(os.path.basename(input_path))[0] + f'.{extension}')
                img.save(output_file, format=save_format)
        print(f"成功转换: {input_path} -> {output_path}")
    except Exception as e:
        print(f"转换失败: {input_path} - {str(e)}")

def main():
    parser = argparse.ArgumentParser(description='图片格式转换工具')
    parser.add_argument('-i', '--input', required=True, help='输入文件或目录路径')
    parser.add_argument('-o', '--output', required=True, help='输出文件或目录路径')
    parser.add_argument('-f', '--format', required=True, choices=['jpg', 'png', 'webp'], help='目标格式 (jpg/png/webp)')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input):
        print("错误：输入路径不存在")
        return
    
    if os.path.isfile(args.input):
        convert_image(args.input, args.output, args.format)
    else:
        for filename in os.listdir(args.input):
            if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                input_file = os.path.join(args.input, filename)
                convert_image(input_file, args.output, args.format)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n操作已取消")