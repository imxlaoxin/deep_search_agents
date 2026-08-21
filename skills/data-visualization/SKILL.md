---
name: data-visualization
version: 1.0
description: 运行Python脚本生成数据图表（柱状图、折线图、饼图）。
trigger: ["对比数据", "趋势图", "数据可视化", "生成图表", "画图", "占比分析", "柱状图"]
tools: ["execute"]
author: system-admin
---

# 数据可视化与图表生成技能

## 🚨 致命错误警告（必须绝对遵守）
1. **绝对不允许自己编造 `--data` 和 JSON 数据！** 脚本不接受 JSON！你必须严格使用 `--x_labels` 和 `--y_values` 来传递逗号分隔的字符串！
2. **严禁外包！** 主智能体必须亲自调用 `execute` 工具的 `command` 参数来运行命令。

## 执行步骤
1. **调用脚本**：使用 `execute` 工具的 `command` 参数，严格替换下方模板中的尖括号内容：

   `python skills/data-visualization/scripts/generate_chart.py --type <图表类型> --title "<标题>" --x_labels "<标签1,标签2>" --y_values "<数值1,数值2>" --output "<工作目录>/<纯文件名.png>"`

   ✅ **正确命令示例**（注意 `--output` 必须带上你的工作目录前缀 `output/`）：
   `python skills/data-visualization/scripts/generate_chart.py --type bar --title "2025年Top5" --x_labels "连花清瘟,达菲" --y_values "1200000,700000" --output "output/sales_chart.png"`

2. **验证输出**：执行上述命令后，不报错即代表成功生成。

