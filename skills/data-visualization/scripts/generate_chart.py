import argparse
import matplotlib.pyplot as plt
from pathlib import Path
import sys

# 解决 Matplotlib 中文显示乱码问题
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False


def main():
    parser = argparse.ArgumentParser(description="生成可视化图表的辅助脚本")
    parser.add_argument("--type", choices=['bar', 'line', 'pie'], required=True, help="图表类型")
    parser.add_argument("--title", required=True, help="图表标题")
    parser.add_argument("--x_labels", required=True, help="X轴标签，用逗号分隔")
    parser.add_argument("--y_values", required=True, help="Y轴数值，用逗号分隔")
    parser.add_argument("--output", required=True, help="输出图片的绝对路径")

    args = parser.parse_args()

    try:
        x_labels = [x.strip("'\" ") for x in args.x_labels.split(',')]
        y_values = [float(y.strip("'\" ")) for y in args.y_values.split(',')]

        if len(x_labels) != len(y_values):
            print("Error: X轴标签和Y轴数值的数量必须相等！", file=sys.stderr)
            sys.exit(1)

        plt.figure(figsize=(10, 6))

        if args.type == 'bar':
            plt.bar(x_labels, y_values, color='#4E75F6', width=0.6)
            plt.ylabel("数值")
        elif args.type == 'line':
            plt.plot(x_labels, y_values, marker='o', linestyle='-', color='#E3557A', linewidth=2)
            plt.grid(True, linestyle='--', alpha=0.6)
            plt.ylabel("数值")
        elif args.type == 'pie':
            plt.pie(y_values, labels=x_labels, autopct='%1.1f%%', startangle=140, colors=plt.cm.Paired.colors)

        plt.title(args.title, fontsize=16, pad=15)
        plt.tight_layout()

        clean_output = args.output.strip("'\" ")
        output_path = Path(clean_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(str(output_path), dpi=300)
        print(f"Success: 图表已生成并保存至 {output_path}")

    except Exception as e:
        print(f"Error: 图表生成失败 - {str(e)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()