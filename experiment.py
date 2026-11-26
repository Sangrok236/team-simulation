"""한 번의 실험 실행(값 + 시드 -> KPI + 시계열). UI 서버와 스크립트가 공용으로 쓴다."""
import math
import time

from analysis import analyze, check_model
from params import apply_params
from system import run
from config import CT, MRI

# (key, label, unit, group, better, decimals)   better: lower / higher / None(참고)
KPI_DEFS = [
    ("total_mean", "전체 소요 평균 (요청→최종보고)", "분", "핵심", "lower", 1),
    ("total_p95", "전체 소요 P95", "분", "핵심", "lower", 1),
    ("scan_wait_ct", "CT 촬영 대기 평균", "분", "핵심", "lower", 1),
    ("scan_wait_mri", "MRI 촬영 대기 평균", "분", "핵심", "lower", 1),
    ("final_wait_mean", "최종판독 대기 평균", "분", "핵심", "lower", 1),
    ("final_wait_p95", "최종판독 대기 P95", "분", "핵심", "lower", 1),
    ("prelim_mean", "응급 초기보고 평균 (촬영→가판독)", "분", "핵심", "lower", 1),
    ("prelim_p95", "응급 초기보고 P95", "분", "핵심", "lower", 1),
    ("post_scan_mean", "촬영 후 최종보고 평균", "분", "핵심", "lower", 1),
    ("post_scan_p95", "촬영 후 최종보고 P95", "분", "핵심", "lower", 1),
    ("within24", "경과 24시간 이내 완료율", "%", "핵심", "higher", 1),
    ("nextday", "촬영일 익일까지 완료율", "%", "핵심", "higher", 1),
    ("unfinished", "미완료 (촬영 완료 기준)", "건", "핵심", "lower", 1),
    ("requests", "집계 구간 접수 건수", "건", "참고", None, 0),
    ("total_mean_ct_urgent", "전체 소요 평균 · CT 응급", "분", "그룹별", "lower", 1),
    ("total_mean_ct_routine", "전체 소요 평균 · CT 일반", "분", "그룹별", "lower", 1),
    ("total_mean_mri_urgent", "전체 소요 평균 · MRI 응급", "분", "그룹별", "lower", 1),
    ("total_mean_mri_routine", "전체 소요 평균 · MRI 일반", "분", "그룹별", "lower", 1),
    ("util_ct", "CT 촬영 슬롯 점유율", "%", "자원", None, 1),
    ("util_mri", "MRI 촬영 슬롯 점유율", "%", "자원", None, 1),
    ("util_safety", "MRI 안전확인 점유율", "%", "자원", None, 1),
    ("util_final_1", "판독 1 점유율 (예정근무 대비)", "%", "자원", None, 1),
    ("util_final_2", "판독 2 점유율 (예정근무 대비)", "%", "자원", None, 1),
    ("util_final_3", "판독 3 점유율 (예정근무 대비)", "%", "자원", None, 1),
    ("util_final_4", "판독 4 점유율 (예정근무 대비)", "%", "자원", None, 1),
    ("util_final_5", "판독 5 점유율 (예정근무 대비)", "%", "자원", None, 1),
    ("util_final_6", "판독 6 점유율 (예정근무 대비)", "%", "자원", None, 1),
    ("util_final_7", "판독 7 점유율 (예정근무 대비)", "%", "자원", None, 1),
    ("util_final_8", "판독 8 점유율 (예정근무 대비)", "%", "자원", None, 1),
    ("overtime", "초과근무 합계", "분", "자원", "lower", 0),
    ("q_ct_mean", "CT 대기열 시간평균", "건", "대기열", "lower", 2),
    ("q_ct_max", "CT 대기열 최대", "건", "대기열", "lower", 0),
    ("q_mri_mean", "MRI 대기열 시간평균", "건", "대기열", "lower", 2),
    ("q_mri_max", "MRI 대기열 최대", "건", "대기열", "lower", 0),
    ("q_final_mean", "최종판독 대기열 시간평균", "건", "대기열", "lower", 2),
    ("q_final_max", "최종판독 대기열 최대", "건", "대기열", "lower", 0),
    ("q_prelim_mean", "가판독 대기열 시간평균", "건", "대기열", "lower", 2),
    ("q_prelim_max", "가판독 대기열 최대", "건", "대기열", "lower", 0),
]

QUEUES = ["CT_QUEUE", "MRI_QUEUE", "FINAL_WORKLIST", "PRELIM_WORKLIST", "MRI_SAFETY_QUEUE"]


KPI_HELP = {
    "total_mean": "환자 요청이 들어온 순간부터 최종보고서가 나올 때까지 걸린 시간의 평균. 이 시뮬레이션의 대표 성적표.",
    "total_p95": "전체 소요시간을 짧은 순으로 줄 세웠을 때 95% 지점. '가장 늦은 5%는 적어도 이 정도 걸린다'는 뜻으로, 평균이 가리는 나쁜 경우를 보여준다.",
    "scan_wait_ct": "CT 요청이 접수된 뒤 촬영이 시작될 때까지 줄 서서 기다린 시간의 평균.",
    "scan_wait_mri": "MRI 요청이 접수된 뒤(안전확인 포함) 촬영이 시작될 때까지 기다린 시간의 평균.",
    "final_wait_mean": "촬영이 끝난 뒤 최종판독이 시작될 때까지 판독 대기열에서 기다린 시간의 평균.",
    "final_wait_p95": "최종판독 대기시간의 95% 지점 (가장 오래 기다리는 5%의 경계).",
    "prelim_mean": "응급 요청만 해당. 촬영이 끝난 뒤 가판독(초기 소견)이 나올 때까지 걸린 시간의 평균.",
    "prelim_p95": "응급 초기보고 시간의 95% 지점.",
    "post_scan_mean": "촬영이 끝난 뒤 최종보고서가 나올 때까지 (판독 대기 + 판독 + 가판독 영향 포함)의 평균.",
    "post_scan_p95": "촬영 후 최종보고까지 시간의 95% 지점.",
    "within24": "요청 접수 후 24시간 안에 최종보고가 끝난 비율. 높을수록 좋다.",
    "nextday": "촬영한 날의 다음 날 자정까지 최종보고가 끝난 비율. 높을수록 좋다.",
    "unfinished": "집계 구간에 촬영을 마쳤지만 후속 관찰이 끝날 때까지도 최종보고가 안 나온 건수. 0이 아니면 판독이 계속 밀리고 있다는 신호.",
    "requests": "집계 구간에 접수된 검사 요청 수. 시드가 같으면 정책이 달라도 같은 값이어야 한다.",
    "total_mean_ct_urgent": "CT 응급 요청만의 전체 소요 평균.",
    "total_mean_ct_routine": "CT 일반 요청만의 전체 소요 평균.",
    "total_mean_mri_urgent": "MRI 응급 요청만의 전체 소요 평균.",
    "total_mean_mri_routine": "MRI 일반 요청만의 전체 소요 평균.",
    "util_ct": "집계 구간에 CT 촬영 슬롯 용량 중 실제로 촬영한 시간의 비율. 100%에 가까우면 슬롯이 병목이다.",
    "util_mri": "MRI 촬영 슬롯 용량 중 실제 촬영 시간의 비율.",
    "util_safety": "MRI 안전확인 자리가 일한 시간의 비율.",
    "util_final_1": "판독 1이 예정된 근무시간 중 실제로 판독한 시간의 비율. 근무 시간 밖(초과근무)은 제외하고 센다.",
    "util_final_2": "판독 2의 점유율 (예정근무 대비).",
    "util_final_3": "판독 3의 점유율 (예정근무 대비).",
    "util_final_4": "판독 4의 점유율 (예정근무 대비). 근무가 없으면 비어 있다.",
    "util_final_5": "판독 5의 점유율 (예정근무 대비). 근무가 없으면 비어 있다.",
    "util_final_6": "판독 6의 점유율 (예정근무 대비). 근무가 없으면 비어 있다.",
    "util_final_7": "판독 7의 점유율 (예정근무 대비). 자리가 없으면 비어 있다.",
    "util_final_8": "판독 8의 점유율 (예정근무 대비). 자리가 없으면 비어 있다.",
    "overtime": "근무표 시간이 끝났는데도 읽던 건을 마무리하느라 더 일한 시간의 합 (모든 판독 자리, 집계 구간).",
    "q_ct_mean": "CT 촬영 대기열에 평균 몇 건이 줄 서 있었는지 (시간 가중 평균).",
    "q_ct_max": "집계 구간 중 CT 촬영 대기열이 가장 길었을 때의 건수.",
    "q_mri_mean": "MRI 촬영 대기열의 시간 가중 평균 건수.",
    "q_mri_max": "MRI 촬영 대기열의 최대 건수.",
    "q_final_mean": "최종판독 대기열의 시간 가중 평균 건수.",
    "q_final_max": "최종판독 대기열의 최대 건수.",
    "q_prelim_mean": "가판독 대기열의 시간 가중 평균 건수.",
    "q_prelim_max": "가판독 대기열의 최대 건수.",
}


def kpi_schema():
    return [{"key": k, "label": l, "unit": u, "group": g, "better": b, "decimals": d, "help": KPI_HELP.get(k, "")}
            for k, l, u, g, b, d in KPI_DEFS]


def _clean(x):
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x


def extract_kpis(a):
    t, r, q = a["timing"], a["resource"], a["queue"]
    k = {
        "total_mean": t["ALL"]["전체 소요(요청→최종보고)"]["mean"],
        "total_p95": t["ALL"]["전체 소요(요청→최종보고)"]["p95"],
        "scan_wait_ct": t[CT]["촬영 대기"]["mean"],
        "scan_wait_mri": t[MRI]["촬영 대기"]["mean"],
        "final_wait_mean": t["ALL"]["최종판독 대기"]["mean"],
        "final_wait_p95": t["ALL"]["최종판독 대기"]["p95"],
        "prelim_mean": t["ALL"]["응급 초기보고(가판독-촬영)"]["mean"],
        "prelim_p95": t["ALL"]["응급 초기보고(가판독-촬영)"]["p95"],
        "post_scan_mean": t["ALL"]["촬영 후 최종보고"]["mean"],
        "post_scan_p95": t["ALL"]["촬영 후 최종보고"]["p95"],
        "within24": 100 * t["ALL"]["경과 24h 이내 완료율"],
        "nextday": 100 * t["ALL"]["촬영일 익일까지 완료율"],
        "unfinished": t["ALL"]["미완료(촬영 완료 기준)"],
        "requests": t["ALL"]["요청 접수"],
        "total_mean_ct_urgent": t["CT-URGENT"]["전체 소요(요청→최종보고)"]["mean"],
        "total_mean_ct_routine": t["CT-ROUTINE"]["전체 소요(요청→최종보고)"]["mean"],
        "total_mean_mri_urgent": t["MRI-URGENT"]["전체 소요(요청→최종보고)"]["mean"],
        "total_mean_mri_routine": t["MRI-ROUTINE"]["전체 소요(요청→최종보고)"]["mean"],
        "util_ct": 100 * r["CT 촬영 슬롯 점유율"],
        "util_mri": 100 * r["MRI 촬영 슬롯 점유율"],
        "util_safety": 100 * r["MRI 안전확인 점유율"],
        "overtime": sum(r["초과근무 합계(분)"].values()),
    }
    for key, val in r.items():
        if key.startswith("FinalReader_"):
            k["util_final_" + key.split("_")[1].split(" ")[0]] = 100 * val
    for name, pre in (("CT_QUEUE", "q_ct"), ("MRI_QUEUE", "q_mri"), ("FINAL_WORKLIST", "q_final"),
                      ("PRELIM_WORKLIST", "q_prelim")):
        k[pre + "_mean"] = q.get(name, {}).get("시간평균", 0.0)
        k[pre + "_max"] = q.get(name, {}).get("최대", 0)
    return {key: _clean(float(v)) for key, v in k.items()}


def queue_series(events, cfg, bin_min=60):
    """집계 구간의 대기열 길이를 1시간 단위 시간가중 평균으로 낸다.
    timeline: 구간 전체 / profile24: 시계 시각(0~23시)별 일평균."""
    lo, hi = cfg.evaluation.warmup_end, cfg.evaluation.measure_end
    nb = int((hi - lo) // bin_min)
    step = next(st for st in (1, 2, 3, 4, 6, 8, 12, 24) if nb / st <= 800 or st == 24)   # 타임라인 한 점 = step 시간 평균
    pts = {q: [] for q in QUEUES}
    for e in events:
        if e["event_type"] == "QUEUE_CHANGED" and e["queue"] in pts:
            pts[e["queue"]].append((e["timestamp"], e["length"]))
    timeline, profile = {}, {}
    for q in QUEUES:
        series = sorted(pts[q], key=lambda p: p[0])
        area = [0.0] * nb
        cur, prev_t = 0, float(lo)
        i = 0
        while i < len(series) and series[i][0] <= lo:
            cur = series[i][1]
            i += 1
        bounds = [(t, ln) for t, ln in series[i:] if t < hi] + [(float(hi), None)]
        for t, ln in bounds:
            a, b = prev_t, t
            if cur and b > a:
                j = int((a - lo) // bin_min)
                while a < b and j < nb:
                    end = min(b, lo + (j + 1) * bin_min)
                    area[j] += cur * (end - a)
                    a, j = end, j + 1
            prev_t = t
            if ln is not None:
                cur = ln
        tl = [x / bin_min for x in area]
        timeline[q] = [round(sum(tl[i:i + step]) / len(tl[i:i + step]), 3) for i in range(0, nb, step)]
        sums, cnt = [0.0] * 24, [0] * 24
        for j, x in enumerate(tl):
            h = int(((lo + j * bin_min + cfg.clock.clock_at_t0) // 60) % 24)
            sums[h] += x
            cnt[h] += 1
        profile[q] = [round(s / c, 3) if c else 0.0 for s, c in zip(sums, cnt)]
    first_clock_hour = int(((lo + cfg.clock.clock_at_t0) // 60) % 24)
    return {"timeline": timeline, "profile24": profile, "first_clock_hour": first_clock_hour,
            "timeline_step_h": step}


def run_one(values, seed, verify=True):
    cfg = apply_params(values)
    cfg.evaluation.seed = int(seed)
    t0 = time.time()
    events = run(cfg)
    a = analyze(events, cfg)
    out = {"seed": cfg.evaluation.seed, "kpi": extract_kpis(a), "series": queue_series(events, cfg),
           "n_events": len(events)}
    if verify:
        checks = check_model(events, cfg)
        out["checks_ok"] = all(ok for _, ok, _ in checks)
        out["failed_checks"] = [n for n, ok, _ in checks if not ok]
    out["seconds"] = round(time.time() - t0, 2)
    return out
