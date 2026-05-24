"""Streamlit UI — 2026 약사국시 학습지 프로그램.

탭:
  1. 📝 학습지 출력  (5년 통합, 단원별)
  2. 📅 오늘의 학습지 (얼리버드 계획표 + D-day)
  3. 📊 학습 이력    (worksheets + attempts)
  4. 🛠 데이터 현황  (분류 수정 + 정답 보완)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import datetime as dt
import sqlite3
import streamlit as st

from db import get_conn  # noqa: E402
from config import DB_PATH, OUTPUT_DIR  # noqa: E402
from generator.make_pdf import make_worksheet, fetch_questions  # noqa: E402
from quiz import render_quiz_tab  # noqa: E402

# ────────────────── 상수 ──────────────────
EXAM_DATE = dt.date(2027, 1, 15)  # 77회 시험일 (얼리버드 계획표 기준)
YEAR_OPTIONS = [2021, 2022, 2023, 2024, 2025]

st.set_page_config(page_title="2026 약사국시 학습지", layout="wide", initial_sidebar_state="expanded")


# ────────────────── 비밀번호 게이트 ──────────────────
def _check_password() -> bool:
    """Streamlit Secrets에 'app_password'가 있을 때만 비번 요구.
    로컬에서 secrets 없으면 무시 (자유 사용).
    """
    try:
        required = st.secrets.get("app_password", None)
    except Exception:
        required = None
    if not required:
        return True  # 비번 미설정 → 자유 접근

    if st.session_state.get("auth_ok"):
        return True

    st.markdown("### 🔒 비밀번호")
    pw = st.text_input("접속 비밀번호를 입력하세요", type="password", key="pw_input")
    if pw:
        if pw == required:
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()


_check_password()

# ────────────────── DB helpers ──────────────────
@st.cache_data(ttl=10)
def get_subjects():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, name, session FROM subjects ORDER BY session, id"
        ).fetchall()]

@st.cache_data(ttl=10)
def get_chapters(subject_id: int = None):
    sql = """
      SELECT c.id, c.name, c.chapter_no,
             p.name AS parent,
             s.name AS subject, s.id AS subject_id
      FROM chapters c
      LEFT JOIN chapters p ON c.parent_id = p.id
      LEFT JOIN subjects s ON c.subject_id = s.id
    """
    params = []
    if subject_id:
        sql += " WHERE c.subject_id = ?"
        params.append(subject_id)
    sql += " ORDER BY s.session, p.name NULLS LAST, c.chapter_no NULLS LAST, c.id"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

@st.cache_data(ttl=10)
def get_yc_tree():
    """약물치료학(가상) 트리: {카테고리명: [(leaf_id, leaf_name, qcount), ...]}"""
    YC_SUBJECT_ID = 6
    with get_conn() as conn:
        rows = conn.execute("""
          SELECT c.id, c.name, p.name AS parent,
                 (SELECT COUNT(*) FROM questions WHERE chapter_id=c.id) AS qcnt
          FROM chapters c LEFT JOIN chapters p ON c.parent_id=p.id
          WHERE c.subject_id=? AND c.parent_id IS NOT NULL
          ORDER BY c.id
        """, (YC_SUBJECT_ID,)).fetchall()
    from collections import defaultdict
    tree = defaultdict(list)
    for r in rows:
        if r['parent'] and r['parent'] != '약물치료학':
            tree[r['parent']].append({'id': r['id'], 'name': r['name'], 'qcnt': r['qcnt']})
    return dict(tree)


@st.cache_data(ttl=10)
def get_yj_tree():
    """약제학 sub-chapter 트리: [{id, name, qcnt}, ...] (chapter_no 순)"""
    YJ_PARENT_ID = 6  # 약제학 parent
    with get_conn() as conn:
        rows = conn.execute("""
          SELECT c.id, c.name, c.chapter_no,
                 (SELECT COUNT(*) FROM questions WHERE chapter_id=c.id) AS qcnt
          FROM chapters c
          WHERE c.parent_id=?
          ORDER BY c.chapter_no
        """, (YJ_PARENT_ID,)).fetchall()
    return [{'id': r['id'], 'name': r['name'], 'qcnt': r['qcnt']} for r in rows]


@st.cache_data(ttl=10)
def get_law_tree():
    """약사법규 sub-chapter 트리: [{id, name, qcnt}, ...] (chapter_no 순)"""
    LAW_PARENT_ID = 15  # 약사법규 parent
    with get_conn() as conn:
        rows = conn.execute("""
          SELECT c.id, c.name, c.chapter_no,
                 (SELECT COUNT(*) FROM questions WHERE chapter_id=c.id) AS qcnt
          FROM chapters c
          WHERE c.parent_id=?
          ORDER BY c.chapter_no
        """, (LAW_PARENT_ID,)).fetchall()
    return [{'id': r['id'], 'name': r['name'], 'qcnt': r['qcnt']} for r in rows]


@st.cache_data(ttl=10)
def get_subtree(parent_id: int):
    """주어진 parent chapter의 sub-chapter 트리: [{id, name, qcnt}, ...] (chapter_no 순)"""
    with get_conn() as conn:
        rows = conn.execute("""
          SELECT c.id, c.name, c.chapter_no,
                 (SELECT COUNT(*) FROM questions WHERE chapter_id=c.id) AS qcnt
          FROM chapters c
          WHERE c.parent_id=?
          ORDER BY c.chapter_no
        """, (parent_id,)).fetchall()
    return [{'id': r['id'], 'name': r['name'], 'qcnt': r['qcnt']} for r in rows]


@st.cache_data(ttl=10)
def chapter_qcnt(chapter_ids):
    """주어진 chapter_id들의 문제 수 합."""
    if not chapter_ids:
        return 0
    with get_conn() as conn:
        placeholders = ','.join('?' * len(chapter_ids))
        return conn.execute(
            f"SELECT COUNT(*) FROM questions WHERE chapter_id IN ({placeholders})",
            chapter_ids
        ).fetchone()[0]


# ────────────────── 문제_분류_요청.md 기반 새 분류 (2026-05-02) ──────────────────
# 대분류 → [(소분류 라벨, [chapter_id, ...]), ...]
NEW_TAXONOMY = {
    "🧬 1. 생명약학 (100)": [
        # 생화학 100문제 6단원 분류 (chapter_id 107-112)
        ("생화학·분자생물학", [107, 108, 109, 110, 111, 112]),
        # 미생물학·면역학 98문제 6단원 분류 (chapter_id 119-124)
        ("미생물학·면역학", [119, 120, 121, 122, 123, 124]),
        # 약리학 99문제 6단원 분류 (chapter_id 131-136)
        ("약리학", [131, 132, 133, 134, 135, 136]),
        # 예방약학 96문제 5단원 분류 (chapter_id 102-106)
        ("예방약학", [102, 103, 104, 105, 106]),
        # 병태생리학 107문제 6단원 분류 (chapter_id 125-130)
        ("병태생리학", [125, 126, 127, 128, 129, 130]),
    ],
    "🏭 2. 산업약학 (90)": [
        # 물리약학 93문제 6단원 분류 (chapter_id 137-142)
        ("물리약학", [137, 138, 139, 140, 141, 142]),
        # 의약화학(약물합성학) 91문제 6단원 분류 (chapter_id 113-118)
        ("의약품 합성학·의약화학", [113, 114, 115, 116, 117, 118]),
        # 약품분석학 91문제 6단원 분류 (chapter_id 143-148)
        ("약품분석학", [143, 144, 145, 146, 147, 148]),
        # 약제학은 별도 sub-tree expander로 처리 (1-7장 + PK 개별 선택). 산업약학 한 번에 = 약제학 전체 포함하기 위해 leaf 8개를 같이 묶어둠.
        ("약제학", [88, 89, 90, 91, 92, 93, 94, 95]),
        # 생약학 70문제(149-153) + 한약제제학 22문제(154-157)
        ("생약학·한약제제학", [149, 150, 151, 152, 153, 154, 155, 156, 157]),
    ],
    # 3. 임상·실무약학 — 약물치료학은 별도 트리, 나머지는 단순
    "🏥 3. 임상·실무약학 (140)": [
        # 약국실무 170문제 6단원 분류 (chapter_id 158-163)
        ("처방검토·조제 (약국실무)", [158, 159, 160, 161, 162, 163]),
        # 의약품제조관리학 90문제 5단원 분류 (chapter_id 169-173)
        ("의약품제조관리학", [169, 170, 171, 172, 173]),
        # 사회약학 95문제 5단원 분류 (chapter_id 164-168)
        ("사회약학", [164, 165, 166, 167, 168]),
    ],
    "📖 4. 보건의약관계법규 (20)": [
        # 약사법규 100문제 6단원 분류 (chapter_id 96-101)
        ("약사법규", [96, 97, 98, 99, 100, 101]),
    ],
}


@st.cache_data(ttl=10)
def db_stats():
    with get_conn() as conn:
        r = conn.execute("""
          SELECT COUNT(*) AS t,
                 SUM(CASE WHEN explanation IS NOT NULL AND explanation!='' THEN 1 ELSE 0 END) AS expl,
                 SUM(CASE WHEN chapter_id IS NULL THEN 1 ELSE 0 END) AS unc,
                 SUM(CASE WHEN answer IS NULL OR answer<1 OR answer>5 THEN 1 ELSE 0 END) AS nan
          FROM questions
        """).fetchone()
        return dict(r)

# ────────────────── 사이드바 (탭1·2 공통 사용) ──────────────────
def sidebar_filter():
    """학습지 출력 사이드바 — 문제_분류_요청.md 4대분류 트리.
    반환: filters dict."""
    st.sidebar.title("📝 학습지 필터")
    years = st.sidebar.multiselect("연도 (비우면 5년 통합)", YEAR_OPTIONS, default=[])
    st.sidebar.divider()

    chap_ids: list[int] = []

    # ───── 새 4대분류 트리 ─────
    for big_label, items in NEW_TAXONOMY.items():
        with st.sidebar.expander(big_label, expanded=False):
            # 대분류 전체 선택
            big_all_cids = [cid for _, cids in items for cid in cids]
            big_total = chapter_qcnt(big_all_cids)
            if st.checkbox(f"📦 대분류 전체 ({big_total}문제)", key=f"big_{big_label}"):
                chap_ids.extend(big_all_cids)
            else:
                for sub_label, cids in items:
                    qcnt = chapter_qcnt(cids)
                    if st.checkbox(f"{sub_label} ({qcnt})", key=f"sub_{big_label}_{sub_label}"):
                        chap_ids.extend(cids)

    # ───── 🧬 생화학 (6단원 트리, 생명약학 안의 핵심) ─────
    bio_tree = get_subtree(1)
    if bio_tree:
        bio_total = sum(s['qcnt'] for s in bio_tree)
        with st.sidebar.expander(f"🧬 1-1. 생화학 ({bio_total}문제)", expanded=False):
            all_bio_cids = [s['id'] for s in bio_tree]
            if st.checkbox(f"📦 생화학 전체 ({bio_total})", key="bio_all_root"):
                chap_ids.extend(all_bio_cids)
            else:
                for s in bio_tree:
                    if st.checkbox(f"  └ {s['name']} ({s['qcnt']})",
                                   key=f"bio_leaf_{s['id']}"):
                        chap_ids.append(s['id'])

    # ───── 🛡 예방약학 (5단원 트리, 생명약학 안의 핵심) ─────
    prev_tree = get_subtree(4)
    if prev_tree:
        prev_total = sum(s['qcnt'] for s in prev_tree)
        with st.sidebar.expander(f"🛡 1-2. 예방약학 ({prev_total}문제)", expanded=False):
            all_prev_cids = [s['id'] for s in prev_tree]
            if st.checkbox(f"📦 예방약학 전체 ({prev_total})", key="prev_all_root"):
                chap_ids.extend(all_prev_cids)
            else:
                for s in prev_tree:
                    if st.checkbox(f"  └ {s['name']} ({s['qcnt']})",
                                   key=f"prev_leaf_{s['id']}"):
                        chap_ids.append(s['id'])

    # ───── 🦠 미생물학·면역학 (6단원 트리, 생명약학 안의 핵심) ─────
    mic_tree = get_subtree(2)
    if mic_tree:
        mic_total = sum(s['qcnt'] for s in mic_tree)
        with st.sidebar.expander(f"🦠 1-3. 미생물학·면역학 ({mic_total}문제)", expanded=False):
            all_mic_cids = [s['id'] for s in mic_tree]
            if st.checkbox(f"📦 미생물학 전체 ({mic_total})", key="mic_all_root"):
                chap_ids.extend(all_mic_cids)
            else:
                for s in mic_tree:
                    if st.checkbox(f"  └ {s['name']} ({s['qcnt']})",
                                   key=f"mic_leaf_{s['id']}"):
                        chap_ids.append(s['id'])

    # ───── 🩺 병태생리학 (6단원 트리, 생명약학 안의 핵심) ─────
    pat_tree = get_subtree(3)
    if pat_tree:
        pat_total = sum(s['qcnt'] for s in pat_tree)
        with st.sidebar.expander(f"🩺 1-4. 병태생리학 ({pat_total}문제)", expanded=False):
            all_pat_cids = [s['id'] for s in pat_tree]
            if st.checkbox(f"📦 병태생리학 전체 ({pat_total})", key="pat_all_root"):
                chap_ids.extend(all_pat_cids)
            else:
                for s in pat_tree:
                    if st.checkbox(f"  └ {s['name']} ({s['qcnt']})",
                                   key=f"pat_leaf_{s['id']}"):
                        chap_ids.append(s['id'])

    # ───── 💉 약리학 (6단원 트리, 생명약학 안의 핵심) ─────
    pharm_tree = get_subtree(5)
    if pharm_tree:
        pharm_total = sum(s['qcnt'] for s in pharm_tree)
        with st.sidebar.expander(f"💉 1-5. 약리학 ({pharm_total}문제)", expanded=False):
            all_pharm_cids = [s['id'] for s in pharm_tree]
            if st.checkbox(f"📦 약리학 전체 ({pharm_total})", key="pharm_all_root"):
                chap_ids.extend(all_pharm_cids)
            else:
                for s in pharm_tree:
                    if st.checkbox(f"  └ {s['name']} ({s['qcnt']})",
                                   key=f"pharm_leaf_{s['id']}"):
                        chap_ids.append(s['id'])

    # ───── 🧪 약제학 sub-tree (1-7장 + 약동학 8단원 개별 선택) ─────
    yj_tree = get_yj_tree()
    if yj_tree:
        yj_total = sum(s['qcnt'] for s in yj_tree)
        with st.sidebar.expander(f"🧪 2-1. 약제학 ({yj_total}문제)", expanded=False):
            all_yj_cids = [s['id'] for s in yj_tree]
            if st.checkbox(f"📦 약제학 전체 ({yj_total})", key="yj_all_root"):
                chap_ids.extend(all_yj_cids)
            else:
                for s in yj_tree:
                    if st.checkbox(f"  └ {s['name']} ({s['qcnt']})",
                                   key=f"yj_leaf_{s['id']}"):
                        chap_ids.append(s['id'])

    # ───── ⚗️ 의약화학 (6단원 트리, 산업약학 안의 핵심) ─────
    mc_tree = get_subtree(9)
    if mc_tree:
        mc_total = sum(s['qcnt'] for s in mc_tree)
        with st.sidebar.expander(f"⚗️ 2-2. 의약화학 ({mc_total}문제)", expanded=False):
            all_mc_cids = [s['id'] for s in mc_tree]
            if st.checkbox(f"📦 의약화학 전체 ({mc_total})", key="mc_all_root"):
                chap_ids.extend(all_mc_cids)
            else:
                for s in mc_tree:
                    if st.checkbox(f"  └ {s['name']} ({s['qcnt']})",
                                   key=f"mc_leaf_{s['id']}"):
                        chap_ids.append(s['id'])

    # ───── 🌡 물리약학 (6단원 트리, 산업약학 안의 핵심) ─────
    pp_tree = get_subtree(7)
    if pp_tree:
        pp_total = sum(s['qcnt'] for s in pp_tree)
        with st.sidebar.expander(f"🌡 2-3. 물리약학 ({pp_total}문제)", expanded=False):
            all_pp_cids = [s['id'] for s in pp_tree]
            if st.checkbox(f"📦 물리약학 전체 ({pp_total})", key="pp_all_root"):
                chap_ids.extend(all_pp_cids)
            else:
                for s in pp_tree:
                    if st.checkbox(f"  └ {s['name']} ({s['qcnt']})",
                                   key=f"pp_leaf_{s['id']}"):
                        chap_ids.append(s['id'])

    # ───── 🔬 약품분석학 (6단원 트리, 산업약학 안의 핵심) ─────
    pa_tree = get_subtree(8)
    if pa_tree:
        pa_total = sum(s['qcnt'] for s in pa_tree)
        with st.sidebar.expander(f"🔬 2-4. 약품분석학 ({pa_total}문제)", expanded=False):
            all_pa_cids = [s['id'] for s in pa_tree]
            if st.checkbox(f"📦 약품분석학 전체 ({pa_total})", key="pa_all_root"):
                chap_ids.extend(all_pa_cids)
            else:
                for s in pa_tree:
                    if st.checkbox(f"  └ {s['name']} ({s['qcnt']})",
                                   key=f"pa_leaf_{s['id']}"):
                        chap_ids.append(s['id'])

    # ───── 🌿 생약학 (5단원 트리, 산업약학 안의 핵심) ─────
    ph_tree = get_subtree(10)
    if ph_tree:
        ph_total = sum(s['qcnt'] for s in ph_tree)
        with st.sidebar.expander(f"🌿 2-5. 생약학 ({ph_total}문제)", expanded=False):
            all_ph_cids = [s['id'] for s in ph_tree]
            if st.checkbox(f"📦 생약학 전체 ({ph_total})", key="ph_all_root"):
                chap_ids.extend(all_ph_cids)
            else:
                for s in ph_tree:
                    if st.checkbox(f"  └ {s['name']} ({s['qcnt']})",
                                   key=f"ph_leaf_{s['id']}"):
                        chap_ids.append(s['id'])

    # ───── 🍵 한약제제학 (4단원 트리, 산업약학 안의 핵심) ─────
    hm_tree = get_subtree(11)
    if hm_tree:
        hm_total = sum(s['qcnt'] for s in hm_tree)
        with st.sidebar.expander(f"🍵 2-6. 한약제제학 ({hm_total}문제)", expanded=False):
            all_hm_cids = [s['id'] for s in hm_tree]
            if st.checkbox(f"📦 한약제제학 전체 ({hm_total})", key="hm_all_root"):
                chap_ids.extend(all_hm_cids)
            else:
                for s in hm_tree:
                    if st.checkbox(f"  └ {s['name']} ({s['qcnt']})",
                                   key=f"hm_leaf_{s['id']}"):
                        chap_ids.append(s['id'])

    # ───── 💊 약물치료학 (16 카테고리 트리, 임상·실무약학 안의 핵심) ─────
    yc_tree = get_yc_tree()
    if yc_tree:
        yc_total = sum(l['qcnt'] for cat, leaves in yc_tree.items() for l in leaves)
        with st.sidebar.expander(f"💊 3-1. 약물치료학 ({yc_total}문제)", expanded=False):
            # 약치 전체
            all_yc_cids = [l['id'] for cat, leaves in yc_tree.items() for l in leaves]
            if st.checkbox(f"📦 약치 전체 ({yc_total})", key="yc_all_root"):
                chap_ids.extend(all_yc_cids)
            else:
                # 카테고리 정렬: 문제 많은 순
                sorted_cats = sorted(yc_tree.items(),
                                     key=lambda x: -sum(l['qcnt'] for l in x[1]))
                for cat, leaves in sorted_cats:
                    cat_total = sum(l['qcnt'] for l in leaves)
                    cat_select = st.checkbox(f"  📁 {cat} ({cat_total})", key=f"yc_cat_{cat}")
                    if cat_select:
                        chap_ids.extend([l['id'] for l in leaves])
                    else:
                        # leaf 개별 선택
                        for leaf in leaves:
                            if st.checkbox(f"      └ {leaf['name']} ({leaf['qcnt']})",
                                           key=f"yc_leaf_{leaf['id']}"):
                                chap_ids.append(leaf['id'])

    # ───── 🏥 약국실무 (6단원 트리, 임상·실무약학 안의 핵심) ─────
    pr_tree = get_subtree(13)
    if pr_tree:
        pr_total = sum(s['qcnt'] for s in pr_tree)
        with st.sidebar.expander(f"🏥 3-2. 약국실무 ({pr_total}문제)", expanded=False):
            all_pr_cids = [s['id'] for s in pr_tree]
            if st.checkbox(f"📦 약국실무 전체 ({pr_total})", key="pr_all_root"):
                chap_ids.extend(all_pr_cids)
            else:
                for s in pr_tree:
                    if st.checkbox(f"  └ {s['name']} ({s['qcnt']})",
                                   key=f"pr_leaf_{s['id']}"):
                        chap_ids.append(s['id'])

    # ───── 📋 사회약학 (5단원 트리, 임상·실무약학 안의 핵심) ─────
    sp_tree = get_subtree(14)
    if sp_tree:
        sp_total = sum(s['qcnt'] for s in sp_tree)
        with st.sidebar.expander(f"📋 3-3. 사회약학 ({sp_total}문제)", expanded=False):
            all_sp_cids = [s['id'] for s in sp_tree]
            if st.checkbox(f"📦 사회약학 전체 ({sp_total})", key="sp_all_root"):
                chap_ids.extend(all_sp_cids)
            else:
                for s in sp_tree:
                    if st.checkbox(f"  └ {s['name']} ({s['qcnt']})",
                                   key=f"sp_leaf_{s['id']}"):
                        chap_ids.append(s['id'])

    # ───── 🏭 의약품제조관리학 (5단원 트리, 임상·실무약학 안의 핵심) ─────
    gm_tree = get_subtree(87)
    if gm_tree:
        gm_total = sum(s['qcnt'] for s in gm_tree)
        with st.sidebar.expander(f"🏭 3-4. 의약품제조관리학 ({gm_total}문제)", expanded=False):
            all_gm_cids = [s['id'] for s in gm_tree]
            if st.checkbox(f"📦 의약품제조관리학 전체 ({gm_total})", key="gm_all_root"):
                chap_ids.extend(all_gm_cids)
            else:
                for s in gm_tree:
                    if st.checkbox(f"  └ {s['name']} ({s['qcnt']})",
                                   key=f"gm_leaf_{s['id']}"):
                        chap_ids.append(s['id'])

    # ───── 📖 약사법규 (6단원 트리, 보건의약관계법규 안의 핵심) ─────
    law_tree = get_law_tree()
    if law_tree:
        law_total = sum(s['qcnt'] for s in law_tree)
        with st.sidebar.expander(f"📖 4-1. 약사법규 ({law_total}문제)", expanded=False):
            all_law_cids = [s['id'] for s in law_tree]
            if st.checkbox(f"📦 약사법규 전체 ({law_total})", key="law_all_root"):
                chap_ids.extend(all_law_cids)
            else:
                for s in law_tree:
                    if st.checkbox(f"  └ {s['name']} ({s['qcnt']})",
                                   key=f"law_leaf_{s['id']}"):
                        chap_ids.append(s['id'])

    st.sidebar.divider()
    include_hidden = st.sidebar.checkbox("비공개 자료 포함", value=True)
    unc_only = st.sidebar.checkbox("미분류 문제만", value=False)

    # 중복 제거
    chap_ids = list(dict.fromkeys(chap_ids))

    return {
        'year': years if years else None,
        'subject_ids': [],   # 새 분류는 chapter_id로만 필터
        'chapter_ids': chap_ids,
        'qnum_ranges': {},
        'include_hidden': include_hidden,
        'unclassified_only': unc_only,
    }

# ────────────────── 메인 UI ──────────────────
st.title("📚 2026 약사국시 학습지 프로그램")

stats = db_stats()
c1, c2, c3, c4 = st.columns(4)
c1.metric("전체 문제", f"{stats['t']:,}")
c2.metric("해설", f"{stats['expl']} ({stats['expl']*100/stats['t']:.1f}%)")
c3.metric("분류율", f"{(stats['t']-stats['unc'])*100/stats['t']:.1f}%")
c4.metric("정답 있음", f"{(stats['t']-stats['nan'])*100/stats['t']:.1f}%")

st.divider()

tab1, tabq, tab2, tab3, tab4 = st.tabs(
    ["📝 학습지 출력", "✏️ 문제 풀이", "📅 오늘의 학습지", "📊 학습 이력", "🛠 데이터 현황"]
)

# ───────────── Tab Q: 문제 풀이 (퀴즈) ─────────────
with tabq:
    render_quiz_tab()

# ───────────── Tab 1: 학습지 출력 ─────────────
with tab1:
    filters = sidebar_filter()
    try:
        qs_preview = fetch_questions(filters)
    except Exception as e:
        st.error(f"미리보기 오류: {e}")
        qs_preview = []

    cl, cr = st.columns([2, 1])
    with cl:
        n_q = len(qs_preview)
        st.subheader(f"선택 결과: {n_q}문제")
        if qs_preview:
            from collections import Counter
            sub_dist = Counter(q['subject'] for q in qs_preview)
            for s, n in sub_dist.most_common():
                st.write(f"- {s}: {n}문제")

            # 예상 소요 시간 (경험치: 클라우드 ~10문제/초)
            est_sec = max(3, int(n_q / 8))
            if n_q > 200:
                st.warning(f"⏱ 예상 소요: 약 **{est_sec}초** (문제가 많아 1-2분까지 걸릴 수 있음). "
                           f"메모리 한계로 실패할 가능성도 있어 **단원 1-2개씩 나눠 출력**을 권장합니다.")
            elif n_q > 50:
                st.info(f"⏱ 예상 소요: 약 **{est_sec}초**")
            else:
                st.caption(f"⏱ 예상 소요: 약 **{est_sec}초**")
        else:
            st.info("사이드바에서 과목·단원을 선택하세요.")

    with cr:
        if st.button("📄 학습지 PDF 출력", type="primary", disabled=not qs_preview):
            import time
            t_start = time.time()
            progress = st.progress(0, text="📚 데이터 조회 중...")
            try:
                # 1. fetch (이미 qs_preview에 있으나 fresh 보장 위해 재조회)
                progress.progress(15, text="📚 문제·해설 조회 중...")
                # 2. 렌더링·PDF 생성
                progress.progress(35, text=f"🎨 PDF 렌더링 중 ({n_q}문제)... 30초~2분 소요")
                out_path = make_worksheet(filters)
                progress.progress(95, text="💾 다운로드 준비 중...")
                with open(out_path, 'rb') as f:
                    pdf_bytes = f.read()
                progress.progress(100, text="✅ 완료!")
                elapsed = time.time() - t_start
                st.success(f"생성 완료 ({elapsed:.1f}초): {out_path.name} · {len(pdf_bytes)//1024} KB")
                st.download_button(
                    "⬇ PDF 다운로드", pdf_bytes,
                    file_name=out_path.name, mime='application/pdf',
                    type="primary",
                )
            except Exception as e:
                progress.empty()
                st.error(f"❌ 오류: {type(e).__name__}: {e}")
                st.caption("문제가 너무 많거나 메모리 한계일 수 있어요. 단원 수를 줄여 다시 시도하세요.")

# ───────────── Tab 2: 오늘의 학습지 (얼리버드) ─────────────
with tab2:
    today = dt.date.today()
    dday = (EXAM_DATE - today).days
    cd1, cd2 = st.columns(2)
    cd1.metric("D-day", f"{dday}일", help=f"77회 시험일: {EXAM_DATE}")

    # study_plan 테이블 사용
    try:
        with get_conn() as conn:
            plan_dates = [r[0] for r in conn.execute("SELECT DISTINCT plan_date FROM study_plan ORDER BY plan_date").fetchall()]
    except sqlite3.OperationalError:
        plan_dates = []

    if not plan_dates:
        st.info("얼리버드 계획표가 아직 로드되지 않았습니다. (`parsers/parse_plan.py` 실행 필요)")
    else:
        # 가장 가까운 미래 날짜 default
        future = [d for d in plan_dates if d >= today.isoformat()]
        default_idx = plan_dates.index(future[0]) if future else len(plan_dates) - 1
        selected = st.selectbox("학습 날짜", plan_dates, index=default_idx)

        with get_conn() as conn:
            day_plan = conn.execute(
                "SELECT category, content, chapter_id FROM study_plan WHERE plan_date=? ORDER BY id",
                (selected,)
            ).fetchall()

        if day_plan:
            st.subheader(f"{selected} 학습 분량")
            chap_items = []
            for r in day_plan:
                cat, content, cid = r['category'], r['content'], r['chapter_id']
                emoji = {"합성": "⚗️", "생약": "🌿", "약치": "💊", "약법": "📖"}.get(cat, "·")
                if cid:
                    st.write(f"{emoji} **[{cat}]** {content}  `chapter_id={cid}`")
                    chap_items.append(cid)
                else:
                    st.write(f"{emoji} **[{cat}]** {content}")

            if chap_items:
                st.divider()
                if st.button(f"💊 약치 단원 5년치 통합 출력 ({len(chap_items)}단원)", type="primary"):
                    try:
                        with st.spinner("생성 중..."):
                            f2 = {'year': None, 'subject_ids': [], 'chapter_ids': chap_items, 'include_hidden': True}
                            out2 = make_worksheet(f2)
                        with open(out2, 'rb') as f:
                            st.download_button("⬇ PDF 다운로드", f, file_name=out2.name, mime='application/pdf')
                    except Exception as e:
                        st.error(f"오류: {e}")

# ───────────── Tab 3: 학습 이력 ─────────────
with tab3:
    # ── 학습 통계 대시보드 (#1) ──
    st.subheader("📊 학습 통계 대시보드")
    today = dt.date.today()
    dday = (EXAM_DATE - today).days

    with get_conn() as conn:
        att_stats = conn.execute(
            "SELECT COUNT(*) AS total, SUM(is_correct) AS correct, "
            "COUNT(DISTINCT DATE(attempted_at)) AS days FROM attempts"
        ).fetchone()
    total_att = att_stats["total"] or 0
    correct = att_stats["correct"] or 0
    days = att_stats["days"] or 0

    # 복습 도래 카운트
    with get_conn() as conn:
        due = conn.execute(
            "SELECT COUNT(*) FROM review_schedule WHERE DATE(next_review_at) <= DATE('now')"
        ).fetchone()[0]

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("📅 D-day", f"{dday}일", help=f"시험일: {EXAM_DATE.isoformat()}")
    m2.metric("📝 누적 풀이", f"{total_att}회")
    m3.metric("✅ 정답률", f"{correct*100/total_att:.0f}%" if total_att else "—")
    m4.metric("📚 학습일", f"{days}일")
    m5.metric("🔁 복습 도래", f"{due}개", help="간격 반복(SM-2)으로 오늘 복습 시간이 된 문제")

    if total_att == 0:
        st.info("아직 풀이 데이터가 없습니다. ✏️ 문제 풀이 탭에서 시작하세요.")
    else:
        # 일별 학습량 (최근 30일)
        with get_conn() as conn:
            daily = conn.execute(
                "SELECT DATE(attempted_at) AS d, COUNT(*) AS n, "
                "SUM(is_correct) AS c FROM attempts "
                "WHERE DATE(attempted_at) >= DATE('now', '-30 days') "
                "GROUP BY DATE(attempted_at) ORDER BY d"
            ).fetchall()
        if daily:
            try:
                import pandas as pd
                df = pd.DataFrame(
                    [{"날짜": d["d"], "풀이": d["n"], "정답": d["c"] or 0} for d in daily]
                )
                st.markdown("**최근 30일 학습량**")
                st.bar_chart(df.set_index("날짜")[["풀이", "정답"]])
            except ImportError:
                st.caption("pandas 미설치 — 차트 생략")

        # 과목별 정답률
        with get_conn() as conn:
            by_subj = conn.execute(
                "SELECT s.name AS subject, COUNT(*) AS total, SUM(a.is_correct) AS correct "
                "FROM attempts a JOIN questions q ON a.question_id=q.id "
                "JOIN subjects s ON q.subject_id=s.id "
                "GROUP BY s.id ORDER BY s.session, s.id"
            ).fetchall()
        if by_subj:
            st.markdown("**과목별 정답률**")
            for r in by_subj:
                tot = r["total"] or 0
                cor = r["correct"] or 0
                rate = cor * 100 / tot if tot else 0
                bar = "█" * int(rate / 5) + "░" * (20 - int(rate / 5))
                st.markdown(
                    f"- **{r['subject']}** `{bar}` {rate:.0f}% ({cor}/{tot})"
                )

        # 연속 학습일 (streak)
        with get_conn() as conn:
            recent_days = conn.execute(
                "SELECT DISTINCT DATE(attempted_at) AS d FROM attempts ORDER BY d DESC LIMIT 60"
            ).fetchall()
        if recent_days:
            streak = 0
            cursor = dt.date.today()
            day_set = {dt.date.fromisoformat(r["d"]) for r in recent_days}
            while cursor in day_set:
                streak += 1
                cursor -= dt.timedelta(days=1)
            if streak > 0:
                st.success(f"🔥 연속 학습 {streak}일째 — 계속 가요!")

    st.divider()
    st.subheader("출력 이력")
    with get_conn() as conn:
        ws = conn.execute(
            "SELECT id, created_at, pdf_path, filter_json FROM worksheets ORDER BY id DESC LIMIT 30"
        ).fetchall()
    if not ws:
        st.info("아직 출력 이력이 없습니다.")
    else:
        for w in ws:
            with st.expander(f"#{w['id']} · {w['created_at']}"):
                st.code(w['filter_json'], language='json')
                st.caption(w['pdf_path'])

    st.divider()
    st.subheader("정답 체크 (수동)")
    qid = st.number_input("qid 입력", min_value=0, step=1, key="att_qid")
    if qid:
        with get_conn() as conn:
            q = conn.execute("SELECT id, body, answer FROM questions WHERE id=?", (qid,)).fetchone()
        if q:
            st.write(q['body'][:400])
            user_ans = st.radio("내 답", [1, 2, 3, 4, 5], horizontal=True)
            if st.button("기록"):
                with get_conn() as conn:
                    conn.execute(
                        "INSERT INTO attempts (question_id, attempted_at, is_correct) VALUES (?, ?, ?)",
                        (qid, dt.datetime.now().isoformat(timespec='seconds'),
                         1 if user_ans == q['answer'] else 0)
                    )
                    conn.commit()
                correct_ans = q['answer']
                msg = "✅ 정답!" if user_ans == correct_ans else f"❌ 오답 (정답: {correct_ans})"
                st.success(f"기록됨. {msg}")
        else:
            st.error("해당 qid 없음")

# ───────────── Tab 4: 데이터 현황 ─────────────
with tab4:
    cs1, cs2 = st.columns(2)
    with cs1:
        st.subheader("🔧 단원 잘못 분류 수정")
        fix_qid = st.number_input("수정할 qid", min_value=0, step=1, key="fix_qid")
        if fix_qid:
            with get_conn() as conn:
                q = conn.execute(
                    "SELECT q.body, q.chapter_id, c.name AS chap_name FROM questions q LEFT JOIN chapters c ON q.chapter_id=c.id WHERE q.id=?",
                    (fix_qid,)
                ).fetchone()
            if q:
                st.write(f"**현재 단원**: {q['chap_name'] or '(미분류)'}")
                st.text(q['body'][:300])
                chs = get_chapters()
                ch_options = {f"[{c['subject']}] {c['parent'] or '-'} > {c['name']}": c['id'] for c in chs}
                new_label = st.selectbox("새 단원", list(ch_options.keys()), key="new_chap")
                if st.button("적용"):
                    new_cid = ch_options[new_label]
                    with get_conn() as conn:
                        conn.execute("UPDATE questions SET chapter_id=? WHERE id=?", (new_cid, fix_qid))
                        conn.execute(
                            "INSERT INTO audit (question_id, field, old_value, new_value, source, confidence, resolved_at) VALUES (?, 'chapter_id', ?, ?, 'user-fix', 1.0, ?)",
                            (fix_qid, str(q['chapter_id']), str(new_cid), dt.datetime.now().isoformat(timespec='seconds'))
                        )
                        conn.commit()
                    st.success(f"수정됨: chapter_id={new_cid}")
                    st.cache_data.clear()
            else:
                st.error("qid 없음")

    with cs2:
        st.subheader("⚠️ 정답 NULL 보완")
        with get_conn() as conn:
            nulls = conn.execute("""
              SELECT q.id, e.year, s.name AS subj, q.question_number AS qnum, q.body
              FROM questions q JOIN exams e ON q.exam_id=e.id JOIN subjects s ON q.subject_id=s.id
              WHERE q.answer IS NULL OR q.answer<1 OR q.answer>5
              ORDER BY e.year, s.session, q.question_number
              LIMIT 20
            """).fetchall()
        st.write(f"NULL 건수: **{len(nulls)}**")
        if nulls:
            opts = {f"qid={r['id']} | {r['year']} {r['subj']} {r['qnum']}번": r['id'] for r in nulls}
            sel = st.selectbox("문제 선택", list(opts.keys()))
            sel_qid = opts[sel]
            with get_conn() as conn:
                q = conn.execute("SELECT body FROM questions WHERE id=?", (sel_qid,)).fetchone()
            st.text(q['body'][:300])
            new_ans = st.radio("정답", [1, 2, 3, 4, 5], horizontal=True, key="ans_fix")
            if st.button("정답 입력"):
                with get_conn() as conn:
                    conn.execute("UPDATE questions SET answer=? WHERE id=?", (new_ans, sel_qid))
                    conn.execute(
                        "INSERT INTO audit (question_id, field, old_value, new_value, source, confidence, resolved_at) VALUES (?, 'answer', NULL, ?, 'user-fix', 1.0, ?)",
                        (sel_qid, str(new_ans), dt.datetime.now().isoformat(timespec='seconds'))
                    )
                    conn.commit()
                st.success("입력됨")
                st.cache_data.clear()

    st.divider()
    st.subheader("📈 audit 변경 이력 (최근 100건)")
    with get_conn() as conn:
        recent = conn.execute("""
          SELECT a.resolved_at, a.question_id AS qid, a.field, a.source,
                 substr(a.old_value, 1, 60) AS old, substr(a.new_value, 1, 60) AS new
          FROM audit a ORDER BY a.id DESC LIMIT 100
        """).fetchall()
    import pandas as pd
    df = pd.DataFrame([dict(r) for r in recent])
    st.dataframe(df, width='stretch', height=300)
