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


def _next_rate_change(cfg, modality, t):
    """t 이후 도착률이 달라지는 첫 정시. 같은 률이 이어지는 시각은 건너뛴다.
    (률이 같으면 재시작하지 않아 표본 경로가 시간대 수와 무관하다.)"""
    table = cfg.demand.rate_per_hour[modality]
    cur = table[cfg.clock.hour_at(t)]
    b = cfg.clock.next_change(t)
    while b < cfg.evaluation.total_end and table[cfg.clock.hour_at(b)] == cur:
        b = cfg.clock.next_change(b)
    return b


def _arrival_times(cfg, modality, rng):
    """시간대별 비례율을 갖는 포아송 과정 (률이 바뀌는 순간에 재시작; 지수분포의 무기억성 이용)."""
    horizon = cfg.evaluation.total_end
    times = []
    t = 0.0
    while t < horizon:
        boundary = _next_rate_change(cfg, modality, t)
        rate = cfg.demand.rate_per_hour[modality][cfg.clock.hour_at(t)] / 60.0
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
            raw.append({
                "modality": modality,
                "urgent": urgent,
                "urgency": "URGENT" if urgent else "ROUTINE",
                "request_time": t,
                "safety_time": safety if modality == MRI else None,
                "scan_time": scan,
                "prelim_time": prelim if urgent else None,
                "final_time": final,
            })
    raw.sort(key=lambda c: c["request_time"])
    for i, c in enumerate(raw):
        c["case_id"] = "CASE_%05d" % (i + 1)
    return raw
