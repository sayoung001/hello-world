"""
sector_map.py — GICS 기반 섹터 매핑 모듈
==========================================
[기능]
- 종목 Industry → GICS 섹터 → 섹터 ETF 매핑
- 3단계 Fallback: 정확 매칭 → Industry 테이블 → 키워드 매칭
"""

# ============================================================
# GICS 11 Sectors → 대표 ETF
# ============================================================

GICS_SECTOR_ETF = {
    'Information Technology': {'etf': 'XLK', 'name_kr': '정보기술'},
    'Communication Services': {'etf': 'XLC', 'name_kr': '커뮤니케이션'},
    'Health Care':            {'etf': 'XLV', 'name_kr': '헬스케어'},
    'Financials':             {'etf': 'XLF', 'name_kr': '금융'},
    'Consumer Discretionary': {'etf': 'XLY', 'name_kr': '임의소비재'},
    'Consumer Staples':       {'etf': 'XLP', 'name_kr': '필수소비재'},
    'Energy':                 {'etf': 'XLE', 'name_kr': '에너지'},
    'Industrials':            {'etf': 'XLI', 'name_kr': '산업재'},
    'Materials':              {'etf': 'XLB', 'name_kr': '소재'},
    'Real Estate':            {'etf': 'XLRE', 'name_kr': '부동산'},
    'Utilities':              {'etf': 'XLU', 'name_kr': '유틸리티'},
    # 서브섹터 (추가)
    'Semiconductors':         {'etf': 'SOXX', 'name_kr': '반도체'},
}

# ============================================================
# Industry → GICS Sector 매핑 (60+ entries)
# ============================================================

INDUSTRY_TO_SECTOR = {
    # Information Technology
    'Semiconductors': 'Information Technology',
    '반도체': 'Information Technology',
    '반도체 장비': 'Information Technology',
    'Semiconductor Equipment & Materials': 'Information Technology',
    'Software - Application': 'Information Technology',
    'Software - Infrastructure': 'Information Technology',
    '소프트웨어': 'Information Technology',
    '소프트웨어 및 IT 서비스': 'Information Technology',
    'Information Technology Services': 'Information Technology',
    'Electronic Components': 'Information Technology',
    '전자 장비 및 부품': 'Information Technology',
    '컴퓨터 하드웨어': 'Information Technology',
    'Computer Hardware': 'Information Technology',
    'Scientific & Technical Instruments': 'Information Technology',
    '과학 및 기술 기기': 'Information Technology',
    'Solar': 'Information Technology',
    '태양광': 'Information Technology',

    # Communication Services
    'Internet Content & Information': 'Communication Services',
    '인터넷 콘텐츠 및 정보': 'Communication Services',
    'Entertainment': 'Communication Services',
    '엔터테인먼트': 'Communication Services',
    'Electronic Gaming & Multimedia': 'Communication Services',
    '전자 게임 및 멀티미디어': 'Communication Services',
    'Advertising Agencies': 'Communication Services',
    '광고 대행사': 'Communication Services',
    'Telecom Services': 'Communication Services',
    '통신 서비스': 'Communication Services',
    'Publishing': 'Communication Services',

    # Health Care
    'Biotechnology': 'Health Care',
    '바이오테크놀로지': 'Health Care',
    'Drug Manufacturers': 'Health Care',
    '의약품 제조': 'Health Care',
    'Medical Devices': 'Health Care',
    '의료 기기': 'Health Care',
    '의료 시설 및 서비스': 'Health Care',
    'Medical Care Facilities': 'Health Care',
    'Health Information Services': 'Health Care',
    '건강 정보 서비스': 'Health Care',
    'Diagnostics & Research': 'Health Care',
    '진단 및 연구': 'Health Care',
    'Pharmaceutical Retailers': 'Health Care',

    # Financials
    'Banks - Regional': 'Financials',
    '지역 은행': 'Financials',
    'Insurance': 'Financials',
    '보험': 'Financials',
    'Capital Markets': 'Financials',
    '자본 시장': 'Financials',
    'Financial Data & Stock Exchanges': 'Financials',
    '금융 데이터': 'Financials',
    'Asset Management': 'Financials',
    '자산 관리': 'Financials',
    'Credit Services': 'Financials',
    '신용 서비스': 'Financials',
    'Mortgage Finance': 'Financials',
    'Financial Conglomerates': 'Financials',

    # Consumer Discretionary
    'Auto Manufacturers': 'Consumer Discretionary',
    '자동차': 'Consumer Discretionary',
    '자동차 제조': 'Consumer Discretionary',
    'Restaurants': 'Consumer Discretionary',
    '레스토랑': 'Consumer Discretionary',
    'Apparel Manufacturing': 'Consumer Discretionary',
    '의류 및 액세서리': 'Consumer Discretionary',
    'Internet Retail': 'Consumer Discretionary',
    '인터넷 소매': 'Consumer Discretionary',
    'Specialty Retail': 'Consumer Discretionary',
    '전문 소매': 'Consumer Discretionary',
    'Resorts & Casinos': 'Consumer Discretionary',
    '리조트 및 카지노': 'Consumer Discretionary',
    'Travel Services': 'Consumer Discretionary',
    '여행 서비스': 'Consumer Discretionary',
    'Leisure': 'Consumer Discretionary',
    'Home Improvement Retail': 'Consumer Discretionary',
    'Residential Construction': 'Consumer Discretionary',
    'Luxury Goods': 'Consumer Discretionary',

    # Consumer Staples
    'Packaged Foods': 'Consumer Staples',
    '식품 가공': 'Consumer Staples',
    'Beverages': 'Consumer Staples',
    '음료': 'Consumer Staples',
    'Household & Personal Products': 'Consumer Staples',
    '생활 용품': 'Consumer Staples',
    'Tobacco': 'Consumer Staples',
    'Grocery Stores': 'Consumer Staples',
    'Farm Products': 'Consumer Staples',
    'Discount Stores': 'Consumer Staples',

    # Energy
    'Oil & Gas E&P': 'Energy',
    '석유 및 가스': 'Energy',
    'Oil & Gas Midstream': 'Energy',
    'Oil & Gas Integrated': 'Energy',
    'Oil & Gas Equipment & Services': 'Energy',
    '석유 장비': 'Energy',
    'Uranium': 'Energy',
    '우라늄': 'Energy',

    # Industrials
    'Aerospace & Defense': 'Industrials',
    '항공우주 및 방위': 'Industrials',
    '방위산업': 'Industrials',
    'Industrial Distribution': 'Industrials',
    '산업 유통': 'Industrials',
    'Waste Management': 'Industrials',
    '폐기물 관리': 'Industrials',
    'Railroads': 'Industrials',
    '철도': 'Industrials',
    'Airlines': 'Industrials',
    '항공사': 'Industrials',
    'Trucking': 'Industrials',
    'Engineering & Construction': 'Industrials',
    '건설': 'Industrials',
    'Specialty Industrial Machinery': 'Industrials',
    '중장비 및 차량': 'Industrials',
    'Conglomerates': 'Industrials',
    '경영 지원 서비스': 'Industrials',
    '해양 화물 및 물류': 'Industrials',
    'Staffing & Employment Services': 'Industrials',
    'Marine Shipping': 'Industrials',
    'Rental & Leasing Services': 'Industrials',
    'Security & Protection Services': 'Industrials',

    # Materials
    'Gold': 'Materials',
    '금': 'Materials',
    'Copper': 'Materials',
    '구리': 'Materials',
    'Steel': 'Materials',
    '철강': 'Materials',
    'Specialty Chemicals': 'Materials',
    '특수 화학': 'Materials',
    'Building Materials': 'Materials',
    '건축 자재': 'Materials',
    'Lumber & Wood Production': 'Materials',
    'Other Industrial Metals & Mining': 'Materials',

    # Real Estate
    'REIT': 'Real Estate',
    'REIT - Residential': 'Real Estate',
    'REIT - Industrial': 'Real Estate',
    'REIT - Healthcare Facilities': 'Real Estate',
    'Real Estate Services': 'Real Estate',
    '부동산': 'Real Estate',
    'REIT - Specialty': 'Real Estate',
    'REIT - Office': 'Real Estate',
    'REIT - Retail': 'Real Estate',
    'REIT - Diversified': 'Real Estate',
    'REIT - Mortgage': 'Real Estate',
    'REIT - Hotel & Motel': 'Real Estate',
    'Real Estate - Development': 'Real Estate',
    'Real Estate - Diversified': 'Real Estate',

    # Utilities
    'Utilities - Regulated Electric': 'Utilities',
    '전력': 'Utilities',
    'Utilities - Renewable': 'Utilities',
    '신재생 에너지': 'Utilities',
    'Utilities - Diversified': 'Utilities',
    'Utilities - Independent Power Producers': 'Utilities',
    'Utilities - Regulated Gas': 'Utilities',
    'Utilities - Regulated Water': 'Utilities',
}

# ============================================================
# 키워드 Fallback
# ============================================================

_KEYWORD_SECTOR = {
    'semi': 'Information Technology',
    'chip': 'Information Technology',
    'software': 'Information Technology',
    'cloud': 'Information Technology',
    'cyber': 'Information Technology',
    'tech': 'Information Technology',
    'AI': 'Information Technology',
    'data center': 'Information Technology',
    'solar': 'Information Technology',
    'bio': 'Health Care',
    'pharma': 'Health Care',
    'drug': 'Health Care',
    'medical': 'Health Care',
    'health': 'Health Care',
    'bank': 'Financials',
    'insurance': 'Financials',
    'financial': 'Financials',
    'auto': 'Consumer Discretionary',
    'retail': 'Consumer Discretionary',
    'restaurant': 'Consumer Discretionary',
    'hotel': 'Consumer Discretionary',
    'food': 'Consumer Staples',
    'beverage': 'Consumer Staples',
    'oil': 'Energy',
    'gas': 'Energy',
    'energy': 'Energy',
    'mining': 'Materials',
    'steel': 'Materials',
    'gold': 'Materials',
    'chemical': 'Materials',
    'defense': 'Industrials',
    'aerospace': 'Industrials',
    'industrial': 'Industrials',
    'construction': 'Industrials',
    'logistics': 'Industrials',
    'shipping': 'Industrials',
    'REIT': 'Real Estate',
    'property': 'Real Estate',
    'utility': 'Utilities',
    'electric': 'Utilities',
}


def get_sector_etf(ticker, industry=''):
    """
    종목의 섹터 ETF 조회 (3단계 Fallback)
    
    Returns: (etf_ticker, sector_name_kr) tuple
    """
    industry = str(industry).strip() if industry else ''

    # 1단계: 정확한 Industry 매칭
    if industry in INDUSTRY_TO_SECTOR:
        sector = INDUSTRY_TO_SECTOR[industry]
        info = GICS_SECTOR_ETF.get(sector, {})
        return info.get('etf', '-'), info.get('name_kr', '-')

    # 2단계: 키워드 매칭
    industry_lower = industry.lower()
    for keyword, sector in _KEYWORD_SECTOR.items():
        if keyword.lower() in industry_lower:
            info = GICS_SECTOR_ETF.get(sector, {})
            return info.get('etf', '-'), info.get('name_kr', '-')

    return '-', '-'


# ============================================================
# 한국 시장 섹터 ETF
# ============================================================

KR_SECTOR_ETF = {
    '반도체':       {'etf': '091160', 'name': 'KODEX 반도체'},
    '은행':         {'etf': '091170', 'name': 'KODEX 은행'},
    '자동차':       {'etf': '091180', 'name': 'KODEX 자동차'},
    '건설':         {'etf': '117700', 'name': 'KODEX 건설'},
    '에너지화학':   {'etf': '117460', 'name': 'KODEX 에너지화학'},
    '바이오':       {'etf': '266370', 'name': 'KODEX 바이오'},
    '2차전지':      {'etf': '305720', 'name': 'KODEX 2차전지'},
    'IT':           {'etf': '261060', 'name': 'KODEX IT'},
}
