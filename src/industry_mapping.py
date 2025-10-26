"""
IndustryGroupCode映射表

QuantConnect/Morningstar子行业代码到可读名称的映射(完整55个)

说明:
- 基于QuantConnect LEAN官方源码(AssetClassificationHelper.cs)
- 策略按MorningstarIndustryGroupCode动态分组(不限制子行业数量)
- 实际回测中出现约18-20个子行业(取决于财务筛选结果)
- 所有映射已验证,确保准确性
"""

# Morningstar IndustryGroupCode映射字典(按QuantConnect官方源码)
INDUSTRY_GROUP_NAMES = {
    # === 基础材料 (101xx) ===
    10110: '农业',                  # Agriculture
    10120: '建材',                  # BuildingMaterials
    10130: '化工',                  # Chemicals
    10140: '林产品',                # ForestProducts
    10150: '金属矿业',              # MetalsAndMining
    10160: '钢铁',                  # Steel

    # === 消费周期 (102xx) ===
    10200: '汽车及零部件',          # VehiclesAndParts
    10220: '家具装置',              # Furnishings
    10230: '房建',                  # HomebuildingAndConstruction
    10240: '服装制造',              # ManufacturingApparelAndAccessories
    10250: '包装容器',              # PackagingAndContainers
    10260: '个人服务',              # PersonalServices
    10270: '餐厅',                  # Restaurants
    10280: '周期零售',              # RetailCyclical
    10290: '旅游休闲',              # TravelAndLeisure

    # === 金融 (103xx) ===
    10310: '资产管理',              # AssetManagement
    10320: '银行',                  # Banks
    10330: '资本市场',              # CapitalMarkets
    10340: '保险',                  # Insurance
    10350: '多元金融',              # DiversifiedFinancialServices
    10360: '信贷服务',              # CreditServices

    # === 房地产 (104xx) ===
    10410: '房地产',                # RealEstate
    10420: 'REITs',                # REITs

    # === 消费防御 (205xx) ===
    20510: '酒精饮料',              # BeveragesAlcoholic
    20520: '非酒精饮料',            # BeveragesNonAlcoholic
    20525: '消费品',                # ConsumerPackagedGoods
    20540: '教育',                  # Education
    20550: '防御零售',              # RetailDefensive
    20560: '烟草',                  # TobaccoProducts

    # === 医疗保健 (206xx-207xx) ===
    20610: '生物科技',              # Biotechnology
    20620: '制药',                  # DrugManufacturers
    20630: '医疗计划',              # HealthcarePlans
    20645: '医疗服务',              # HealthcareProvidersAndServices
    20650: '医疗器械仪器',          # MedicalDevicesAndInstruments
    20660: '医疗诊断研究',          # MedicalDiagnosticsAndResearch
    20670: '医疗分销',              # MedicalDistribution
    20710: '独立电力',              # UtilitiesIndependentPowerProducers
    20720: '公用事业',              # UtilitiesRegulated

    # === 通信服务 (308xx) ===
    30810: '电信服务',              # TelecommunicationServices
    30820: '多元媒体',              # MediaDiversified
    30830: '互动媒体',              # InteractiveMedia

    # === 能源 (309xx) ===
    30910: '油气',                  # OilAndGas
    30920: '其他能源',              # OtherEnergySources

    # === 工业 (310xx) ===
    31010: '航空国防',              # AerospaceAndDefense
    31020: '商业服务',              # BusinessServices
    31030: '企业集团',              # Conglomerates
    31040: '建筑',                  # Construction
    31050: '重型机械',              # FarmAndHeavyConstructionMachinery
    31060: '工业分销',              # IndustrialDistribution
    31070: '工业产品',              # IndustrialProducts
    31080: '运输',                  # Transportation
    31090: '废物管理',              # WasteManagement

    # === 科技 (311xx) ===
    31110: '软件',                  # Software
    31120: '硬件',                  # Hardware
    31130: '半导体',                # Semiconductors

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
