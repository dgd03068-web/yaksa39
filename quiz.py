"""문제 풀이(퀴즈) 탭 — 학습 모드 / 시험 모드 + 오답노트 + 북마크.

app.py 에서 `from quiz import render_quiz_tab` 후 탭 안에서 호출.
세션 상태는 st.session_state['quiz'] 한 dict 로 관리.
"""
from __future__ import annotations

import datetime as dt
import random

import streamlit as st

from db import get_conn

CHOICE_LABELS = ["①", "②", "③", "④", "⑤"]


# ─────────────────────── DB helpers ───────────────────────
def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def record_attempt(qid: int, selected: int | None, is_correct: bool, mode: str) -> None:
    """풀이 1건 기록."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO attempts (question_id, attempted_at, is_correct, selected_answer, mode) "
            "VALUES (?, ?, ?, ?, ?)",
            (qid, _now(), 1 if is_correct else 0, selected, mode),
        )


def toggle_bookmark(qid: int) -> bool:
    """북마크 토글. 반환: 토글 후 상태(True=북마크됨)."""
    with get_conn() as conn:
        exists = conn.execute(
            "SELECT 1 FROM bookmarks WHERE question_id=?", (qid,)
        ).fetchone()
        if exists:
            conn.execute("DELETE FROM bookmarks WHERE question_id=?", (qid,))
            return False
        conn.execute(
            "INSERT INTO bookmarks (question_id, created_at) VALUES (?, ?)",
            (qid, _now()),
        )
        return True


def get_bookmarked_ids() -> set[int]:
    with get_conn() as conn:
        return {r[0] for r in conn.execute("SELECT question_id FROM bookmarks")}


def get_wrong_ids() -> set[int]:
    """각 문제의 가장 최근 풀이가 오답인 question_id 집합."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT a.question_id, a.is_correct
            FROM attempts a
            JOIN (
              SELECT question_id, MAX(id) AS max_id
              FROM attempts GROUP BY question_id
            ) last ON a.id = last.max_id
            """
        ).fetchall()
    return {r[0] for r in rows if r[1] == 0}


def _fetch_by_ids(qids: list[int]) -> list[dict]:
    if not qids:
        return []
    with get_conn() as conn:
        ph = ",".join("?" * len(qids))
        rows = conn.execute(
            f"""
            SELECT q.id, q.body, q.has_image, q.answer, q.explanation, q.concept_tag,
                   e.year AS year, q.question_number AS qnum,
                   s.name AS subject, c.name AS chapter
            FROM questions q
            JOIN exams e ON q.exam_id = e.id
            JOIN subjects s ON q.subject_id = s.id
            LEFT JOIN chapters c ON q.chapter_id = c.id
            WHERE q.id IN ({ph})
            """,
            qids,
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["choices"] = [
                (c["number"], c["text"])
                for c in conn.execute(
                    "SELECT number, text FROM choices WHERE question_id=? ORDER BY number",
                    (d["id"],),
                )
            ]
            out.append(d)
    # 입력 qids 순서 유지
    order = {q: i for i, q in enumerate(qids)}
    out.sort(key=lambda d: order.get(d["id"], 1e9))
    return out


def _fetch_by_chapters(chapter_ids: list[int], only_explained: bool, only_answered: bool) -> list[dict]:
    """단원 선택 → 문제 목록. only_explained=해설 있는 것만, only_answered=정답 1~5 있는 것만."""
    if not chapter_ids:
        return []
    conds = ["q.chapter_id IN ({})".format(",".join("?" * len(chapter_ids)))]
    params: list = list(chapter_ids)
    if only_explained:
        conds.append("q.explanation IS NOT NULL AND q.explanation != ''")
    if only_answered:
        conds.append("q.answer BETWEEN 1 AND 5")
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT q.id FROM questions q WHERE {' AND '.join(conds)}", params
        ).fetchall()
        qids = [r[0] for r in rows]
    return _fetch_by_ids(qids)


def get_chapter_options() -> list[dict]:
    """선택용 chapter 목록: leaf chapter(자식 없는) 위주, 라벨에 과목·부모 포함."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.name, p.name AS parent, s.name AS subject, s.session,
                   (SELECT COUNT(*) FROM questions q WHERE q.chapter_id=c.id) AS qcnt,
                   (SELECT COUNT(*) FROM chapters cc WHERE cc.parent_id=c.id) AS childcnt
            FROM chapters c
            LEFT JOIN chapters p ON c.parent_id = p.id
            LEFT JOIN subjects s ON c.subject_id = s.id
            ORDER BY s.session, s.id, c.chapter_no, c.id
            """
        ).fetchall()
    opts = []
    for r in rows:
        if r["childcnt"] > 0 or r["qcnt"] == 0:
            continue  # 부모 chapter·빈 chapter 제외
        label = f"[{r['subject']}]"
        if r["parent"]:
            label += f" {r['parent']} ·"
        label += f" {r['name']} ({r['qcnt']})"
        opts.append({"id": r["id"], "label": label})
    return opts


# ─────────────────────── 채점 ───────────────────────
def _grade(q: dict, selected: int | None) -> bool:
    return selected is not None and selected == q["answer"]


def _explanation_html(text: str) -> str:
    """해설 텍스트 → 간단 HTML (핵심·정답마커 강조)."""
    import html as _html

    s = _html.escape(text or "")
    s = s.replace("[핵심]", '<b style="background:#fff3cd;padding:1px 4px;border-radius:3px;">[핵심]</b>')
    s = s.replace("✅", '<span style="color:#198754;font-weight:700;">✅</span>')
    return s.replace("\n", "<br>")


# ─────────────────────── UI ───────────────────────
def render_quiz_tab() -> None:
    q = st.session_state.setdefault("quiz", {"phase": "setup"})
    phase = q.get("phase", "setup")
    if phase == "setup":
        _render_setup()
    elif phase == "running":
        if q["mode"] == "study":
            _render_study()
        else:
            _render_exam()
    elif phase == "result":
        _render_result()


def _start_quiz(questions: list[dict], mode: str, shuffle: bool = True) -> None:
    if shuffle:
        random.shuffle(questions)
    st.session_state["quiz"] = {
        "phase": "running",
        "mode": mode,
        "questions": questions,
        "idx": 0,
        "answers": {},   # qid -> selected int
        "checked": {},   # qid -> bool (study 모드 채점 여부)
        "started_at": _now(),
    }
    st.rerun()


def _render_setup() -> None:
    st.subheader("✏️ 문제 풀이")
    st.caption("해설이 있는 문제를 골라 학습/시험 모드로 풀고, 오답·북마크를 관리하세요.")

    wrong_ids = get_wrong_ids()
    bookmark_ids = get_bookmarked_ids()

    source = st.radio(
        "출제 범위",
        ["단원별", f"오답노트 ({len(wrong_ids)})", f"북마크 ({len(bookmark_ids)})"],
        horizontal=True,
    )

    questions: list[dict] = []
    if source == "단원별":
        opts = get_chapter_options()
        labels = [o["label"] for o in opts]
        label_to_id = {o["label"]: o["id"] for o in opts}
        picked = st.multiselect("단원 선택 (여러 개 가능)", labels,
                                placeholder="과목·단원을 검색해서 선택하세요")
        c1, c2 = st.columns(2)
        only_expl = c1.checkbox("해설 있는 문제만", value=True)
        only_ans = c2.checkbox("정답 있는 문제만", value=True)
        if picked:
            chap_ids = [label_to_id[p] for p in picked]
            questions = _fetch_by_chapters(chap_ids, only_expl, only_ans)
    elif source.startswith("오답노트"):
        if wrong_ids:
            questions = _fetch_by_ids(list(wrong_ids))
        else:
            st.info("아직 오답이 없습니다. 문제를 풀면 틀린 문제가 여기 모입니다.")
    else:  # 북마크
        if bookmark_ids:
            questions = _fetch_by_ids(list(bookmark_ids))
        else:
            st.info("북마크한 문제가 없습니다. 풀이 중 ⭐ 버튼으로 북마크하세요.")

    if not questions:
        return

    st.success(f"가능한 문제: {len(questions)}개")
    c1, c2 = st.columns(2)
    mode_label = c1.radio("모드", ["학습 모드 (한 문제씩 즉시 채점)", "시험 모드 (일괄 채점)"])
    mode = "study" if mode_label.startswith("학습") else "exam"
    max_n = len(questions)
    n = c2.slider("문제 수", 1, max_n, min(20, max_n))

    if st.button("🚀 풀이 시작", type="primary"):
        _start_quiz(questions[:n] if n < len(questions) else list(questions), mode)


def _bookmark_button(qid: int, key_prefix: str) -> None:
    marked = qid in st.session_state.setdefault("_bm_cache", get_bookmarked_ids())
    star = "⭐" if marked else "☆"
    if st.button(f"{star} 북마크", key=f"{key_prefix}_bm_{qid}"):
        new_state = toggle_bookmark(qid)
        cache = st.session_state["_bm_cache"]
        if new_state:
            cache.add(qid)
        else:
            cache.discard(qid)
        st.rerun()


def _render_question_body(q: dict) -> None:
    st.markdown(f"##### {q['body']}")
    if q["has_image"]:
        st.caption("🖼 [그림 비공개] — 원본 시험지 참고")


def _render_study() -> None:
    q = st.session_state["quiz"]
    questions = q["questions"]
    idx = q["idx"]
    total = len(questions)
    cur = questions[idx]
    qid = cur["id"]

    st.progress((idx) / total, text=f"학습 모드 · {idx + 1} / {total}")
    head_l, head_r = st.columns([4, 1])
    head_l.caption(f"{cur['year']}년 {cur['qnum']}번 · [{cur['subject']}] {cur.get('chapter') or '미분류'}")
    with head_r:
        _bookmark_button(qid, "study")

    _render_question_body(cur)

    checked = q["checked"].get(qid, False)
    labels = [f"{CHOICE_LABELS[n-1] if 1 <= n <= 5 else n} {t}" for n, t in cur["choices"]]
    nums = [n for n, _ in cur["choices"]]

    prev = q["answers"].get(qid)
    sel_idx = nums.index(prev) if prev in nums else None
    picked = st.radio("정답 선택", labels, index=sel_idx,
                      key=f"study_radio_{qid}", disabled=checked)
    selected = nums[labels.index(picked)] if picked else None

    if not checked:
        if st.button("제출", type="primary", key=f"study_submit_{qid}"):
            if selected is None:
                st.warning("보기를 선택하세요.")
            else:
                is_correct = _grade(cur, selected)
                q["answers"][qid] = selected
                q["checked"][qid] = True
                record_attempt(qid, selected, is_correct, "study")
                st.rerun()
    else:
        selected = q["answers"].get(qid)
        is_correct = _grade(cur, selected)
        ans_label = CHOICE_LABELS[cur["answer"] - 1] if cur["answer"] and 1 <= cur["answer"] <= 5 else "—"
        if is_correct:
            st.success(f"⭕ 정답입니다! (정답: {ans_label})")
        else:
            sel_label = CHOICE_LABELS[selected - 1] if selected and 1 <= selected <= 5 else "—"
            st.error(f"❌ 오답 — 선택: {sel_label} / 정답: {ans_label}")
        if cur.get("concept_tag"):
            st.markdown(f"<span style='color:#cc0000;font-weight:700;'>{cur['concept_tag']}</span>",
                        unsafe_allow_html=True)
        if cur.get("explanation"):
            st.markdown(
                f"<div style='background:#f7f7f7;border-left:3px solid #cc0000;"
                f"padding:8px 12px;border-radius:4px;'>{_explanation_html(cur['explanation'])}</div>",
                unsafe_allow_html=True,
            )
        else:
            st.caption("해설 미작성")

        nav_l, nav_r = st.columns(2)
        if idx + 1 < total:
            if nav_r.button("다음 문제 ▶", type="primary", key=f"study_next_{qid}"):
                q["idx"] += 1
                st.rerun()
        else:
            if nav_r.button("결과 보기 🏁", type="primary", key=f"study_finish_{qid}"):
                q["phase"] = "result"
                st.rerun()
        if idx > 0 and nav_l.button("◀ 이전 문제", key=f"study_prev_{qid}"):
            q["idx"] -= 1
            st.rerun()

    if st.button("⏹ 그만두고 결과 보기", key=f"study_quit_{qid}"):
        q["phase"] = "result"
        st.rerun()


def _render_exam() -> None:
    q = st.session_state["quiz"]
    questions = q["questions"]
    total = len(questions)

    st.subheader(f"📝 시험 모드 · 총 {total}문제")
    st.caption("모든 문제를 풀고 맨 아래 '제출하고 채점' 버튼을 누르세요.")

    with st.form("exam_form"):
        for i, cur in enumerate(questions):
            qid = cur["id"]
            st.markdown(f"**{i + 1}. ({cur['year']}년 {cur['qnum']}번)** {cur['body']}")
            if cur["has_image"]:
                st.caption("🖼 [그림 비공개]")
            labels = [f"{CHOICE_LABELS[n-1] if 1 <= n <= 5 else n} {t}" for n, t in cur["choices"]]
            st.radio("정답", labels, index=None, key=f"exam_radio_{qid}",
                     label_visibility="collapsed")
            st.divider()
        submitted = st.form_submit_button("✅ 제출하고 채점", type="primary")

    if submitted:
        for cur in questions:
            qid = cur["id"]
            picked = st.session_state.get(f"exam_radio_{qid}")
            selected = None
            if picked:
                labels = [f"{CHOICE_LABELS[n-1] if 1 <= n <= 5 else n} {t}" for n, t in cur["choices"]]
                nums = [n for n, _ in cur["choices"]]
                selected = nums[labels.index(picked)]
            q["answers"][qid] = selected
            record_attempt(qid, selected, _grade(cur, selected), "exam")
        q["phase"] = "result"
        st.rerun()


def _render_result() -> None:
    q = st.session_state["quiz"]
    questions = q["questions"]
    answers = q["answers"]
    graded = [(cur, answers.get(cur["id"]), _grade(cur, answers.get(cur["id"])))
              for cur in questions if cur["id"] in answers]
    total = len(graded)
    correct = sum(1 for _, _, ok in graded if ok)

    st.subheader("🏁 결과")
    if total == 0:
        st.info("푼 문제가 없습니다.")
    else:
        pct = correct / total * 100
        c1, c2, c3 = st.columns(3)
        c1.metric("점수", f"{correct} / {total}")
        c2.metric("정답률", f"{pct:.0f}%")
        c3.metric("오답", f"{total - correct}개")
        st.progress(correct / total)

    # 문제별 펼침
    with st.expander("📋 문제별 정오답·해설 보기", expanded=(total <= 20)):
        for i, (cur, selected, ok) in enumerate(graded, 1):
            ans_label = CHOICE_LABELS[cur["answer"] - 1] if cur["answer"] and 1 <= cur["answer"] <= 5 else "—"
            sel_label = CHOICE_LABELS[selected - 1] if selected and 1 <= selected <= 5 else "미선택"
            mark = "⭕" if ok else "❌"
            st.markdown(f"**{mark} {i}. ({cur['year']}년 {cur['qnum']}번)** "
                        f"선택 {sel_label} / 정답 {ans_label}")
            if not ok:
                st.markdown(f"<span style='font-size:0.9em;color:#555;'>{cur['body'][:120]}…</span>",
                            unsafe_allow_html=True)
                if cur.get("explanation"):
                    st.markdown(
                        f"<div style='background:#f7f7f7;border-left:3px solid #cc0000;"
                        f"padding:6px 10px;border-radius:4px;font-size:0.9em;'>"
                        f"{_explanation_html(cur['explanation'])}</div>",
                        unsafe_allow_html=True,
                    )
            st.divider()

    c1, c2 = st.columns(2)
    if c1.button("🔁 새 문제 풀기", type="primary"):
        st.session_state["quiz"] = {"phase": "setup"}
        st.session_state.pop("_bm_cache", None)
        st.rerun()
    wrong = [cur["id"] for cur, _, ok in graded if not ok]
    if wrong and c2.button(f"❌ 오답 {len(wrong)}개 다시 풀기"):
        _start_quiz(_fetch_by_ids(wrong), q["mode"])
