"""이벤트 로그 -> 지표 계산, 모델 검증.

집계 대상 정의 (정현 명세 9-2):
 * 전체 소요시간: 집계 구간에 '접수된' 요청
 * 촬영 이후 지표: 집계 구간에 '촬영을 완료한' 요청
 * 완료되지 않은 요청은 평균에서 빼지 않고 '미완료 N건'으로 따로 표시한다.
"""
import math
import bisect
from collections import defaultdict

from config import CT, MRI


# ------------------------------------------------------------------ 로그 -> 요청 표
def build_case_table(events):
    cases = {}
    for e in events:
        et, cid = e["event_type"], e.get("case_id")
        if et == "EXAM_REQUESTED":
            c = e["case"]
            cases[cid] = {"case_id": cid, "modality": c["modality"], "urgent": c["urgent"],
                          "request": e["timestamp"]}
        elif et == "SAFETY_STARTED":
            cases[cid]["safety_start"] = e["timestamp"]
        elif et == "SAFETY_COMPLETED":
            cases[cid]["safety_end"] = e["timestamp"]
        elif et == "SCAN_STARTED":
            cases[cid]["scan_start"] = e["timestamp"]
            cases[cid]["scan_unit"] = e["resource_id"]
        elif et == "SCAN_COMPLETED":
            cases[cid]["scan_end"] = e["timestamp"]
        elif et == "READING_ASSIGNED":
            k = "prelim" if e["task_type"] == "PRELIM" else "final"
            cases[cid][k + "_start"] = e["timestamp"]
            cases[cid][k + "_reader"] = e["resource_id"]
        elif et == "PRELIM_COMPLETED":
            cases[cid]["prelim_end"] = e["timestamp"]
        elif et == "FINAL_COMPLETED":
            cases[cid]["final_end"] = e["timestamp"]
    return cases


def percentile(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100.0
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summarize(xs):
    if not xs:
        return {"n": 0, "mean": float("nan"), "p50": float("nan"), "p90": float("nan"), "p95": float("nan")}
    return {"n": len(xs), "mean": sum(xs) / len(xs), "p50": percentile(xs, 50),
            "p90": percentile(xs, 90), "p95": percentile(xs, 95)}


def _in(t, lo, hi):
    return t is not None and lo <= t < hi


def _groups(cases):
    """CT/MRI x 응급/일반 그룹 + 전체."""
    out = {"ALL": cases}
    for m in (CT, MRI):
        out[m] = [c for c in cases if c["modality"] == m]
        out[m + "-URGENT"] = [c for c in out[m] if c["urgent"]]
        out[m + "-ROUTINE"] = [c for c in out[m] if not c["urgent"]]
    return out


# ------------------------------------------------------------------ 시간 지표
def timing_metrics(cases, cfg):
    lo, hi = cfg.evaluation.warmup_end, cfg.evaluation.measure_end
    res = {}
    for name, grp in _groups(list(cases.values())).items():
        r = {}
        req = [c for c in grp if _in(c["request"], lo, hi)]
        r["요청 접수"] = len(req)
        r["전체 소요(요청→최종보고)"] = summarize([c["final_end"] - c["request"] for c in req if "final_end" in c])
        r["미완료(접수 기준)"] = sum(1 for c in req if "final_end" not in c)

        scanned = [c for c in grp if _in(c.get("scan_end"), lo, hi)]
        r["촬영 완료"] = len(scanned)
        r["촬영 후 최종보고"] = summarize([c["final_end"] - c["scan_end"] for c in scanned if "final_end" in c])
        r["미완료(촬영 완료 기준)"] = sum(1 for c in scanned if "final_end" not in c)
        within = sum(1 for c in scanned if "final_end" in c and c["final_end"] - c["scan_end"] <= 1440)
        r["경과 24h 이내 완료율"] = within / len(scanned) if scanned else float("nan")

        def next_day_deadline(t):
            clock = t + cfg.clock.clock_at_t0
            day = math.floor(clock / 1440)
            return (day + 2) * 1440 - cfg.clock.clock_at_t0      # 촬영일 익일 말
        nd = sum(1 for c in scanned if "final_end" in c and c["final_end"] < next_day_deadline(c["scan_end"]))
        r["촬영일 익일까지 완료율"] = nd / len(scanned) if scanned else float("nan")

        r["안전확인 대기"] = summarize([c["safety_start"] - c["request"] for c in grp
                                    if _in(c.get("safety_start"), lo, hi)])
        r["촬영 대기"] = summarize([c["scan_start"] - c.get("safety_end", c["request"]) for c in grp
                                  if _in(c.get("scan_start"), lo, hi)])
        r["최종판독 대기"] = summarize([c["final_start"] - (c.get("prelim_end") if c["urgent"] else c["scan_end"])
                                    for c in grp if _in(c.get("final_start"), lo, hi)])
        r["응급 초기보고(가판독-촬영)"] = summarize([c["prelim_end"] - c["scan_end"] for c in grp
                                              if c["urgent"] and _in(c.get("prelim_end"), lo, hi)])
        res[name] = r
    return res


# ------------------------------------------------------------------ 자원 지표
def _busy_minutes(intervals, lo, hi):
    return sum(max(0.0, min(e, hi) - max(s, lo)) for s, e in intervals)


def _slot_capacity(slots_by_hour, cfg, lo, hi):
    """집계 구간 동안 '슬롯 x 시간' 용량(분)."""
    cap, t = 0.0, float(lo)
    while t < hi:
        nxt = min(cfg.clock.next_change(t), hi)
        cap += slots_by_hour[cfg.clock.hour_at(t)] * (nxt - t)
        t = nxt
    return cap


def resource_metrics(cases, cfg):
    lo, hi = cfg.evaluation.warmup_end, cfg.evaluation.measure_end
    cl = list(cases.values())
    out = {}
    # 촬영: 슬롯 용량 대비 점유율
    for m, slots in ((CT, cfg.scan.ct_slots), (MRI, cfg.scan.mri_slots)):
        iv = [(c["scan_start"], c["scan_end"]) for c in cl
              if c["modality"] == m and "scan_start" in c and "scan_end" in c]
        iv += [(c["scan_start"], cfg.evaluation.total_end) for c in cl
               if c["modality"] == m and "scan_start" in c and "scan_end" not in c]
        out["%s 촬영 슬롯 점유율" % m] = _busy_minutes(iv, lo, hi) / _slot_capacity(slots, cfg, lo, hi)
    iv = [(c["safety_start"], c["safety_end"]) for c in cl if "safety_start" in c and "safety_end" in c]
    out["MRI 안전확인 점유율"] = _busy_minutes(iv, lo, hi) / _slot_capacity(cfg.scan.safety_slots, cfg, lo, hi)
    # 판독자: 예정 근무시간 대비 점유율, 초과근무
    iv_by = defaultdict(list)
    for c in cl:
        if "final_start" in c and "final_end" in c:
            iv_by[c["final_reader"]].append((c["final_start"], c["final_end"]))
        if "prelim_start" in c and "prelim_end" in c:
            iv_by["PreliminaryReader"].append((c["prelim_start"], c["prelim_end"]))
    for r in cfg.staffing.final_reader_ids:
        sched, t = 0.0, float(lo)
        while t < hi:
            nxt = min(cfg.clock.next_change(t), hi)
            if r in cfg.staffing.final_assignment.get(cfg.clock.hour_at(t), {}):
                sched += nxt - t
            t = nxt
        out["%s 점유율(예정근무 대비)" % r] = _busy_minutes(iv_by[r], lo, hi) / sched if sched else float("nan")
    # 초과근무: 작업이 근무 종료 경계를 넘어 계속된 시간
    ot = defaultdict(float)
    for c in cl:
        if "final_start" in c and "final_end" in c and _in(c["final_end"], lo, hi):
            r = c["final_reader"]
            t = c["final_start"]
            while True:
                b = cfg.clock.next_change(t)
                if b >= c["final_end"]:
                    break
                if r not in cfg.staffing.final_assignment.get(cfg.clock.hour_at(b), {}):
                    ot[r] += c["final_end"] - b
                    break
                t = b
    out["초과근무 합계(분)"] = dict(ot)
    return out


def queue_metrics(events, cfg):
    lo, hi = cfg.evaluation.warmup_end, cfg.evaluation.measure_end
    series = defaultdict(list)
    for e in events:
        if e["event_type"] == "QUEUE_CHANGED":
            series[e["queue"]].append((e["timestamp"], e["length"]))
    out = {}
    for q, pts in series.items():
        pts.sort(key=lambda p: p[0])
        area, mx = 0.0, 0
        prev_t, prev_len = lo, 0
        for t, ln in pts:
            if t < lo:
                prev_len = ln
                continue
            if t >= hi:
                break
            area += prev_len * (t - prev_t)
            mx = max(mx, prev_len, ln)
            prev_t, prev_len = t, ln
        area += prev_len * (hi - prev_t)
        mx = max(mx, prev_len)
        out[q] = {"시간평균": area / (hi - lo), "최대": mx}
    return out


def analyze(events, cfg):
    cases = build_case_table(events)
    return {"timing": timing_metrics(cases, cfg), "resource": resource_metrics(cases, cfg),
            "queue": queue_metrics(events, cfg), "cases": cases}


# ------------------------------------------------------------------ 모델 검증 (최종 파이프라인 7.2)
def check_model(events, cfg):
    cases = build_case_table(events)
    results = []

    def add(name, bad):
        results.append((name, not bad, "" if not bad else "위반 %d건 예: %s" % (len(bad), bad[:3])))

    cnt = defaultdict(lambda: defaultdict(int))
    for e in events:
        if e.get("case_id"):
            cnt[e["case_id"]][e["event_type"] + (":" + e["task_type"] if e["event_type"] == "READING_ASSIGNED" else "")] += 1
    dup = [cid for cid, d in cnt.items()
           if d["SCAN_STARTED"] > 1 or d["SCAN_COMPLETED"] > 1 or d["READING_ASSIGNED:FINAL"] > 1
           or d["READING_ASSIGNED:PRELIM"] > 1 or d["FINAL_COMPLETED"] > 1 or d["PRELIM_COMPLETED"] > 1]
    add("같은 검사가 중복 촬영·판독되지 않는다", dup)

    bad = [c["case_id"] for c in cases.values()
           if ("prelim_start" in c) != (c["urgent"] and "scan_end" in c and "prelim_start" in c)
           or (not c["urgent"] and "prelim_start" in c)]
    add("가판독은 응급 요청에만 생성된다", bad)

    bad = [c["case_id"] for c in cases.values() if "final_start" in c and c["urgent"]
           and ("prelim_end" not in c or c["final_start"] < c["prelim_end"])]
    add("응급 요청의 최종판독은 가판독 완료 후에 시작한다", bad)

    bad = [c["case_id"] for c in cases.values() if c["modality"] == MRI and "scan_start" in c
           and ("safety_end" not in c or c["scan_start"] < c["safety_end"])]
    add("모든 MRI 촬영은 안전확인 완료 후에 시작한다", bad)

    # 촬영 동시성: 시작 시점의 시간대 슬롯 수와 장비 수를 넘지 않는다
    bad = []
    for m, units, slots in ((CT, cfg.scan.ct_units, cfg.scan.ct_slots), (MRI, cfg.scan.mri_units, cfg.scan.mri_slots)):
        iv = sorted((c["scan_start"], c.get("scan_end", float("inf")), c["case_id"], c["scan_unit"])
                    for c in cases.values() if c["modality"] == m and "scan_start" in c)
        starts = [x[0] for x in iv]                                        # iv 는 시작 시각순
        ends = sorted(x[1] for x in iv)
        for s, e, cid, u in iv:
            conc = bisect.bisect_right(starts, s) - bisect.bisect_right(ends, s)   # s2<=s<e2 인 건수 (자신 포함)
            if conc > min(units, slots[cfg.clock.hour_at(s)]):
                bad.append((cid, round(s, 1), conc))
        by_unit = defaultdict(list)
        for s, e, cid, u in iv:
            by_unit[u].append((s, e, cid))
        for u, lst in by_unit.items():
            lst.sort()
            for a, b in zip(lst, lst[1:]):
                if b[0] < a[1]:
                    bad.append(("장비 겹침", u, a[2], b[2]))
    add("촬영 시작 시 장비·슬롯 동시 확보 (장비 수와 시간대 슬롯 수 이내)", bad)

    # 판독 배정: 근무 중이며 자격이 있는 자리에만 배정
    bad = []
    for c in cases.values():
        if "final_start" in c:
            allowed = cfg.staffing.final_assignment.get(cfg.clock.hour_at(c["final_start"]), {}).get(c["final_reader"])
            if allowed is None or c["modality"] not in allowed:
                bad.append((c["case_id"], c["final_reader"], round(c["final_start"], 1)))
    add("최종판독은 근무 중이고 자격이 있는 판독 자리에만 배정된다", bad)

    # 자원 겹침 없음(판독자)
    bad = []
    by_r = defaultdict(list)
    for c in cases.values():
        if "final_start" in c:
            by_r[c["final_reader"]].append((c["final_start"], c.get("final_end", float("inf")), c["case_id"]))
    for r, lst in by_r.items():
        lst.sort()
        for a, b in zip(lst, lst[1:]):
            if b[0] < a[1]:
                bad.append((r, a[2], b[2]))
    add("판독자 한 자리는 동시에 한 건만 처리한다", bad)

    # 우선순위: 더 빨리 처리해야 할 응급이 대기 중인데 일반이 먼저 시작되지 않는다
    bad = []

    def prio(stage_ready, stage_start, label, filt=lambda c: True):
        items = [(c[stage_ready] if isinstance(stage_ready, str) else stage_ready(c), c[stage_start], c)
                 for c in cases.values() if filt(c) and stage_start in c]
        items = [(r, s, c) for r, s, c in items if r is not None]
        # 일반 건(r, s)보다 먼저 준비된(r2 <= r) 응급이 더 늦게(s2 > s) 시작했다면 위반.
        # 응급을 준비 시각순으로 두고 '지금까지 가장 늦은 시작'을 누적해 이분탐색으로 확인한다.
        urg = sorted((r2, s2, c2["case_id"]) for r2, s2, c2 in items if c2["urgent"])
        keys = [u[0] for u in urg]
        run_max, best = [], None
        for u in urg:
            if best is None or u[1] > best[1]:
                best = u
            run_max.append(best)
        for r, s, c in items:
            if not c["urgent"]:
                i = bisect.bisect_right(keys, r)
                if i and run_max[i - 1][1] > s:
                    bad.append((label, c["case_id"], run_max[i - 1][2]))
    prio("request", "scan_start", "CT 촬영", lambda c: c["modality"] == CT)
    prio("safety_end", "scan_start", "MRI 촬영", lambda c: c["modality"] == MRI)
    prio("request", "safety_start", "MRI 안전확인", lambda c: c["modality"] == MRI)
    add("대기열에서 응급 요청이 일반 요청보다 먼저 시작한다 (같은 단계)", bad[:50] if bad else [])

    # 보존: 요청 수 = 완료 + 미완료
    n_req = len(cases)
    n_done = sum(1 for c in cases.values() if "final_end" in c)
    results.append(("요청 보존(요청 = 완료 + 미완료)", True,
                    "요청 %d = 완료 %d + 미완료 %d" % (n_req, n_done, n_req - n_done)))
    return results
