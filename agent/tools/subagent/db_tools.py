import json
import os

from dotenv import load_dotenv, find_dotenv
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from mysql.connector import connect, Error

from api.context import get_thread_context
from api.monitor import monitor

load_dotenv(find_dotenv())


# 加载配置文件方便后续使用
def get_db_config():
    """Get database configuration from environment variables."""
    config = {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": os.getenv("MYSQL_DATABASE"),
        "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
        "collation": os.getenv("MYSQL_COLLATION", "utf8mb4_unicode_ci"),
        "autocommit": True,
        "sql_mode": os.getenv("MYSQL_SQL_MODE", "TRADITIONAL")
    }
    # 移除 None 值（核心必要操作）
    config = {k: v for k, v in config.items() if v is not None}

    # 补充：校验核心配置是否存在（可选但推荐）
    required_keys = ["user", "password", "database"]
    missing_keys = [k for k in required_keys if k not in config]
    if missing_keys:
        raise ValueError(f"缺失数据库核心配置：{', '.join(missing_keys)}")

    return config


@tool
def list_database_tables(config: RunnableConfig = None) -> str:
    """
    列出mysql数据库中所有可用的表，让模型明确知道库中创建了哪些表.
    :return: 返回表名数据 返回格式为json数组
    """
    thread_id = get_thread_context() or "default"
    configurable = config.get("configurable", {}) if config else {}
    raw_ns = configurable.get("checkpoint_ns", "main")
    stable_subagent_id = raw_ns.split("|")[0]
    cur_subagent_id = f"{thread_id}_{stable_subagent_id}"

    monitor.report_tool('list_database_tables', args={'cur_subagent_id': cur_subagent_id})
    config = get_db_config()
    try:
        with connect(**config) as conn:
            with conn.cursor() as cursor:
                cursor.execute('show tables')
                ret = cursor.fetchall()
                if not ret:
                    return '数据库中没有可用的表'
                tables = [item[0] for item in ret]
                return json.dumps(tables, ensure_ascii=False)

    except Error as e:
        return f'list_database_tables操作异常，异常信息：{str(e)}'


@tool
def get_table_data(table_name: str, config: RunnableConfig = None) -> str:
    """
    获取数据库指定表的结构信息（字段名、数据类型）以及少量的实例数据
    注意: 在调用此工具时，需要先进行前置操作：调用list_database_tables工具，了解数据库有哪些表.
    :param table_name: 指定需要查询的表名
    :return: 返回表数据&表字段 返回格式为csv格式
    """
    thread_id = get_thread_context() or "default"
    configurable = config.get("configurable", {}) if config else {}
    raw_ns = configurable.get("checkpoint_ns", "main")
    stable_subagent_id = raw_ns.split("|")[0]
    cur_subagent_id = f"{thread_id}_{stable_subagent_id}"

    monitor.report_tool('get_table_data', args={'table_name': table_name, 'cur_subagent_id': cur_subagent_id})
    config = get_db_config()
    try:
        with connect(**config) as conn:
            with conn.cursor() as cursor:
                # 1. 获取字段类型信息，让模型感知数据类型
                cursor.execute(f"DESCRIBE `{table_name}`;")
                schema_info = cursor.fetchall()
                schema_desc = "【表结构说明】:\n" + "\n".join(
                    [f'- 列名: {row[0]}，类型: {row[1]}，是否允许为空: {row[2]}' for row in schema_info])
                # print(schema_desc)
                # 2. 获取x条示例数据，让模型感知数据形态
                cursor.execute(f'select * from {table_name} limit 5;')
                table_desc = cursor.description
                if not table_desc:
                    # 如果table_desc为None，则表中也不会有数据
                    return f'{table_name}表为空'
                table_desc = '【示例数据】:\n' + ','.join([column_row[0] for column_row in table_desc])
                table_data = cursor.fetchall()
                table_data = '\n'.join([','.join(map(str, row)) for row in table_data])
                return f'{schema_desc}\n{table_desc}\n{table_data}'

    except Error as e:
        return f'get_table_data操作异常，异常信息：{str(e)}'


_db_counters = {}  # 数据库查询物理计数器


@tool
def execute_sql_query(sql: str, reflection_on_previous: str = "初次执行无需反思", config: RunnableConfig = None):
    """
    执行自定义sql查询
    当需要复杂的筛选、联接或聚合时使用此工具.
    注意：
        1. 在调用此工具之前需要先调用工具list_database_tables了解数据库可用表，然后调用工具get_table_data快速预览表字段和表数据.
        2. 为了保护系统，请确保只执行查询语句，且结果集会自动限制为前100条
    参数说明：
        sql: 要执行的 SELECT 语句。
        reflection_on_previous: 强制要求！如果上一次 SQL 报错或查无数据，你必须在此参数中填写反思（例如：“上次由于字段名错写为A报错，这次修正为B”）。
    :return: 返回自定义sql查询结果 返回格式为csv格式
    """
    thread_id = get_thread_context() or "default"
    # 从 LangGraph 上下文中提取当前子 Agent 调用的唯一命名空间 (namespace)
    configurable = config.get("configurable", {}) if config else {}
    raw_ns = configurable.get("checkpoint_ns", "main")
    # 切分调用栈，只保留第一层的稳定任务 ID
    stable_subagent_id = raw_ns.split("|")[0]

    # 构造针对“本次子 Agent 任务”的独立 Key
    cur_subagent_id = f"{thread_id}_{stable_subagent_id}"
    current_count = _db_counters.get(cur_subagent_id, 0)

    # 物理拦截：最多允许 LLM 纠错重试 2 次
    if current_count >= 2:
        _db_counters[cur_subagent_id] = 0
        monitor.report_tool("execute_sql_query-system_warning", {"警告": "触发物理熔断，终止SQL重试"})
        return "【系统强制拦截】SQL纠错重试已达3次上限！禁止再调用查询工具！请根据现有信息回答，或直接告知用户数据库中缺乏对应数据。"

    _db_counters[cur_subagent_id] = current_count + 1


    monitor.report_tool('execute_sql_query', args={
        'sql': sql,
        'reflection_on_previous': reflection_on_previous,
        'search_count': f'{current_count}',
        'cur_subagent_id': cur_subagent_id
    })
    config = get_db_config()
    if not sql.strip().lower().startswith('select'):
        return '错误: 该工具仅支持执行SELECT查询语句.'
    try:
        with connect(**config) as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql)
                table_desc = cursor.description
                if not table_desc:
                    # 如果table_desc为None，则表中也不会有数据
                    return f'sql执行成功，但没有返回任何数据列'
                table_desc = ','.join([column_row[0] for column_row in table_desc])
                # 限制读取前100条
                table_data = cursor.fetchmany(100)
                table_data = '\n'.join([','.join(map(str, row)) for row in table_data])
                if not table_data or len(table_data) == 0:
                    return 'sql执行成功，但没有返回任何数据行'
                return f'{table_desc}\n{table_data}'

    except Error as e:
        return f'execute_sql_query操作异常，异常信息：{str(e)}'


if __name__ == '__main__':
    print(list_database_tables())   # ["drugs", "inventory", "sales_records"]
    # print(get_table_data('drugs'))
    # print(execute_sql_query('select * from drugs d left join inventory i on i.drug_id = d.drug_id'))

"""
get_table_data 返回:
【表结构说明】:
- 列名: sale_id，类型: int，是否允许为空: NO
- 列名: drug_id，类型: int，是否允许为空: NO
- 列名: sale_date，类型: date，是否允许为空: NO
- 列名: quantity_sold，类型: int，是否允许为空: NO
- 列名: unit_price，类型: decimal(10,2)，是否允许为空: YES
- 列名: total_amount，类型: decimal(15,2)，是否允许为空: YES
- 列名: customer_name，类型: varchar(100)，是否允许为空: YES
- 列名: region，类型: varchar(50)，是否允许为空: YES
- 列名: sales_rep，类型: varchar(50)，是否允许为空: YES
【示例数据】:
sale_id,drug_id,sale_date,quantity_sold,unit_price,total_amount,customer_name,region,sales_rep
1,1,2025-02-15,200,25.00,5000.00,北京朝阳医院,华北区,北京朝阳销售部
2,1,2025-08-10,500,24.50,12250.00,天津大药房,华北区,天津南开销售分部
3,2,2025-01-20,1000,15.00,15000.00,海王星辰连锁,华东区,杭州滨江销售部
4,2,2025-12-05,5000,15.00,75000.00,上海华山医院,华东区,上海静安销售总部
5,3,2025-03-10,300,35.00,10500.00,广州中山医院,华南区,广州越秀销售部

一、 柱状图 (Bar Chart) 测试提问
销售额对比测试（多表 JOIN + GROUP BY + 柱状图）
    “请统计2025年销售总金额排名前 5 的药品名称及其对应的总销售额，并画一个柱状图对比它们的数据，生成一份分析报告。” 
区域销售对比测试（分类统计 + 柱状图）
    “帮我分析一下各个销售区域（region）的药品销售总金额，并生成柱状图进行对比展示。”  
二、 饼图 (Pie Chart) 测试提问
市场份额/占比测试（占比计算 + 饼图）
    “请分析目前仓库中各类治疗领域（therapeutic_area）的药品库存总量占比情况，输出一份数据可视化分析，并附上占比饼图。”  
仓库分布占比测试
    "连花清瘟胶囊在各个仓库/库区的库存分布情况如何？请做个占比分析图表。"
折线图 (Line Chart) 测试提问
时间趋势测试（时间序列 + 折线图）
    "请按销售日期（sale_date）汇总2025年各月份的药品销售总额变化情况，画出趋势图并给出销售趋势分析。"
"""
