"""UI 에 노출할 실험 변수 정의 + Config 적용 + 프리셋.

변수를 추가하려면:
  1) PARAMS 에 _p(...) (한 값) 또는 _h(...) (0~23시 시간대별 24개 값) 한 줄 추가
  2) apply_params() 에서 Config 로 연결.
화면은 /api/schema 로 이 정의를 읽어 자동으로 만들어진다. 모든 변수·그룹에는 help(? 설명)를 단다.
"""
from config import Config, CT, MRI, HOURS, by_hours

MAX_FINAL, MAX_PRELIM = 8, 4
READER_OPTIONS = ["off", "CT", "MRI", "CT+MRI"]
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
PRELIM_TARGETS = ["응급 전체", "ED 응급만", "ED·IP 응급", "없음"]
_PT_CODE = {"응급 전체": "ALL", "ED 응급만": "ED", "ED·IP 응급": "ED_IP", "없음": "NONE"}

GROUPS = [
    ("demand", "수요", "검사 요청이 얼마나 들어오는지. 총량은 아래 '전체 수요 배율'과 응급 비율로, 시간대별 모양은 '24시간 편집기 → 수요' 탭에서 정한다. 도착은 포아송(무작위) 과정이고, 같은 시드면 같은 요청이 같은 시각에 들어온다."),
    ("scan", "촬영 자원", "CT·MRI 장비 대수. 실제로 동시에 촬영하는 수는 min(장비 수, 그 시각에 켜져 있는 촬영 슬롯 수) 이다. 슬롯은 촬영을 맡을 수 있는 인력/배정 한 자리로, '24시간 편집기 → 촬영 슬롯' 탭에서 자리를 추가·삭제하고 시간대별로 켠다."),
    ("staff", "판독 근무표", "가판독 자리(응급 요청의 초기 소견)와 최종판독 자리(모든 요청의 최종 보고)를 '24시간 편집기 → 판독 근무표' 탭에서 추가·삭제하고, 자리마다 몇 시에 무엇을 읽는지 칠해서 정한다. off = 그 시각엔 근무하지 않음. 시작값은 가판독 1자리, 최종판독 1자리다."),
    ("times", "처리시간 (분, 삼각분포)", "각 단계가 걸리는 시간. 삼각분포로 무작위 추출하며 '최소 / 가장 흔한 값(최빈) / 최대' 세 숫자로 정한다. 평균은 (최소+최빈+최대)÷3 이다."),
    ("cost", "비용·수입 (만원)", "수입과 지출 항목을 직접 정한다. 체크한 항목만 일평균 순이익(수입−지출)에 들어간다. 모든 금액은 만원 단위의 가정값이며 실제 병원 자료가 아니다 — 발표에서는 가정이라고 밝힐 것. 항목은 추가·수정·삭제할 수 있다."),
    ("eval", "평가 기간", "시뮬레이션을 돌리는 날짜 구성. 준비 구간은 병원이 비어 있는 상태에서 시작하는 영향을 없애려고 집계에서 뺀다. 후속 관찰 동안에도 요청은 계속 들어오지만 집계에는 넣지 않고 마무리만 지켜본다."),
]


def _p(key, label, group, default, unit="", min=None, max=None, step=None,
       type="number", help="", grid=None, options=None):
    d = {"key": key, "label": label, "group": group, "default": default, "unit": unit,
         "type": type, "help": help}
    if min is not None:
        d["min"] = min
    if max is not None:
        d["max"] = max
    if step is not None:
        d["step"] = step
    if grid:
        d["grid"] = {"row": grid[0], "col": grid[1]}
    if options:
        d["options"] = options
    return d


def _h(key, label, group, default, kind="number", unit="", min=None, max=None, step=None,
       help="", options=None, short=""):
    """0~23시 시간대별 값 24개 (시계 시각 기준)."""
    d = _p(key, label, group, list(default), unit, min, max, step, type="hourly", help=help, options=options)
    d["kind"] = kind
    d["short"] = short or label
    return d


def _r(key, label, group, default_rows, kind, options=None, min_rows=1, max_rows=8, short="", help="",
       row_label=""):
    """자리(행)를 추가·삭제할 수 있는 시간대별 표. 값 = 행의 목록, 각 행은 0~23시 24개 값."""
    d = _p(key, label, group, [list(r) for r in default_rows], type="rows", help=help, options=options)
    d.update(kind=kind, min_rows=min_rows, max_rows=max_rows, short=short or label, row_label=row_label or label)
    return d


def _rows_by_count(counts):
    """시간대별 '켜진 자리 수' 목록 -> 자리(행)별 on/off 표 (i번째 행은 count > i 인 시각에 켜짐)."""
    n = max(max(counts), 1)
    return [[c > i for c in counts] for i in range(n)]


def put(lst, start, end, value):
    """start~end시(끝 미포함, 자정 넘김 가능)에 value 를 넣은 새 목록."""
    out = list(lst)
    h = start % 24
    for _ in range((end - start) % 24 or 24):
        out[h] = value
        h = (h + 1) % 24
    return out


def _day(day, night):
    return [by_hours(day, night)[h] for h in HOURS]


# 하루 수요 모양 (상대 가중치, 0~23시). 외래 진료 시간대에 몰리는 형태의 '예시'일 뿐이다.
_SHAPE = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 2.0, 6.0, 10.0, 11.0, 10.0,
          7.0, 9.0, 10.0, 9.0, 7.0, 5.0, 3.0, 2.5, 1.5, 1.5, 1.2, 1.0]


def _curve(total_per_day):
    s = sum(_SHAPE)
    return [round(total_per_day * w / s, 1) for w in _SHAPE]



PARAMS = [
    # 수요
    _p("start_hour", "시작 시각", "demand", 0, "시", 0, 23, 1,
       help="t=0 이 하루 중 몇 시인지. 명세 8.7 기준은 2024-01-01(월) 00:00 이라 0시이다. 08시로 바꾸면 첫날 08:00 에 시작한다."),
    _p("demand_mult", "전체 수요 배율", "demand", 1.0, "×", 0.2, 3.0, 0.05,
       help="아래 24시간 수요 곡선(CT·MRI)에 일괄로 곱한다. 1.2 = 모든 시간대 수요 20% 증가. 곡선 모양은 그대로 두고 총량만 바꿀 때 쓴다."),
    _p("ct_urgent", "CT 응급 비율", "demand", 20, "%", 0, 100, 1,
       help="CT 요청 중 응급인 비율. 응급은 대기열에서 일반보다 먼저 처리되고, 최종판독 전에 가판독 단계를 거친다."),
    _p("mri_urgent", "MRI 응급 비율", "demand", 10, "%", 0, 100, 1,
       help="MRI 요청 중 응급인 비율. 응급은 촬영 대기열 우선 + 가판독 경로를 탄다."),
    _p("ct_daily_var", "CT 일별 변동 (±)", "demand", 0.0, "건/시간", 0, 10, 0.1,
       help="0이면 매일 같은 수요. 1로 두면 매일 그날의 CT 도착률이 설정값(24시간 편집기 값)에서 -1 ~ +1 건/시간 사이 임의의 값만큼 달라진다(하루 동안은 모든 시간대에 같은 값, 0 아래로는 내려가지 않음). 날마다 다른 값이 뽑히고, 같은 시드면 같은 날엔 같은 값이다."),
    _p("mri_daily_var", "MRI 일별 변동 (±)", "demand", 0.0, "건/시간", 0, 5, 0.1,
       help="MRI 도착률의 일별 변동. 0이면 변동 없음. MRI는 도착률 자체가 작으므로(기준 야간 0.3건/시간) 0.1~0.3 정도부터 써 보는 것을 권한다."),
    _p("mix_ed", "환자군 비중 ED", "demand", 30, "가중치", 0, 100, 1,
       help="응급실(ED) 요청의 상대 비중. 24시간 같은 가중치로 발생한다. 실측이 없는 가정값이다."),
    _p("mix_ip", "환자군 비중 IP", "demand", 25, "가중치", 0, 100, 1,
       help="입원(IP) 요청의 상대 비중. 주간(08~20시)에는 이 값, 야간에는 절반으로 발생한다. 가정값."),
    _p("mix_op", "환자군 비중 OP", "demand", 45, "가중치", 0, 100, 1,
       help="외래(OP) 요청의 상대 비중. 평일 08~17시에만 발생한다. 가정값."),
    _p("prelim_target", "가판독 대상", "staff", "응급 전체", type="choice", options=PRELIM_TARGETS,
       help="어떤 응급 요청이 가판독을 거치는지. 대상이 아닌 응급은 촬영 후 바로 최종판독으로 간다(명세 7.5). 환자군은 요청 시점에 정해진다."),
    _p("start_weekday", "시작 요일", "demand", "월", type="choice", options=WEEKDAYS,
       help="시뮬레이션 첫날의 요일. 요일별 배수가 어느 날에 적용되는지 정한다. 준비 구간도 날짜에 포함되므로, 집계 구간이 무슨 요일에 시작하는지는 시작 요일 + 준비 일수로 정해진다."),
    # 촬영 자원
    _p("ct_units", "CT 장비 수", "scan", 4, "대", 1, 8, 1,
       help="CT 장비 대수. 실제 동시 촬영 수 = min(장비 수, 그 시각의 CT 슬롯). 장비만 늘리고 슬롯을 그대로 두면 효과가 없을 수 있다."),
    _p("mri_units", "MRI 장비 수", "scan", 3, "대", 1, 6, 1,
       help="MRI 장비 대수. 실제 동시 촬영 수 = min(장비 수, 그 시각의 MRI 슬롯)."),
    # 시간대별 (24시간 편집기)
    _h("ct_rate", "CT 도착률", "demand", _curve(110), "number", "건/시간", 0, 30, 0.1, short="CT 도착률",
       help="그 시각에 CT 검사 요청이 들어오는 평균 속도(건/시간). 기본값은 하루 110건(명세 8.2)을 외래 피크형 모양으로 나눈 값. 전체 수요 배율이 곱해진다. 편집기에서 같은 값으로 칠한 칸을 다시 클릭하면 0(요청 없음)이 된다."),
    _h("mri_rate", "MRI 도착률", "demand", _curve(45), "number", "건/시간", 0, 10, 0.1, short="MRI 도착률",
       help="그 시각에 MRI 검사 요청이 들어오는 평균 속도(건/시간). 기본값은 하루 45건(명세 8.2)을 같은 모양으로 나눈 값."),
    _r("ct_slots", "CT 촬영 슬롯", "scan", _rows_by_count(_day(2, 1)), "bool", min_rows=1, max_rows=8, short="CT 슬롯",
       row_label="CT 슬롯",
       help="CT 촬영에 투입된 동시 촬영 자리(촬영 인력)들. 자리를 추가·삭제하고, 칸을 클릭하면 그 시각에 켜지거나 꺼진다. 그 시각에 켜진 자리 수가 CT 슬롯 수이고, 실제 동시 촬영 = min(CT 장비 수, 슬롯 수). 모두 꺼진 시각에는 새 촬영을 시작하지 못한다(진행 중인 건은 끝까지)."),
    _r("mri_slots", "MRI 촬영 슬롯", "scan", _rows_by_count(_day(2, 1)), "bool", min_rows=1, max_rows=6, short="MRI 슬롯",
       row_label="MRI 슬롯",
       help="MRI 동시 촬영 자리들. 켜진 자리 수 = 그 시각의 MRI 슬롯 수이고, 실제 동시 촬영 = min(MRI 장비 수, 슬롯 수)."),
    _r("safety_slots", "MRI 안전확인 슬롯", "scan", _rows_by_count(_day(1, 1)), "bool", min_rows=1, max_rows=4, short="안전확인 슬롯",
       row_label="안전확인",
       help="MRI 촬영 전 금속·이식물 등 안전확인을 맡는 자리들. 켜진 자리가 하나도 없는 시각에는 새 안전확인을 시작할 수 없어 MRI가 밀린다."),
    _r("prelim", "가판독 자리", "staff", [[True] * 24], "bool", min_rows=1, max_rows=MAX_PRELIM, short="가판독 자리",
       row_label="가판독",
       help="응급 요청의 가판독(초기 소견)을 맡는 자리들. 시작값은 1자리. 자리를 추가하면 응급이 몰릴 때 동시에 여러 건을 가판독할 수 있다. 칸을 클릭하면 그 시각의 근무가 켜지거나 꺼진다. 모든 자리가 꺼진 시각에는 가판독이 시작되지 않고 대기한다."),
    _r("final", "최종판독 자리", "staff", [["CT+MRI"] * 24, put(["off"] * 24, 8, 24, "CT+MRI"), put(["off"] * 24, 8, 16, "CT+MRI")], "choice", options=READER_OPTIONS, min_rows=1, max_rows=MAX_FINAL,
       short="최종판독 자리", row_label="최종판독",
       help="최종판독(모든 요청의 마지막 단계)을 맡는 자리들. 기본값은 명세 8.4: 08~16시 3명, 16~24시 2명, 00~08시 1명(하루 48인시). 자리를 추가·삭제하고, 칸마다 그 시각에 읽는 검사 종류를 칠한다: off=근무 안 함, CT / MRI / CT+MRI. '붓'과 같은 값인 칸을 다시 클릭하면 off 가 된다. 시각이 바뀌어도 읽던 건은 끝까지 읽고, 근무가 끝난 뒤 이어서 일하면 초과근무로 집계된다."),
]

# 요일별 수요 배수 (한 줄짜리 표로 보인다)
for _i, _d in enumerate(WEEKDAYS):
    PARAMS.append(_p("wd_mult_%d" % _i, "%s요일 수요 배수" % _d, "demand", 1.0, "×", 0, 5, 0.05, grid=("요일 배수", _d),
                     help="%s요일의 모든 시간대 도착률에 곱한다. 1 = 그대로, 1.3 = 30%% 증가, 0.6 = 40%% 감소. 일별 변동을 쓰면 (설정값 ± 변동) 에 이 배수가 곱해진다." % _d))
    PARAMS[-1]["rowhelp"] = "요일마다 수요 크기를 다르게 한다. 주말·휴일에 검사가 줄거나 월요일에 몰리는 식의 가정을 넣을 때 쓴다. 실제 병원 자료 없이 임의로 정한 값은 가정값임을 발표에 밝혀야 한다."

# 처리시간 (삼각분포)
_TIMES = [
    ("ct_scan", "CT 촬영(장비 점유)", (10, 15, 20), "CT 한 건을 찍는 동안 장비 한 자리를 차지하는 시간."),
    ("mri_safety", "MRI 안전확인", (3, 5, 7), "MRI 촬영 전 안전확인에 드는 시간. 촬영 장비는 아직 쓰지 않고 안전확인 자리만 차지한다."),
    ("mri_scan", "MRI 촬영(장비 점유)", (30, 35, 40), "MRI 한 건을 찍는 동안 장비 한 자리를 차지하는 시간."),
    ("prelim_ct", "CT 가판독", (5, 15, 25), "응급 CT 의 초기 소견(가판독)에 드는 시간."),
    ("prelim_mri", "MRI 가판독", (8, 18, 28), "응급 MRI 의 초기 소견(가판독)에 드는 시간."),
    ("final_ct", "CT 최종판독", (5, 15, 25), "CT 최종판독 한 건에 드는 시간."),
    ("final_mri", "MRI 최종판독", (8, 18, 28), "MRI 최종판독 한 건에 드는 시간."),
]
for key, label, (lo, mode, hi), hlp in _TIMES:
    for suffix, col, v, ch in (("min", "최소", lo, "가장 짧게 걸리는 경우"), ("mode", "최빈", mode, "가장 흔한 경우(분포의 꼭짓점)"),
                               ("max", "최대", hi, "가장 길게 걸리는 경우")):
        PARAMS.append(_p("%s_%s" % (key, suffix), "%s %s" % (label, col), "times", v, "분", 0, 600, 1,
                         grid=(label, col), help="%s %s: %s. 최소 ≤ 최빈 ≤ 최대여야 한다." % (hlp, col, ch)))
        PARAMS[-1]["rowhelp"] = hlp + " 삼각분포라 평균은 (최소+최빈+최대)÷3 이다."
# 비용·수입 항목 (사용자가 추가·삭제·체크). 금액 단위는 만원.
PERIODS = ["일", "주", "월", "연"]
# 항목 하나의 '대상'(무엇에 단가를 곱하는가). kind: fixed 고정 / stock 보유·증설 대수 / usage 가동 시간 / count 건수
COST_BASES = [
    {"id": "fixed", "label": "병원 전체 고정", "kind": "fixed", "unit": "", "periods": PERIODS, "filters": [],
     "desc": "장비·인력과 상관없이 정해진 금액이 기간마다 발생(임대료·관리비·기본 수입 등)"},
    {"id": "ct_owned", "label": "CT 보유 대수", "kind": "stock", "unit": "대", "periods": PERIODS, "filters": [],
     "desc": "보유한 CT 장비 대수 × 단가"},
    {"id": "mri_owned", "label": "MRI 보유 대수", "kind": "stock", "unit": "대", "periods": PERIODS, "filters": [],
     "desc": "보유한 MRI 장비 대수 × 단가"},
    {"id": "ct_new", "label": "CT 신규 증설 대수", "kind": "stock", "unit": "대", "periods": PERIODS, "filters": [],
     "desc": "기준 보유 대수보다 늘린 CT 대수 × 단가(구매비를 평가기간으로 환산할 때)"},
    {"id": "mri_new", "label": "MRI 신규 증설 대수", "kind": "stock", "unit": "대", "periods": PERIODS, "filters": [],
     "desc": "기준 보유 대수보다 늘린 MRI 대수 × 단가"},
    {"id": "ct_active", "label": "CT 가동 장비 시간", "kind": "usage", "unit": "대", "periods": ["시간"], "filters": ["time", "weekday"],
     "desc": "실제로 켜 둔 CT 장비 수(min(장비, 슬롯)) × 가동 시간"},
    {"id": "mri_active", "label": "MRI 가동 장비 시간", "kind": "usage", "unit": "대", "periods": ["시간"], "filters": ["time", "weekday"],
     "desc": "실제로 켜 둔 MRI 장비 수 × 가동 시간"},
    {"id": "tech_slots", "label": "촬영·안전확인 인력 시간", "kind": "usage", "unit": "자리", "periods": ["시간"], "filters": ["time", "weekday"],
     "desc": "켜진 CT·MRI 촬영 슬롯과 안전확인 슬롯 수 × 근무 시간"},
    {"id": "reader_hours", "label": "판독 의료진 근무 시간", "kind": "usage", "unit": "자리", "periods": ["시간"], "filters": ["time", "weekday"],
     "desc": "근무표에 켜진 최종판독·가판독 자리 수 × 근무 시간"},
    {"id": "overtime", "label": "초과근무 시간", "kind": "usage", "unit": "", "periods": ["시간"], "filters": [],
     "desc": "예정 근무가 끝난 뒤 읽던 건을 마무리한 시간의 합"},
    {"id": "ct_scans", "label": "CT 촬영 완료 건수", "kind": "count", "unit": "건", "periods": ["건"], "filters": ["time", "weekday", "patient", "urgency"],
     "desc": "집계 구간에 촬영을 마친 CT 건수 × 건당 금액"},
    {"id": "mri_scans", "label": "MRI 촬영 완료 건수", "kind": "count", "unit": "건", "periods": ["건"], "filters": ["time", "weekday", "patient", "urgency"],
     "desc": "집계 구간에 촬영을 마친 MRI 건수 × 건당 금액"},
    {"id": "all_scans", "label": "촬영 완료 건수 (CT+MRI)", "kind": "count", "unit": "건", "periods": ["건"], "filters": ["time", "weekday", "patient", "urgency"],
     "desc": "촬영을 마친 모든 건수 × 건당 금액"},
    {"id": "final_reads", "label": "최종판독 완료 건수", "kind": "count", "unit": "건", "periods": ["건"], "filters": ["time", "weekday", "patient", "urgency"],
     "desc": "최종판독이 끝난 건수 × 건당 금액(판독료 수입·건당 수당 등)"},
    {"id": "prelim_reads", "label": "가판독 완료 건수", "kind": "count", "unit": "건", "periods": ["건"], "filters": ["time", "weekday", "patient", "urgency"],
     "desc": "가판독이 끝난 건수 × 건당 금액"},
]
COST_BY_BASIS = {b["id"]: b for b in COST_BASES}
COST_FILTERS = {"time": ["전체", "주간", "야간"], "weekday": ["전체", "평일", "주말"],
                "patient": ["전체", "ED", "IP", "OP"], "urgency": ["전체", "응급", "일반"]}


def _ci(id, name, type, basis, period, amount, **flt):
    d = {"id": id, "name": name, "type": type, "enabled": True, "basis": basis, "period": period, "amount": amount,
         "time": "전체", "weekday": "전체", "patient": "전체", "urgency": "전체"}
    d.update(flt)
    return d


# 기본 항목 — 금액은 모두 '가정값'(실제 병원 자료 아님). 팀에서 확정해 고친다.
DEFAULT_COST_ITEMS = [
    _ci("e_ct_maint", "CT 유지비", "지출", "ct_owned", "월", 500),
    _ci("e_mri_maint", "MRI 유지비", "지출", "mri_owned", "월", 1000),
    _ci("e_ct_new", "CT 신규 증설비", "지출", "ct_new", "월", 1500),
    _ci("e_mri_new", "MRI 신규 증설비", "지출", "mri_new", "월", 3000),
    _ci("e_ct_run", "CT 가동 운영비", "지출", "ct_active", "시간", 3),
    _ci("e_mri_run", "MRI 가동 운영비", "지출", "mri_active", "시간", 6),
    _ci("e_tech", "촬영 인력 인건비", "지출", "tech_slots", "시간", 1.5),
    _ci("e_reader", "판독 의료진 인건비", "지출", "reader_hours", "시간", 10),
    _ci("e_ot", "초과근무 수당", "지출", "overtime", "시간", 15),
    _ci("i_ct", "CT 촬영 수입", "수입", "ct_scans", "건", 10),
    _ci("i_mri", "MRI 촬영 수입", "수입", "mri_scans", "건", 30),
]
PARAMS += [
    _p("base_ct", "기준 CT 보유 대수", "cost", 4, "대", 0, 20, 1,
       help="이미 가진 CT 대수. 장비 수를 이보다 늘리면 '신규 증설 대수'로 계산된다 (명세 8.1: 4대)."),
    _p("base_mri", "기준 MRI 보유 대수", "cost", 3, "대", 0, 20, 1,
       help="이미 가진 MRI 대수. 늘린 분은 '신규 증설 대수'로 계산된다 (명세 8.1: 3대)."),
    {"key": "cost_items", "label": "비용·수입 항목", "group": "cost", "default": DEFAULT_COST_ITEMS, "unit": "만원", "type": "costs",
     "help": "체크한 항목만 계산에 들어간다. 항목마다 대상(무엇에 곱하는지), 단가 기간(시간·일·주·월·연·건당), 조건(시간대·요일·환자군·긴급도)을 정할 수 있고 직접 추가·삭제할 수 있다. 금액은 만원 단위 가정값이다."},
]

# 평가
PARAMS += [
    _p("warmup_days", "준비 구간 (집계 제외)", "eval", 7, "일", 0, 90, 1,
       help="병원이 빈 상태로 시작하는 영향을 없애기 위해 처음 며칠은 집계에서 뺀다. t=0 의 시각은 위 수요 그룹의 시작 시각으로 정한다."),
    _p("measure_days", "집계 구간", "eval", 28, "일", 1, 365, 1,
       help="준비 구간 다음, 지표를 모으는 기간. 길수록 결과가 안정적이지만 이 모델에선 시드 수를 늘리는 쪽이 더 효과적이다."),
    _p("followup_days", "후속 관찰", "eval", 3, "일", 0, 30, 1,
       help="집계 구간이 끝난 뒤에도 요청은 계속 들어오고, 집계 구간에 접수된 건이 끝까지 처리되는지 지켜보는 기간."),
]

DEFAULTS = {p["key"]: p["default"] for p in PARAMS}
BY_KEY = {p["key"]: p for p in PARAMS}


def _num(v, key, p=None):
    p = p or BY_KEY[key]
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError("%s: 숫자가 아닙니다 (%r)" % (p["label"], v))
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError("%s: 올바른 숫자가 아닙니다" % p["label"])
    if "min" in p and x < p["min"]:
        raise ValueError("%s: %s 이상이어야 합니다" % (p["label"], p["min"]))
    if "max" in p and x > p["max"]:
        raise ValueError("%s: %s 이하여야 합니다" % (p["label"], p["max"]))
    return x


def _hourly(v, key):
    """시간대별 24개 값을 검사해 {시각: 값} 으로."""
    p = BY_KEY[key]
    if not isinstance(v, (list, tuple)) or len(v) != 24:
        raise ValueError("%s: 0~23시 24개 값이 필요합니다" % p["label"])
    out = {}
    for h, x in enumerate(v):
        if p["kind"] == "bool":
            out[h] = bool(x)
        elif p["kind"] == "choice":
            if x not in p["options"]:
                raise ValueError("%s %d시: 알 수 없는 값 %r" % (p["label"], h, x))
            out[h] = x
        else:
            out[h] = _num(x, key, dict(p, label="%s %d시" % (p["label"], h)))
    return out


def _rows(v, key):
    """자리(행) 목록을 검사해 [ {시각: 값}, ... ] 으로."""
    p = BY_KEY[key]
    if not isinstance(v, (list, tuple)) or not v or not all(isinstance(r, (list, tuple)) for r in v):
        raise ValueError("%s: 자리 목록이 필요합니다" % p["label"])
    if not p["min_rows"] <= len(v) <= p["max_rows"]:
        raise ValueError("%s: 자리는 %d~%d개여야 합니다 (현재 %d개)" % (p["label"], p["min_rows"], p["max_rows"], len(v)))
    return [_hourly(r, key) for r in v]


def _cost_items(v):
    """비용·수입 항목 목록을 검사해 정규화한다 (체크된 항목만 계산에 쓰이지만 형식은 전부 검사)."""
    if not isinstance(v, (list, tuple)) or len(v) > 40:
        raise ValueError("비용·수입 항목: 최대 40개의 목록이 필요합니다")
    out, seen = [], set()
    for i, it in enumerate(v, 1):
        if not isinstance(it, dict):
            raise ValueError("비용·수입 항목 %d: 형식이 올바르지 않습니다" % i)
        name = str(it.get("name", "")).strip()[:40]
        if not name:
            raise ValueError("비용·수입 항목 %d: 이름이 필요합니다" % i)
        typ = it.get("type")
        if typ not in ("지출", "수입"):
            raise ValueError("%s: 구분은 지출 또는 수입이어야 합니다" % name)
        b = COST_BY_BASIS.get(it.get("basis"))
        if not b:
            raise ValueError("%s: 알 수 없는 대상 %r" % (name, it.get("basis")))
        period = it.get("period")
        if period not in b["periods"]:
            raise ValueError("%s: 단가 기간은 %s 중 하나여야 합니다" % (name, "/".join(b["periods"])))
        try:
            amt = float(it.get("amount"))
        except (TypeError, ValueError):
            raise ValueError("%s: 금액이 숫자가 아닙니다" % name)
        if not 0 <= amt <= 1e13 or amt != amt:
            raise ValueError("%s: 금액은 0 이상이어야 합니다" % name)
        d = {"id": str(it.get("id") or "x%d" % i), "name": name, "type": typ, "enabled": bool(it.get("enabled", True)),
             "basis": b["id"], "period": period, "amount": amt}
        if d["id"] in seen:
            d["id"] += "_%d" % i
        seen.add(d["id"])
        for f, opts in COST_FILTERS.items():
            x = it.get(f, "전체")
            if x not in opts:
                raise ValueError("%s: 조건 %s 값이 올바르지 않습니다" % (name, f))
            d[f] = x if f in b["filters"] else "전체"       # 해당 대상에 쓰지 않는 조건은 무시
        out.append(d)
    return out


def apply_params(values):
    """UI 값(dict)을 Config 로 바꾼다. 빠진 값은 기본값. 잘못된 값은 ValueError."""
    v = dict(DEFAULTS)
    for k, x in (values or {}).items():
        if k in BY_KEY:
            v[k] = x
    n = {k: _num(v[k], k) for k, p in BY_KEY.items() if p["type"] == "number"}
    hr = {k: _hourly(v[k], k) for k, p in BY_KEY.items() if p["type"] == "hourly"}
    rw = {k: _rows(v[k], k) for k, p in BY_KEY.items() if p["type"] == "rows"}
    cfg = Config()

    m = n["demand_mult"]
    cfg.demand.rate_per_hour = {
        CT: {h: r * m for h, r in hr["ct_rate"].items()},
        MRI: {h: r * m for h, r in hr["mri_rate"].items()},
    }
    cfg.demand.urgent_ratio = {CT: n["ct_urgent"] / 100.0, MRI: n["mri_urgent"] / 100.0}
    cfg.demand.daily_var = {CT: n["ct_daily_var"] * m, MRI: n["mri_daily_var"] * m}   # 배율을 곱한 도착률 기준
    cfg.demand.weekday_mult = [n["wd_mult_%d" % i] for i in range(7)]
    if v["start_weekday"] not in WEEKDAYS:
        raise ValueError("시작 요일: 알 수 없는 값 %r" % (v["start_weekday"],))
    cfg.demand.start_weekday = WEEKDAYS.index(v["start_weekday"])
    cfg.clock.clock_at_t0 = int(n["start_hour"]) * 60
    cfg.demand.patient_mix = {"ED": n["mix_ed"], "IP": n["mix_ip"], "OP": n["mix_op"]}
    if sum(cfg.demand.patient_mix.values()) <= 0:
        raise ValueError("환자군 가중치: 하나 이상은 0보다 커야 합니다")
    if v["prelim_target"] not in _PT_CODE:
        raise ValueError("가판독 대상: 알 수 없는 값 %r" % (v["prelim_target"],))
    cfg.demand.prelim_target = _PT_CODE[v["prelim_target"]]
    cfg.cost.base_ct, cfg.cost.base_mri = int(n["base_ct"]), int(n["base_mri"])
    cfg.cost.items = [d for d in _cost_items(v["cost_items"]) if d["enabled"]]

    s = cfg.scan
    s.ct_units, s.mri_units = int(n["ct_units"]), int(n["mri_units"])
    on = lambda rows: {h: sum(1 for r in rows if r[h]) for h in HOURS}   # 시각별 켜진 자리 수
    s.ct_slots, s.mri_slots, s.safety_slots = on(rw["ct_slots"]), on(rw["mri_slots"]), on(rw["safety_slots"])
    s.safety_units = max(1, max(s.safety_slots.values()))   # 슬롯이 1 이하면 담당 1자리

    assign = {h: {} for h in HOURS}
    for i, row in enumerate(rw["final"], 1):
        for h, val in row.items():
            if val != "off":
                assign[h]["FinalReader_%d" % i] = {CT, MRI} if val == "CT+MRI" else {val}
    cfg.staffing.final_assignment = assign
    cfg.staffing.prelim_assignment = {h: {"PreliminaryReader_%d" % i for i, row in enumerate(rw["prelim"], 1) if row[h]}
                                      for h in HOURS}

    def tri(key):
        t = (n[key + "_min"], n[key + "_mode"], n[key + "_max"])
        if not t[0] <= t[1] <= t[2] or t[2] <= 0:
            raise ValueError("%s: 최소 ≤ 최빈 ≤ 최대 이고 최대 > 0 이어야 합니다" % BY_KEY[key + "_min"]["grid"]["row"])
        return t
    s.ct_scan, s.mri_safety, s.mri_scan = tri("ct_scan"), tri("mri_safety"), tri("mri_scan")
    cfg.reading.prelim = {CT: tri("prelim_ct"), MRI: tri("prelim_mri")}
    cfg.reading.final = {CT: tri("final_ct"), MRI: tri("final_mri")}

    e = cfg.evaluation
    e.warmup_end = int(n["warmup_days"] * 1440)
    e.measure_end = e.warmup_end + int(n["measure_days"] * 1440)
    e.total_end = e.measure_end + int(n["followup_days"] * 1440)
    return cfg


_base_ct, _base_mri = DEFAULTS["ct_rate"], DEFAULTS["mri_rate"]
_PEAK_CT = _curve(sum(_base_ct))
_PEAK_MRI = _curve(sum(_base_mri))

# 기본 프리셋: Notion 명세(⑧ 8.2)에 직접 적힌 것만 둔다. 나머지 조건은 팀이 '내 프리셋'으로 저장한다.
PRESETS = [
    {"id": "baseline", "name": "Baseline: 명세 기본값 (수요 100%)",
     "desc": "명세 ⑧ 기준값. CT 4·MRI 3대, 하루 CT 110·MRI 45건, 판독 08~16시 3명·16~24시 2명·00~08시 1명, 7일 준비 + 28일 집계 + 3일 후속", "values": {}},
    {"id": "d80", "name": "수요 80%", "desc": "기준 수요의 80% (명세 8.2 비교 조건)", "values": {"demand_mult": 0.8}},
    {"id": "d120", "name": "수요 120%", "desc": "기준 수요의 120% (명세 8.2 비교 조건)", "values": {"demand_mult": 1.2}},
    {"id": "d150", "name": "수요 150%", "desc": "기준 수요의 150% (명세 8.2 비교 조건)", "values": {"demand_mult": 1.5}},
]


def schema():
    return {"groups": [{"id": g, "label": l, "help": d} for g, l, d in GROUPS],
            "params": PARAMS, "presets": PRESETS, "readerOptions": READER_OPTIONS,
            "costDef": {"bases": COST_BASES, "filters": COST_FILTERS, "periodDays": {"일": 1, "주": 7, "월": 30, "연": 365}}}
