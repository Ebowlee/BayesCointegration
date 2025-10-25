"""
IndustryGroupCode映射表

Morningstar子行业代码到可读名称的映射
基于回测中实际出现的26个子行业代码
"""

# Morningstar IndustryGroupCode映射字典
INDUSTRY_GROUP_NAMES = {
    # === 房地产 (104xx) ===
    10150: '房地产开发',
    10200: '房地产服务',
    10280: 'REIT-工业',
    10310: 'REIT-零售',
    10420: 'REIT-住宅',

    # === 消费防御 (205xx) ===
    20520: '包装食品',
    20525: '农产品',

    # === 医疗保健 (206xx-207xx) ===
    20620: '制药',
    20650: '生物科技',
    20720: '医疗器械',

    # === 通信服务 (308xx) ===
    30810: '电信服务',
    30830: '媒体娱乐',

    # === 能源 (309xx) ===
    30910: '石油天然气',

    # === 科技 (310xx-311xx) ===
    31080: '半导体',
    31110: '软件应用',
    31120: '软件基础设施',
    31130: '信息技术服务',

    # === 特殊处理 ===
    0: '未分类'  # 处理缺失或未分类数据
}


def get_industry_name(code: int) -> str:
    """
    获取行业名称

    Args:
        code: Morningstar IndustryGroupCode

    Returns:
        行业中文名称，未知代码返回'行业{code}'

    Examples:
        >>> get_industry_name(30910)
        '石油天然气'
        >>> get_industry_name(99999)
        '行业99999'
    """
    return INDUSTRY_GROUP_NAMES.get(code, f'行业{code}')


def get_industry_display(code: int, show_code: bool = True) -> str:
    """
    获取行业显示名称（可选是否显示代码）

    Args:
        code: Morningstar IndustryGroupCode
        show_code: 是否在名称后显示代码

    Returns:
        格式化的行业显示名称

    Examples:
        >>> get_industry_display(30910, show_code=True)
        '石油天然气(30910)'
        >>> get_industry_display(30910, show_code=False)
        '石油天然气'
    """
    name = get_industry_name(code)
    if show_code:
        return f"{name}({code})"
    return name
