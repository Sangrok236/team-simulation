"""UI 에 노출할 실험 변수 정의 + Config 적용 + 프리셋.

변수를 추가하려면:
  1) PARAMS 에 _p(...) (한 값) 또는 _h(...) (0~23시 시간대별 24개 값) 한 줄 추가
  2) apply_params() 에서 Config 로 연결.
화면은 /api/schema 로 이 정의를 읽어 자동으로 만들어진다. 모든 변수·그룹에는 help(? 설명)를 단다.
"""
from config import Config, CT, MRI, HOURS, by_hours

MAX_FINAL, MAX_PRELIM = 8, 4
READER_OPTIONS = ["off", "CT", "MRI", "CT+MRI"]

GROUPS = [
    ("demand", "수요", "검사 요청이 얼마나 들어오는지. 총량은 아래 '전체 수요 배율'과 응급 비율로, 시간대별 모양은 '24시간 편집기 → 수요' 탭에서 정한다. 도착은 포아송(무작위) 과정이고, 같은 시드면 같은 요청이 같은 시각에 들어온다."),
    ("scan", "촬영 자원", "CT·MRI 장비 대수. 실제로 동시에 촬영하는 수는 min(장비 수, 그 시각에 켜져 있는 촬영 슬롯 수) 이다. 슬롯은 촬영을 맡을 수 있는 인력/배정 한 자리로, '24시간 편집기 → 촬영 슬롯' 탭에서 자리를 추가·삭제하고 시간대별로 켠다."),
    ("staff", "판독 근무표", "가판독 자리(응급 요청의 초기 소견)와 최종판독 자리(모든 요청의 최종 보고)를 '24시간 편집기 → 판독 근무표' 탭에서 추가·삭제하고, 자리마다 몇 시에 무엇을 읽는지 칠해서 정한다. off = 그 시각엔 근무하지 않음. 시작값은 가판독 1자리, 최종판독 1자리다."),
    ("times", "처리시간 (분, 삼각분포)", "각 단계가 걸리는 시간. 삼각분포로 무작위 추출하며 '최소 / 가장 흔한 값(최빈) / 최대' 세 숫자로 정한다. 평균은 (최소+최빈+최대)÷3 이다."),
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


PARAMS = [
    # 수요
    _p("demand_mult", "전체 수요 배율", "demand", 1.0, "×", 0.2, 3.0, 0.05,
       help="아래 24시간 수요 곡선(CT·MRI)에 일괄로 곱한다. 1.2 = 모든 시간대 수요 20% 증가. 곡선 모양은 그대로 두고 총량만 바꿀 때 쓴다."),
    _p("ct_urgent", "CT 응급 비율", "demand", 20, "%", 0, 100, 1,
       help="CT 요청 중 응급인 비율. 응급은 대기열에서 일반보다 먼저 처리되고, 최종판독 전에 가판독 단계를 거친다."),
    _p("mri_urgent", "MRI 응급 비율", "demand", 10, "%", 0, 100, 1,
       help="MRI 요청 중 응급인 비율. 응급은 촬영 대기열 우선 + 가판독 경로를 탄다."),
    # 촬영 자원
    _p("ct_units", "CT 장비 수", "scan", 2, "대", 1, 8, 1,
       help="CT 장비 대수. 실제 동시 촬영 수 = min(장비 수, 그 시각의 CT 슬롯). 장비만 늘리고 슬롯을 그대로 두면 효과가 없을 수 있다."),
    _p("mri_units", "MRI 장비 수", "scan", 1, "대", 1, 6, 1,
       help="MRI 장비 대수. 실제 동시 촬영 수 = min(장비 수, 그 시각의 MRI 슬롯)."),
    # 시간대별 (24시간 편집기)
    _h("ct_rate", "CT 도착률", "demand", _day(7.0, 2.0), "number", "건/시간", 0, 30, 0.1, short="CT 도착률",
       help="그 시각에 CT 검사 요청이 들어오는 평균 속도(건/시간). 기본값은 주간(08~20시) 7건, 야간 2건. 전체 수요 배율이 곱해진다. 편집기에서 같은 값으로 칠한 칸을 다시 클릭하면 0(요청 없음)이 된다."),
    _h("mri_rate", "MRI 도착률", "demand", _day(1.2, 0.3), "number", "건/시간", 0, 10, 0.1, short="MRI 도착률",
       help="그 시각에 MRI 검사 요청이 들어오는 평균 속도(건/시간). 기본값은 주간 1.2건, 야간 0.3건."),
    _r("ct_slots", "CT 촬영 슬롯", "scan", _rows_by_count(_day(2, 1)), "bool", min_rows=1, max_rows=8, short="CT 슬롯",
       row_label="CT 슬롯",
       help="CT 촬영에 투입된 동시 촬영 자리(촬영 인력)들. 자리를 추가·삭제하고, 칸을 클릭하면 그 시각에 켜지거나 꺼진다. 그 시각에 켜진 자리 수가 CT 슬롯 수이고, 실제 동시 촬영 = min(CT 장비 수, 슬롯 수). 모두 꺼진 시각에는 새 촬영을 시작하지 못한다(진행 중인 건은 끝까지)."),
    _r("mri_slots", "MRI 촬영 슬롯", "scan", _rows_by_count(_day(1, 1)), "bool", min_rows=1, max_rows=6, short="MRI 슬롯",
       row_label="MRI 슬롯",
       help="MRI 동시 촬영 자리들. 켜진 자리 수 = 그 시각의 MRI 슬롯 수이고, 실제 동시 촬영 = min(MRI 장비 수, 슬롯 수)."),
    _r("safety_slots", "MRI 안전확인 슬롯", "scan", _rows_by_count(_day(1, 1)), "bool", min_rows=1, max_rows=4, short="안전확인 슬롯",
       row_label="안전확인",
       help="MRI 촬영 전 금속·이식물 등 안전확인을 맡는 자리들. 켜진 자리가 하나도 없는 시각에는 새 안전확인을 시작할 수 없어 MRI가 밀린다."),
    _r("prelim", "가판독 자리", "staff", [[True] * 24], "bool", min_rows=1, max_rows=MAX_PRELIM, short="가판독 자리",
       row_label="가판독",
       help="응급 요청의 가판독(초기 소견)을 맡는 자리들. 시작값은 1자리. 자리를 추가하면 응급이 몰릴 때 동시에 여러 건을 가판독할 수 있다. 칸을 클릭하면 그 시각의 근무가 켜지거나 꺼진다. 모든 자리가 꺼진 시각에는 가판독이 시작되지 않고 대기한다."),
    _r("final", "최종판독 자리", "staff", [["CT+MRI"] * 24], "choice", options=READER_OPTIONS, min_rows=1, max_rows=MAX_FINAL,
       short="최종판독 자리", row_label="최종판독",
       help="최종판독(모든 요청의 마지막 단계)을 맡는 자리들. 시작값은 1자리(24시간 CT+MRI). 자리를 추가·삭제하고, 칸마다 그 시각에 읽는 검사 종류를 칠한다: off=근무 안 함, CT / MRI / CT+MRI. '붓'과 같은 값인 칸을 다시 클릭하면 off 가 된다. 시각이 바뀌어도 읽던 건은 끝까지 읽고, 근무가 끝난 뒤 이어서 일하면 초과근무로 집계된다."),
]

# 처리시간 (삼각분포)
_TIMES = [
    ("ct_scan", "CT 촬영(장비 점유)", (10, 15, 20), "CT 한 건을 찍는 동안 장비 한 자리를 차지하는 시간."),
    ("mri_safety", "MRI 안전확인", (3, 5, 7), "MRI 촬영 전 안전확인에 드는 시간. 촬영 장비는 아직 쓰지 않고 안전확인 자리만 차지한다."),
    ("mri_scan", "MRI 촬영(장비 점유)", (30, 40, 50), "MRI 한 건을 찍는 동안 장비 한 자리를 차지하는 시간."),
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
# 평가
PARAMS += [
    _p("warmup_days", "준비 구간 (집계 제외)", "eval", 7, "일", 0, 30, 1,
       help="병원이 빈 상태로 시작하는 영향을 없애기 위해 처음 며칠은 집계에서 뺀다. t=0 은 첫날 08:00."),
    _p("measure_days", "집계 구간", "eval", 7, "일", 1, 28, 1,
       help="준비 구간 다음, 지표를 모으는 기간. 길수록 결과가 안정적이지만 이 모델에선 시드 수를 늘리는 쪽이 더 효과적이다."),
    _p("followup_days", "후속 관찰", "eval", 2, "일", 0, 7, 1,
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


# 하루 수요 모양 (상대 가중치, 0~23시). 외래 진료 시간대에 몰리는 형태의 '예시'일 뿐이다.
_SHAPE = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 2.0, 6.0, 10.0, 11.0, 10.0,
          7.0, 9.0, 10.0, 9.0, 7.0, 5.0, 3.0, 2.5, 1.5, 1.5, 1.2, 1.0]


def _curve(total_per_day):
    s = sum(_SHAPE)
    return [round(total_per_day * w / s, 1) for w in _SHAPE]


_base_ct, _base_mri = DEFAULTS["ct_rate"], DEFAULTS["mri_rate"]
_PEAK_CT = _curve(sum(_base_ct))
_PEAK_MRI = _curve(sum(_base_mri))

# 정현 명세의 판독 근무표 (최종판독 3자리). 시작값은 1자리라서 명세 기준 프리셋들은 이 표를 함께 적용한다.
_SPEC_FINAL = [_day("CT", "CT+MRI"), _day("MRI", "off"), _day("CT", "off")]
_SPEC = {"final": _SPEC_FINAL}
_R3_LATE = [_SPEC_FINAL[0], _SPEC_FINAL[1], put(put(_SPEC_FINAL[2], 8, 20, "off"), 11, 23, "CT")]


def _spec(**kw):
    d = dict(_SPEC)
    d.update(kw)
    return d


# 예시 프리셋 (팀이 자유롭게 고치면 된다). 값은 시작값 대비 바꿀 것만 적는다.
PRESETS = [
    {"id": "start", "name": "시작값 (판독 1자리)", "desc": "가판독 1자리, 최종판독 1자리(24시간 CT+MRI)로 시작. 판독이 모자라 대기가 크게 쌓이는 상태", "values": {}},
    {"id": "baseline", "name": "Baseline: 정현 명세 (최종판독 3자리)", "desc": "정현 명세 기준값 — 판독 1은 24시간, 판독 2·3은 주간만",
     "values": _spec()},
    {"id": "a1_equip_only", "name": "A1′ CT 장비만 증설", "desc": "장비 3대, 촬영 슬롯은 그대로 → 인력이 막으면 효과가 없는지 확인",
     "values": _spec(ct_units=3)},
    {"id": "a1_full", "name": "A1 CT 용량 확대", "desc": "장비 3대 + 촬영 슬롯 주간 3 / 야간 2",
     "values": _spec(ct_units=3, ct_slots=_rows_by_count(_day(3, 2)))},
    {"id": "a2_mri", "name": "A2 MRI 용량 확대", "desc": "MRI 2대 + 주간 슬롯 2",
     "values": _spec(mri_units=2, mri_slots=_rows_by_count(_day(2, 1)))},
    {"id": "b1_move", "name": "B1 판독 1자리 주간→야간 (총 근무시간 유지)", "desc": "판독 3을 주간에서 야간(CT+MRI)으로 이동",
     "values": _spec(final=[_SPEC_FINAL[0], _SPEC_FINAL[1], _day("off", "CT+MRI")])},
    {"id": "b2_add_night", "name": "B2 야간 판독 1자리 추가", "desc": "판독 2를 야간에도 CT+MRI로 근무 (총 근무시간 +12h/일)",
     "values": _spec(final=[_SPEC_FINAL[0], _day("MRI", "CT+MRI"), _SPEC_FINAL[2]])},
    {"id": "s1_concentrate", "name": "S1 수요 주간 집중 (총량 유지)", "desc": "야간 수요를 주간으로 몰아 하루 총 요청 수는 같게",
     "values": _spec(ct_rate=_day(9.0, 0.0), mri_rate=_day(1.5, 0.0))},
    {"id": "s3_high", "name": "S3 높은 수요 ×1.2", "desc": "모든 도착률 1.2배", "values": _spec(demand_mult=1.2)},
    {"id": "h1_peak", "name": "H1 외래 피크형 수요 곡선 (하루 총량 기준과 같음)",
     "desc": "오전 9~12시, 오후 13~16시에 요청이 몰리는 곡선. 하루 총량은 기준과 같고 시간대 모양만 다름",
     "values": _spec(ct_rate=_PEAK_CT, mri_rate=_PEAK_MRI)},
    {"id": "h2_peak_cap", "name": "H2 피크형 수요 + 피크 시간만 CT 슬롯 3",
     "desc": "H1 수요 곡선에서 9~17시에만 CT 장비 3대·슬롯 3으로 운영 (용량을 필요한 시간에만 투입)",
     "values": _spec(ct_rate=_PEAK_CT, mri_rate=_PEAK_MRI, ct_units=3,
                     ct_slots=_rows_by_count(put(_day(2, 1), 9, 17, 3)))},
    {"id": "h3_late_reader", "name": "H3 판독 3 근무를 11~23시로 늦춤 (총 근무시간 유지)",
     "desc": "판독 3의 CT 근무를 08~20시에서 11~23시로 이동 (기준 수요 곡선)",
     "values": _spec(final=_R3_LATE)},
    {"id": "h4_peak_late", "name": "H4 피크형 수요 + 판독 3 근무 늦춤",
     "desc": "H1 수요 곡선에서 판독 3을 11~23시로 이동 → 수요 모양에 근무표를 맞추면 판독 대기가 줄어드는지",
     "values": _spec(ct_rate=_PEAK_CT, mri_rate=_PEAK_MRI, final=_R3_LATE)},
]


def schema():
    return {"groups": [{"id": g, "label": l, "help": d} for g, l, d in GROUPS],
            "params": PARAMS, "presets": PRESETS, "readerOptions": READER_OPTIONS}
