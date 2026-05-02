"""키워드 기반 chapter 자동 분류 (API 키 없을 때 baseline).
사용자 정의 15개 세부 과목 기준 (2026-05-02 결정):
  생화학, 미생물학, 병태생리학, 예방약학, 약물학,
  약제학, 물리약학, 약품분석학, 약물합성학, 생약학, 한약제제학,
  약물치료학(질환별 sub-chapter 매칭),
  약국실무, 사회약학, 약사법규
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import get_conn  # noqa: E402

# (chapter_name, [keywords]) — 우선순위 위 → 아래
KEYWORD_RULES_BY_SUBJECT: dict[str, list[tuple[str, list[str]]]] = {
    "생명약학": [
        ("미생물학", [
            "세균", "그람양성", "그람음성", "바이러스", "virus", "포도구균", "연쇄구균",
            "마이코플라즈마", "리스테리아", "Candida", "Plasmodium", "Treponema", "Mycoplasma",
            "Bacillus", "Clostridium", "Salmonella", "Shigella", "Escherichia",
            "Staphylococcus", "Streptococcus", "Neisseria", "곰팡이", "진균",
            "박테리오파지", "원충", "콜레라균", "탄저균", "디프테리아", "백일해",
            "HIV", "CMV", "거대세포바이러스", "헤르페스", "B형 간염", "C형 간염",
            "인플루엔자", "코로나", "rotavirus", "polyomavirus", "papillomavirus",
            "Epstein-Barr", "기생충", "회충", "촌충", "흡충",
            "MHC", "T 세포", "T세포", "B세포", "B 세포", "항체", "면역", "Ig", "TLR",
            "IL-", "사이토카인", "수지상세포", "백신", "보체", "케모카인",
            "비만세포", "단클론항체", "면역억제제", "면역결핍",
        ]),
        ("생화학", [
            "단백질", "효소", "ATP", "TCA", "당분해", "glycolysis", "콜라겐", "케라틴",
            "지방산", "콜레스테롤", "인슐린의 작용", "포도당", "아미노산", "글리신", "글루타민",
            "리신", "메티오닌", "트립토판", "기질", "코엔자임", "보조효소", "아세틸",
            "메발론산", "퓨린", "피리미딘", "당신생", "케톤체", "비타민", "니아신",
            "비오틴", "엽산", "헤모글로빈", "사이트룰린", "오르니틴", "요소회로",
            "베타산화", "옥살로아세트", "시트르산", "글리코겐", "DNA", "RNA",
            "전사", "번역", "역전사", "스플라이싱", "프로모터", "히스톤", "유전자",
            "텔로미어", "리보솜", "엑손", "인트론", "polymerase", "ribozyme",
            "메틸화", "캡핑", "siRNA", "miRNA", "BRCA", "BCR-ABL",
        ]),
        ("병태생리학", [
            "부종", "심부전", "협심증", "고혈압", "당뇨", "심근", "맥압", "영아",
            "신장", "간세포", "폐", "혈전", "암", "심박출량",
            "사구체", "이뇨", "혈관확장", "혈관수축", "골밀도", "Wilson병",
            "Cushing", "Hunter", "Graves", "다발경화증", "중증근무력증", "디조지",
            "류마티스", "가족성샘종성", "자가면역", "Mallory", "괴사", "괴저",
        ]),
        ("예방약학", [
            "예방", "공중", "역학", "선별검사", "위양성", "위음성", "민감도",
            "특이도", "발생률", "유병률", "산업보건", "감염병", "사회보장",
            "독성", "납", "비소", "카드뮴", "라돈", "벤젠", "톨루엔", "포름알데히드",
            "유해", "발암", "톡신", "중독", "오염", "살충제", "농약", "수은", "크롬",
            "오크라톡신", "파튤린", "아플라톡신", "메탄올", "디설피람", "테트로도톡신",
            "오카다익", "고냐톡신", "베네루핀", "팔로이딘", "아마니틴",
            "프랄리독심", "atropine", "식품첨가물",
        ]),
        ("약물학", [
            "수용체", "GABA", "도파민", "세로토닌", "노르에피네프린", "아드레날린",
            "베타", "알파", "콜린", "무스카린", "니코틴", "약리작용", "효능약", "길항약",
            "G단백질", "이차전령", "EGFR", "tyrosine kinase", "신경전달",
            "통로", "차단제", "효현제", "agonist", "antagonist", "수용체 길항",
        ]),
    ],
    "산업약학": [
        ("약품분석학", [
            "크로마토그래피", "HPLC", "GC", "질량분석", "MS", "NMR", "IR", "UV",
            "분광", "전기영동", "검량선", "검출한계", "정량한계", "선택성", "선형성",
            "검출기", "이동상", "정성", "정량", "기기분석",
        ]),
        ("약물합성학", [
            "치환기", "친전자성", "친핵성", "라디칼", "산화환원", "보호기",
            "유기합성", "구조 결정", "구조-활성", "SAR", "리간드", "약물설계",
            "프로드러그", "키랄", "광학이성질체", "토실레이트", "메실레이트",
            "친수성", "소수성", "친전자치환", "친핵치환", "에스테르화",
        ]),
        ("약제학", [
            "제제", "제형", "정제", "캡슐", "주사제", "현탁", "유제", "유화", "분말",
            "방출", "용출", "방수", "캡슐화", "리포좀", "마이크로", "나노", "서방",
            "바이오어베일러빌리티", "생체이용률", "TDDS", "패치", "에어로졸", "흡입제",
            "좌제", "코팅",
        ]),
        ("물리약학", [
            "용해도", "확산", "삼투", "점도", "표면장력", "유변", "결정", "다형성",
            "수화물", "안정성", "분배계수", "이온화", "pKa", "완충", "친수성",
            "콜로이드", "에멀젼", "현탁성",
        ]),
        ("생약학", [
            "생약", "한약", "약용식물", "기원", "성상", "감별",
            "테르펜", "알칼로이드", "플라보노이드", "사포닌", "탄닌", "쿠마린",
            "글리코사이드", "추출물", "천연",
        ]),
        ("한약제제학", [
            "한방", "한약 처방", "탕제", "전탕", "환제", "산제 (한약)",
        ]),
    ],
    "임상·실무약학1": [
        # 약물치료학 sub-chapters는 별도 매칭 (DRUG_THERAPY_KEYWORDS)
        ("약물학", [
            "수용체", "약리작용", "효능약", "길항약", "agonist", "antagonist",
        ]),
    ],
    "임상·실무약학2": [
        ("약국실무", [
            "조제", "복약지도", "처방전", "약국", "조제실", "라벨", "임상시험",
            "포장", "저장", "유효기간", "약국 운영",
        ]),
        ("사회약학", [
            "보험", "급여", "약가", "의약품정책", "윤리",
            "제3자 지급", "건강보험", "사회보장",
        ]),
    ],
    "보건·의약관계법규": [
        ("약사법규", []),  # 모든 법규 문제 → 약사법규
    ],
}

# 약치 질환 → 단원명 매핑 (chapter_name in DB)
DRUG_THERAPY_KEYWORDS = {
    "고혈압": ["고혈압", "혈압", "ACE 억제제", "ARB", "이뇨제 고혈압", "베타차단제 고혈압", "칼슘채널차단제"],
    "이상지질혈증": ["이상지질", "콜레스테롤", "스타틴", "트리글리세라이드", "LDL", "HDL"],
    "심부전": ["심부전", "박출률", "디곡신", "HFrEF", "심박출량 감소"],
    "부정맥": ["부정맥", "심방세동", "심실세동", "아미오다론", "심방조동"],
    "안정허혈심장병": ["협심증", "안정형 협심증", "허혈성 심장병", "니트로글리세린 안정"],
    "급성관상동맥증후군": ["심근경색", "ACS", "STEMI", "NSTEMI"],
    "정맥혈전색전증": ["정맥혈전", "DVT", "폐색전", "와파린", "헤파린 항응고"],
    "뇌졸중": ["뇌졸중", "허혈성 뇌졸중", "출혈성 뇌졸중", "tPA", "알테플라제"],
    "감염치료의 원칙": ["항생제 원칙", "항균제 선택", "감수성 검사"],
    "상기도감염": ["상기도", "감기", "인후염"],
    "폐렴": ["폐렴", "호흡기 감염"],
    "위장관 및 복강내감염": ["위장관 감염", "복강내 감염"],
    "요로감염": ["요로감염", "방광염", "신우신염"],
    "뇌수막염": ["뇌수막염", "수막염"],
    "결핵": ["결핵", "이소니아지드", "리팜핀"],
    "체액 및 전해질 불균형": ["전해질", "나트륨 불균형", "칼륨 불균형", "탈수", "수분 불균형"],
    "산염기장애": ["산-염기", "산염기", "대사성 산증", "대사성 알칼리증", "호흡성 산증", "호흡성 알칼리증"],
    "약물유발 신장질환": ["약물유발 신", "신독성", "조영제 신증"],
    "급성신손상": ["급성신손상", "AKI", "급성신부전"],
    "만성신장병": ["만성신장병", "CKD", "투석"],
    "신장이식": ["신장이식", "이식 거부", "면역억제제 이식"],
    "양성전립샘비대": ["전립샘비대", "BPH"],
    "요실금": ["요실금"],
    "건선": ["건선"],
    "녹내장": ["녹내장", "안압"],
    "T1DM": ["1형 당뇨", "1형당뇨", "T1DM"],
    "T2DM": ["2형 당뇨", "2형당뇨", "T2DM", "메트포르민", "인슐린 저항"],
    "갑상선질환": ["갑상선", "TSH", "갑상선기능항진", "갑상선기능저하"],
    "위식도역류병": ["위식도역류", "GERD", "PPI"],
    "소화성궤양": ["소화성궤양", "위궤양", "십이지장궤양", "헬리코박터"],
    "IBD": ["크론병", "궤양성 대장염", "IBD"],
    "간경화": ["간경화", "복수", "자발세균복막염"],
    "바이러스간염": ["B형 간염 치료", "C형 간염 치료", "바이러스 간염 치료"],
    "골관절염(OA)": ["골관절염", "OA"],
    "류마티스관절염(RA)": ["류마티스 관절염", "RA", "메토트렉세이트"],
    "Allergic rhinitis": ["알레르기 비염"],
    "Asthma": ["천식", "asthma", "기관지천식"],
    "COPD": ["COPD", "만성폐쇄성"],
    "조현병": ["조현병", "정신분열"],
    "우울장애": ["우울증", "우울장애", "SSRI"],
    "양극성장애": ["양극성", "조울"],
    "불안장애": ["불안장애", "공황"],
    "수면각성장애": ["불면증", "수면장애"],
    "ADHD": ["ADHD", "주의력결핍"],
    "치매(인지증)": ["치매", "알츠하이머"],
    "파킨슨병": ["파킨슨"],
    "뇌전증": ["뇌전증", "간질", "발작"],
    "편두통": ["편두통", "트립탄"],
    "종양약료 기본": ["종양 치료의 원칙", "암 치료의 원칙", "화학요법 원칙"],
    "암 보조요법": ["암 보조"],
    "대장암": ["대장암"],
    "폐암": ["폐암"],
    "유방암": ["유방암"],
    "백혈병": ["백혈병", "AML", "CML", "ALL", "CLL"],
    "폐경": ["폐경", "에스트로겐 보충"],
    "피임": ["피임", "경구피임"],
}


def build_chapter_index(conn) -> dict:
    """{(subject_name, chapter_name): chapter_id}"""
    out = {}
    rows = conn.execute("""
        SELECT s.name AS subject, c.name AS chapter, c.id
        FROM chapters c JOIN subjects s ON c.subject_id = s.id
    """).fetchall()
    for r in rows:
        out[(r["subject"], r["chapter"])] = r["id"]
    return out


def classify_one(subject_name: str, body: str, chapter_index: dict) -> int | None:
    if not body:
        return None

    # 1) 임상·실무약학 → 약치 질환별 단원 우선 시도
    if subject_name.startswith("임상·실무약학"):
        for chap_name, kws in DRUG_THERAPY_KEYWORDS.items():
            for kw in kws:
                if kw in body:
                    cid = chapter_index.get(("약물치료학(가상)", chap_name))
                    if cid:
                        return cid

    # 2) 시험과목별 일반 매핑
    rules = KEYWORD_RULES_BY_SUBJECT.get(subject_name, [])
    for chap_name, kws in rules:
        # 키워드 비어있는 chapter는 default fallback (예: 약사법규)
        if not kws:
            cid = chapter_index.get((subject_name, chap_name))
            if cid:
                return cid
            continue
        for kw in kws:
            if kw in body:
                cid = chapter_index.get((subject_name, chap_name))
                if cid:
                    return cid
    return None


def classify_year(year: int, force: bool = False) -> dict:
    with get_conn() as conn:
        index = build_chapter_index(conn)
        where = "AND q.chapter_id IS NULL" if not force else ""
        rows = conn.execute(f"""
            SELECT q.id, q.body, s.name AS subject
            FROM questions q
            JOIN exams e ON q.exam_id = e.id
            JOIN subjects s ON q.subject_id = s.id
            WHERE e.year = ? {where} AND s.name != '약물치료학(가상)'
        """, (year,)).fetchall()
        total = len(rows)
        hit = 0
        per_subject: dict[str, int] = {}
        for r in rows:
            cid = classify_one(r["subject"], r["body"] or "", index)
            if cid:
                conn.execute("UPDATE questions SET chapter_id = ? WHERE id = ?", (cid, r["id"]))
                hit += 1
                per_subject[r["subject"]] = per_subject.get(r["subject"], 0) + 1
        return {"total": total, "hit": hit, "per_subject": per_subject}


if __name__ == "__main__":
    args = sys.argv[1:]
    year = int(args[0]) if args else 2024
    force = "--force" in args
    res = classify_year(year, force=force)
    print(f"키워드 분류: {res['hit']}/{res['total']} 매칭")
    for s, n in sorted(res["per_subject"].items()):
        print(f"  - {s}: {n}")
