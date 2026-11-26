"""이벤트 형식, 우선순위 키, 검사 요청(ExamCase) 생성.

이벤트 메시지 공통 필드: event_type, timestamp(분), case_id, resource_id, modality.
검사 요청의 서비스시간은 '요청 생성 시 한 번만' 추출해 저장한다.
같은 시드와 같은 수요 설정이면 정책이 달라도 같은 요청·같은 서비스시간을 쓴다.
"""
import random

from config import CT, MRI


def evt(event_type, timestamp, **fields):
    e = {"event_type": event_type, "timestamp": timestamp}
    e.update(fields)
    return e


def priority_key(case, ready_time):
    """응급 우선 -> 단계 준비 시각이 빠른 순 -> 요청 ID 순."""
    return (0 if case["urgent"] else 1, ready_time, case["case_id"])


def tri(rng, params):
    lo, mode, hi = params
    return rng.triangular(lo, hi, mode)


def _day_offsets(cfg, modality):
    """날짜별 도착률 가감값(건/시간). 변동 0 이면 전부 0 이고 난수도 쓰지 않는다."""
    n = int((cfg.evaluation.total_end + cfg.clock.clock_at_t0) // 1440) + 2
    v = cfg.demand.daily_var.get(modality, 0.0)
    if v <= 0:
        return [0.0] * n
    rng = random.Random("%s:daily:%s" % (cfg.evaluation.seed, modality))   # 도착·서비스시간 난수와 분리
    return [rng.uniform(-v, v) for _ in range(n)]


def _rate_at(cfg, modality, t, offs):
    """시각 t 의 도착률(건/시간) = max(0, 시간대 설정값 + 그날 가감값) x 요일 배수."""
    d = cfg.clock.day_index(t)
    base = cfg.demand.rate_per_hour[modality][cfg.clock.hour_at(t)]
    w = cfg.demand.weekday_mult[(cfg.demand.start_weekday + d) % 7]
    return max(0.0, base + offs[d]) * w


def _next_rate_change(cfg, modality, t, offs):
    """t 이후 도착률이 달라지는 첫 정시. 같은 률이 이어지는 시각은 건너뛴다.
    (률이 같으면 재시작하지 않아 표본 경로가 시간대·날짜 수와 무관하다.)"""
    cur = _rate_at(cfg, modality, t, offs)
    b = cfg.clock.next_change(t)
    while b < cfg.evaluation.total_end and _rate_at(cfg, modality, b, offs) == cur:
        b = cfg.clock.next_change(b)
    return b


def _arrival_times(cfg, modality, rng):
    """시간대별 비례율을 갖는 포아송 과정 (률이 바뀌는 순간에 재시작; 지수분포의 무기억성 이용)."""
    horizon = cfg.evaluation.total_end
    times = []
    offs = _day_offsets(cfg, modality)
    t = 0.0
    while t < horizon:
        boundary = _next_rate_change(cfg, modality, t, offs)
        rate = _rate_at(cfg, modality, t, offs) / 60.0
        if rate <= 0:
            t = boundary
            continue
        nxt = t + rng.expovariate(rate)
        if nxt < boundary:
            if nxt < horizon:
                times.append(nxt)
            t = nxt
        else:
            t = boundary
    return times


def _pick_group(cfg, rng, t):
    """환자군(ED/IP/OP). 요청 시각의 시간대·요일에 따라 가중치가 달라진다."""
    h = cfg.clock.hour_at(t)
    wd = (cfg.demand.start_weekday + cfg.clock.day_index(t)) % 7
    mix = cfg.demand.patient_mix
    w = {"ED": mix["ED"], "IP": mix["IP"] * (1.0 if 8 <= h < 20 else 0.5),
         "OP": mix["OP"] if (wd < 5 and 8 <= h < 17) else 0.0}
    x = rng.random() * sum(w.values())
    for g in ("ED", "IP", "OP"):
        x -= w[g]
        if x < 0:
            return g
    return "ED"


def _has_prelim(cfg, urgent, group):
    tg = cfg.demand.prelim_target
    return urgent and (tg == "ALL" or (tg == "ED" and group == "ED") or (tg == "ED_IP" and group in ("ED", "IP")))


def build_cases(cfg):
    """모든 검사 요청을 도착 시각순으로 만들어 돌려준다."""
    seed = cfg.evaluation.seed
    raw = []
    for modality in (CT, MRI):
        arr_rng = random.Random("%s:arrival:%s" % (seed, modality))
        for k, t in enumerate(_arrival_times(cfg, modality, arr_rng)):
            rng = random.Random("%s:case:%s:%d" % (seed, modality, k))
            urgent = rng.random() < cfg.demand.urgent_ratio[modality]
            # 항상 같은 순서로 모두 추출 -> 정책/설정이 바뀌어도 표본이 같다
            safety = tri(rng, cfg.scan.mri_safety)
            scan = tri(rng, cfg.scan.ct_scan if modality == CT else cfg.scan.mri_scan)
            prelim = tri(rng, cfg.reading.prelim[modality])
            final = tri(rng, cfg.reading.final[modality])
            group = _pick_group(cfg, rng, t)          # 추가 추출은 맨 뒤 -> 앞선 표본은 그대로
            has_prelim = _has_prelim(cfg, urgent, group)
            raw.append({
                "modality": modality,
                "urgent": urgent,
                "urgency": "URGENT" if urgent else "ROUTINE",
                "patient_group": group,
                "has_prelim": has_prelim,
                "request_time": t,
                "safety_time": safety if modality == MRI else None,
                "scan_time": scan,
                "prelim_time": prelim if has_prelim else None,
                "final_time": final,
            })
    raw.sort(key=lambda c: c["request_time"])
    for i, c in enumerate(raw):
        c["case_id"] = "CASE_%05d" % (i + 1)
    return raw
