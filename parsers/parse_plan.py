"""얼리버드 계획표 PDF에서 추출한 일정을 study_plan 테이블로 시드."""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DB_PATH  # noqa: E402

# 시험일 (78회 약사국시 추정 — 2027년 1월 셋째주 금요일 가정)
EXAM_DATE = "2027-01-15"

# 약치 단원명 → chapter_id (강의자료 56단원 기반)
DRUG_THERAPY_MAP = {
    "고혈압": "고혈압", "심부전": "심부전", "허혈성심질환": "안정허혈심장병",
    "관상동맥증후군": "급성관상동맥증후군", "부정맥": "부정맥", "이상지질혈증": "이상지질혈증",
    "뇌졸중,정맥혈전": "뇌졸중", "체액 불균형": "체액 및 전해질 불균형",
    "산염기장애": "산염기장애", "약물유발신질환": "약물유발 신장질환",
    "신장병": "만성신장병", "급성신손상": "급성신손상", "만성신장병": "만성신장병",
    "전립선비대": "양성전립샘비대", "COPD": "COPD", "알레르기비염": "Allergic rhinitis",
    "소화성궤양": "소화성궤양", "위식도역류": "위식도역류병",
    "변비,설사,과민성": None, "염증성장질환": "IBD", "간경변": "간경화",
    "바이러스간염": "바이러스간염", "당뇨": "T2DM", "당뇨,갑상선": "갑상선질환",
    "상기도감염": "상기도감염", "폐렴": "폐렴", "요로감염": "요로감염",
    "결핵": "결핵", "뇌수막염": "뇌수막염", "위장관감염": "위장관 및 복강내감염",
    "진균감염": "진균감염_표재성-심부", "두통": "편두통", "뇌전증": "뇌전증",
    "알츠하이머,파킨슨": "파킨슨병", "우울장애": "우울장애", "조현,양극성": "조현병",
    "수면장애": "수면각성장애", "범불안 장애": "불안장애",
    "소아기정신장애": "ADHD", "골관절염,": "골관절염(OA)", "류마티스": "류마티스관절염(RA)",
    "골다공증": None, "통풍": None, "폐경": "폐경, 피임", "피임": "폐경, 피임",
    "요실금": "요실금", "폐암": "폐암", "대장암": "대장암", "유방암": "유방암",
    "백혈병": "백혈병", "보조적치료": "암 보조요법",
    "아토피피부염": None, "건선": "건선", "여드름": None,
    "녹내장": "녹내장", "임상영양": None,
}

# 67일 일정 (PDF에서 직접 추출)
PLAN_DATA = [
    # (date_str, 합성, 생약, 약치, 약법)
    ("2025-08-04", "Ketamine, Etomidate", "갈근, 감초, 길경", "고혈압", "주교재 1쪽"),
    ("2025-08-05", "Alfentanil Phenobarbital", "길초근, 단삼, 당귀", "심부전", "주교재 2쪽"),
    ("2025-08-06", "Zolpidem Triazolam", "백지, 부자, 세네가", "허혈성심질환", "주교재 3쪽"),
    ("2025-08-07", "Chorpromazine, Risperidone", "시호, 용담, 겐티아", "관상동맥증후군", "주교재 4쪽"),
    ("2025-08-08", "Olanzapine, Sulpiride", "우슬, 울금, 원지", "부정맥", "주교재 5쪽"),
    ("2025-08-09", "Sodium Valproate, Baclofen", None, "이상지질혈증", None),
    ("2025-08-11", "Carbamazepine, Lamotrigine", "인도, 인삼, 홍삼", "뇌졸중,정맥혈전", "주교재 6쪽"),
    ("2025-08-12", "Diazepam, Lorazepam", "자근, 작약, 시호", "체액 불균형", "주교재 7쪽"),
    ("2025-08-13", "Alprazolam Amitriptyline", "숙지황,토근,하수오", "산염기장애", "주교재 8쪽"),
    ("2025-08-14", "Doxepin, Fluoxetine", "황금,황기,대산", "약물유발신질환", "주교재 9쪽"),
    ("2025-08-15", "Sertraline Methylphenidate", "대황,맥문동,반하", "신장병", "주교재 10쪽"),
    ("2025-08-16", "Modafinil Naloxone", None, "급성신손상", None),
    ("2025-08-18", "Pentazocine, Fentanyl", "방기,백출,창출", "만성신장병", "주교재 11쪽"),
    ("2025-08-19", "Methadone L-Dopa, Carbidopa,", "산약,스코폴,여로", "전립선비대", "주교재 12쪽"),
    ("2025-08-20", "Biperiden, Ropinirole", "절패모,지모,천궁", "COPD", "주교재 13쪽"),
    ("2025-08-21", "Selegiline Carisoprodol", "천마,현호색, 황련", "알레르기비염", "주교재 14쪽"),
    ("2025-08-22", "Atropine, Ipratropium Bromide", "히드라,개자,견우자", "소화성궤양", "주교재 15쪽"),
    ("2025-08-23", "Oxybutynin Propiverine,", None, "위식도역류", None),
    ("2025-08-25", "Donepezil HCl Epinephrine", "빈랑자,육두구,차전자", "변비,설사,과민성", "주교재 16쪽"),
    ("2025-08-26", "Salbutamol, Phenylephrine", "카라발두, 커피두 콜히쿰자", "염증성장질환", "주교재 17쪽"),
    ("2025-08-27", "Propranolol, Carvedilol", "파두, 행인, 도인 호미카", "간경변", "주교재 18쪽"),
    ("2025-08-28", "Terazosin, Alfuzosin", "고추, 대추, 마리아엉겅퀴", "바이러스간염", "주교재 19쪽"),
    ("2025-08-29", "Quetiapine, Aripiprazol", "산사, 산수유 산초", "당뇨", "주교재 20쪽"),
    ("2025-08-30", "Sumatriptan, Ondansetron, Paroxetine", None, "당뇨,갑상선", None),
    ("2025-09-01", "Dibucaine, Lidocaine", "연교, 오미자, 지실", "상기도감염", "주교재 21쪽"),
    ("2025-09-02", "Sildenafil, Vardenafil", "진피, 치자,홉", "폐렴", "주교재 22쪽"),
    ("2025-09-03", "Fenoterol, Formoterol, Ambroxo", "회향,팔각향,후추", "요로감염", "주교재 23쪽"),
    ("2025-09-04", "Montelukast, Tiotropium", "광곽향,당약,대마", "결핵", "주교재 24쪽"),
    ("2025-09-05", "Doxapram, Milrinone, Nifedipine,", "로베리,마황,박하", "뇌수막염", "주교재 25쪽"),
    ("2025-09-06", "Amiodarone, Diltiazem, Verapamil", None, "위장관감염", None),
    ("2025-09-08", "Enalapril, Ramipril", "빈카,음양곽,인진호", "진균감염", "주교재 26쪽"),
    ("2025-09-09", "Hydrochlorothiazide, Furosemide", "애엽,청호,현초", "두통", "주교재 27쪽"),
    ("2025-09-10", "Simvastatin, Atorvastatin, Rosuvastatin", "히페리,다엽,디기탈", "뇌전증", "주교재 28쪽"),
    ("2025-09-11", "Clopidogrel, Cilostazol, Ezetimibe", "해총,스트로,센나", "알츠하이머,파킨슨", "주교재 29쪽"),
    ("2025-09-12", "Rosiglitazone, Repaglinide", "야보란,우바우르,은행엽", "우울장애", "주교재 30쪽"),
    ("2025-09-13", "Glimepiride Acetaminophen", None, "조현,양극성", None),
    ("2025-09-15", "Indomethacin, Sulindac, Diclofenac", "코카,계피,두충", "수면장애", "주교재 31쪽"),
    ("2025-09-16", "Ibuprofen, Piroxicam,", "상백피,오가피,요함", "범불안 장애", "주교재 32쪽"),
    ("2025-09-17", "Celecoxib, Bendazac", "주목,키나,황백", "소아기정신장애", "주교재 33쪽"),
    ("2025-09-18", "Isoniazid, Pyrazinamide", "후박,괴하,샤프란", "골관절염,", "주교재 34쪽"),
    ("2025-09-19", "Cetirizine, Fexofenadine, Loratadine,", "시나,신이,정향", "류마티스", "주교재 35쪽"),
    ("2025-09-20", "Pranlukast, Azelastine, Cimetidine,", None, "골다공증", None),
    ("2025-09-22", "Ranitidine, Omeprazole", "제충국,카모밀,하고", "통풍", "주교재 36쪽"),
    ("2025-09-23", "Metronidazole, Hydroxychloroquine Mefloquine", "홍화,고목,목통", "폐경", "주교재 37쪽"),
    ("2025-09-24", "Ketoconazole, Fluconazole", "알로에,오배자,큐라", "피임", "주교재 38쪽"),
    ("2025-09-25", "Cefaclor, Ofloxacin", "아편,동충,맥각", "요실금", "주교재 39쪽"),
    ("2025-09-26", "Ciprofloxacin, Chloramphenicol", "복령,영지,간유", "폐암", "주교재 40쪽"),
    ("2025-09-27", "Acyclovir, Adefovir", None, "대장암", None),
    ("2025-09-29", "Lamivudine, Oseltamivir", "녹용,사향,섬수", "유방암", "부교재 1,2"),
    ("2025-09-30", "Cyclophosphamide, Gemcitabine, 5-Fluorouracil,", "우황,용담,면실유", "백혈병", "부교재 3,4"),
    ("2025-10-01", "Methotrexate, Anastrozole", "참기름,본초 1,2장", "보조적치료", "부교재 5,6"),
    ("2025-10-02", "Imatinib, Tamoxifen", "본초 3,4장", "아토피피부염", "부교재 7,8"),
    ("2025-10-03", "Amlodipine, Benazepril, Valsartan", "본초 5,6장", "건선", "부교재 9,10"),
    ("2025-10-04", "Eperisone, Tolterodine,", None, "여드름", None),
    ("2025-10-06", "Loperamide, Domperidone", "총론 1~4", "녹내장", "부교재 11~13"),
    ("2025-10-07", "Levothyroxine, Orlistat", "총론 5~9", "임상영양", "부교재 14~16"),
]


def init_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS study_plan (
          id INTEGER PRIMARY KEY,
          plan_date TEXT NOT NULL,
          weekday TEXT,
          category TEXT NOT NULL,
          content TEXT,
          chapter_id INTEGER REFERENCES chapters(id),
          notes TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_plan_date ON study_plan(plan_date)")


def chapter_id_for(conn, drug_name: str | None) -> int | None:
    if not drug_name:
        return None
    canon = DRUG_THERAPY_MAP.get(drug_name)
    if not canon:
        return None
    row = conn.execute("SELECT id FROM chapters WHERE name=?", (canon,)).fetchone()
    return row[0] if row else None


def import_plan() -> dict:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    init_table(conn)
    conn.execute("DELETE FROM study_plan")
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    cnt = 0
    matched_drug = 0
    for d_str, hapsung, saengyak, drug, beob in PLAN_DATA:
        d = date.fromisoformat(d_str)
        wd = weekdays[d.weekday()]
        chap = chapter_id_for(conn, drug)
        if chap:
            matched_drug += 1
        # 4과목 각각 row 추가
        for category, content in [("합성", hapsung), ("생약", saengyak), ("약치", drug), ("약법", beob)]:
            if not content:
                continue
            conn.execute(
                "INSERT INTO study_plan (plan_date, weekday, category, content, chapter_id) VALUES (?,?,?,?,?)",
                (d_str, wd, category, content, chap if category == "약치" else None),
            )
            cnt += 1
    conn.commit()
    conn.close()
    return {"days": len(PLAN_DATA), "rows": cnt, "drug_matched": matched_drug}


if __name__ == "__main__":
    r = import_plan()
    print(f"일정 {r['days']}일 / 행 {r['rows']}개 / 약치 단원 매핑 {r['drug_matched']}개")
