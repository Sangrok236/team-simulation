"""원자모델 (Parallel DEVS, PyPDEVS).

현재 시각은 elapsed 에 의존하지 않고 메시지의 timestamp 로 얻는다.
(모든 결합이 zero-delay 이므로 메시지의 timestamp == 수신 시각이다.)
서비스 중인 작업의 남은 시간을 갱신해야 하는 모델은 없다:
 - 서비스 모델(ServiceUnit)은 작업 중 외부 입력을 받지 않는다 (매니저가 막는다).
 - 매니저/스케줄러는 타이머가 없고 ta = 0 또는 inf 뿐이다.
"""
import bisect
import heapq

from pypdevs.DEVS import AtomicDEVS

from config import CT, MRI
from events import evt, priority_key, build_cases

INF = float("inf")


class State(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __str__(self):
        return "State"


def _now(prev, events):
    return max([prev] + [e["timestamp"] for e in events])


# ---------------------------------------------------------------- 검사 요청 생성
class PatientArrivalGenerator(AtomicDEVS):
    """검사 요청을 도착 시각에 맞춰 내보낸다. (EXAM_REQUESTED)"""

    def __init__(self, cfg):
        AtomicDEVS.__init__(self, "PatientArrivalGenerator")
        self.cases = build_cases(cfg)
        self.exam_out = self.addOutPort("exam_out")
        self.state = State(idx=0, t=0.0)

    def timeAdvance(self):
        s = self.state
        if s.idx >= len(self.cases):
            return INF
        return self.cases[s.idx]["request_time"] - s.t

    def outputFnc(self):
        c = self.cases[self.state.idx]
        return {self.exam_out: [evt("EXAM_REQUESTED", c["request_time"], case=c,
                                    case_id=c["case_id"], modality=c["modality"])]}

    def intTransition(self):
        s = self.state
        s.t = self.cases[s.idx]["request_time"]
        s.idx += 1
        return s


class OrderRouter(AtomicDEVS):
    """검사 종류(CT/MRI)에 따라 요청을 분기한다. 지연 없음."""

    def __init__(self):
        AtomicDEVS.__init__(self, "OrderRouter")
        self.exam_in = self.addInPort("exam_in")
        self.ct_out = self.addOutPort("ct_out")
        self.mri_out = self.addOutPort("mri_out")
        self.state = State(pending=[])

    def extTransition(self, inputs):
        self.state.pending.extend(inputs[self.exam_in])
        return self.state

    def timeAdvance(self):
        return 0.0 if self.state.pending else INF

    def outputFnc(self):
        out = {}
        for e in self.state.pending:
            port = self.ct_out if e["modality"] == CT else self.mri_out
            out.setdefault(port, []).append(e)
        return out

    def intTransition(self):
        self.state.pending = []
        return self.state


# ---------------------------------------------------------------- 근무
class StaffScheduleManager(AtomicDEVS):
    """근무·슬롯 구성이 달라지는 정시마다 STAFF_CHANGED 를 내보낸다 (같은 구성이면 내보내지 않음)."""

    def __init__(self, cfg):
        AtomicDEVS.__init__(self, "StaffScheduleManager")
        self.cfg = cfg

        def signature(h):
            fa = cfg.staffing.final_assignment.get(h, {})
            return (
                tuple(sorted((r, tuple(sorted(m))) for r, m in fa.items())),
                tuple(sorted(cfg.staffing.prelim_assignment.get(h, ()))),
                cfg.scan.ct_slots[h], cfg.scan.mri_slots[h], cfg.scan.safety_slots[h],
            )

        self.changes = []
        t = 0.0
        prev = signature(cfg.clock.hour_at(0.0))
        while True:
            t = cfg.clock.next_change(t)
            if t > cfg.evaluation.total_end:
                break
            cur = signature(cfg.clock.hour_at(t))
            if cur != prev:
                self.changes.append(t)
                prev = cur
        self.staff_out = self.addOutPort("staff_out")
        self.state = State(idx=0, t=0.0)

    def timeAdvance(self):
        s = self.state
        if s.idx >= len(self.changes):
            return INF
        return self.changes[s.idx] - s.t

    def outputFnc(self):
        t = self.changes[self.state.idx]
        return {self.staff_out: [evt("STAFF_CHANGED", t, hour=self.cfg.clock.hour_at(t))]}

    def intTransition(self):
        s = self.state
        s.t = self.changes[s.idx]
        s.idx += 1
        return s


# ---------------------------------------------------------------- 서비스 단위
class ServiceUnit(AtomicDEVS):
    """한 번에 한 건을 처리하는 자리 (CT/MRI 스캐너, 안전확인, 판독자 공용).
    start 이벤트의 service 만큼 점유한 뒤 완료 이벤트를 낸다. 중단 없음."""

    def __init__(self, name, done_event):
        AtomicDEVS.__init__(self, name)
        self.done_event = done_event
        self.start_in = self.addInPort("start")
        self.done_out = self.addOutPort("done")
        self.state = State(case=None, now=0.0, sigma=INF, task=None)

    def extTransition(self, inputs):
        s = self.state
        if s.case is not None:
            raise RuntimeError("%s: 점유 중인데 새 작업이 배정됨" % self.name)
        ev = inputs[self.start_in][0]
        s.case = ev["case"]
        s.task = ev.get("task")
        s.now = ev["timestamp"]
        s.sigma = ev["service"]
        return s

    def timeAdvance(self):
        return self.state.sigma if self.state.case is not None else INF

    def outputFnc(self):
        s = self.state
        c = s.case
        return {self.done_out: [evt(self.done_event, s.now + s.sigma, case=c,
                                    case_id=c["case_id"], resource_id=self.name,
                                    modality=c["modality"], task=s.task)]}

    def intTransition(self):
        s = self.state
        s.now += s.sigma
        s.case = None
        s.task = None
        s.sigma = INF
        return s


class ResourceManager(AtomicDEVS):
    """대기열 + 자원 관리 (정소은 구조의 Queue + ResourceManager).
    시작 조건: 비어 있는 장비가 있고, 현재 시간대의 동시 촬영 슬롯에 여유가 있을 것.
    진행 중인 촬영은 교대 시점에도 중단하지 않는다."""

    def __init__(self, name, cfg, unit_ids, slots, started_event, queue_name, service_field):
        AtomicDEVS.__init__(self, name)
        self.cfg = cfg
        self.slots = slots
        self.started_event = started_event
        self.queue_name = queue_name
        self.service_field = service_field
        self.req = self.addInPort("req")
        self.done = self.addInPort("done")
        self.staff_in = self.addInPort("staff_in")
        self.start_ports = {u: self.addOutPort("start_" + u) for u in unit_ids}
        self.metrics_out = self.addOutPort("metrics_out")
        self.state = State(now=0.0, hour=cfg.clock.hour_at(0.0), queue=[],
                           busy={u: False for u in unit_ids},
                           out_start=[], out_metrics=[], last_len=0)

    def extTransition(self, inputs):
        s = self.state
        done = inputs.get(self.done, [])
        staff = inputs.get(self.staff_in, [])
        reqs = inputs.get(self.req, [])
        s.now = _now(s.now, done + staff + reqs)
        # 동시 이벤트 순서: 완료/자원 반환 -> 근무 상태 갱신 -> 신규 요청 -> 신규 배정
        for e in done:
            s.busy[e["resource_id"]] = False
        for e in staff:
            s.hour = e["hour"]
        for e in reqs:
            heapq.heappush(s.queue, (priority_key(e["case"], e["timestamp"]), e["case"]))
        allowed = self.slots[s.hour]
        while s.queue:
            if sum(s.busy.values()) >= allowed:
                break
            idle = [u for u, b in s.busy.items() if not b]
            if not idle:
                break
            _, case = heapq.heappop(s.queue)
            u = idle[0]
            s.busy[u] = True
            s.out_start.append((u, evt("START", s.now, case=case, service=case[self.service_field])))
            s.out_metrics.append(evt(self.started_event, s.now, case_id=case["case_id"],
                                     resource_id=u, modality=case["modality"],
                                     urgent=case["urgent"]))
        if len(s.queue) != s.last_len or reqs:
            s.out_metrics.append(evt("QUEUE_CHANGED", s.now, queue=self.queue_name, length=len(s.queue)))
            s.last_len = len(s.queue)
        return s

    def timeAdvance(self):
        s = self.state
        return 0.0 if (s.out_start or s.out_metrics) else INF

    def outputFnc(self):
        s = self.state
        out = {}
        for u, msg in s.out_start:
            out.setdefault(self.start_ports[u], []).append(msg)
        if s.out_metrics:
            out[self.metrics_out] = list(s.out_metrics)
        return out

    def intTransition(self):
        s = self.state
        s.out_start = []
        s.out_metrics = []
        return s


# ---------------------------------------------------------------- 판독
class ReadingScheduler(AtomicDEVS):
    """판독 업무 목록(Worklist) + 배정(Scheduler).
    * 촬영 완료: 일반 -> 최종판독 업무 / 응급 -> 가판독 업무
    * 가판독 완료 시 최종판독 업무를 정확히 한 번 생성
    * 최종판독은 시간대별 고정 배정표에 따라, 근무 중이고 자격이 있는 빈 자리에만 배정
    * 근무 종료 후에는 신규 배정만 막고 진행 중인 판독은 끝까지 수행
    (Worklist 와 Scheduler 를 한 모델로 합친 것. 분리가 필요하면 이후에 쪼갠다.)"""

    def __init__(self, cfg):
        AtomicDEVS.__init__(self, "ReadingScheduler")
        self.cfg = cfg
        self.prelim_ids = cfg.staffing.prelim_reader_ids
        self.final_ids = cfg.staffing.final_reader_ids
        self.scan_done = self.addInPort("scan_done")
        self.prelim_done = self.addInPort("prelim_done")
        self.final_done = self.addInPort("final_done")
        self.staff_in = self.addInPort("staff_in")
        self.assign_ports = {}
        for r in self.prelim_ids:
            self.assign_ports[r] = self.addOutPort("assign_" + r)
        for r in self.final_ids:
            self.assign_ports[r] = self.addOutPort("assign_" + r)
        self.metrics_out = self.addOutPort("metrics_out")
        self.state = State(now=0.0, hour=cfg.clock.hour_at(0.0),
                           prelim_q=[], final_q=[],
                           prelim_busy={r: False for r in self.prelim_ids}, final_busy={r: False for r in self.final_ids},
                           out_assign=[], out_metrics=[], last_p=0, last_f=0, last_fm=(0, 0))

    def _allowed(self, reader, hour):
        return self.cfg.staffing.final_assignment.get(hour, {}).get(reader, None)

    def extTransition(self, inputs):
        s = self.state
        pdone = inputs.get(self.prelim_done, [])
        fdone = inputs.get(self.final_done, [])
        staff = inputs.get(self.staff_in, [])
        scans = inputs.get(self.scan_done, [])
        s.now = _now(s.now, pdone + fdone + staff + scans)
        # 1) 완료/자원 반환
        for e in pdone:
            s.prelim_busy[e["resource_id"]] = False
            c = e["case"]
            bisect.insort(s.final_q, (priority_key(c, e["timestamp"]), c))
        for e in fdone:
            s.final_busy[e["resource_id"]] = False
        # 2) 근무 상태 갱신
        for e in staff:
            s.hour = e["hour"]
        # 3) 신규 업무
        for e in scans:
            c = e["case"]
            q = s.prelim_q if c["has_prelim"] else s.final_q
            bisect.insort(q, (priority_key(c, e["timestamp"]), c))
        # 4) 배정
        on_duty = self.cfg.staffing.prelim_assignment.get(s.hour, ())
        for r in self.prelim_ids:
            if not s.prelim_q:
                break
            if s.prelim_busy[r] or r not in on_duty:
                continue
            _, c = s.prelim_q.pop(0)
            s.prelim_busy[r] = True
            self._assign(r, "PRELIM", c, c["prelim_time"])
        for r in self.final_ids:
            allowed = self._allowed(r, s.hour)
            if allowed is None or s.final_busy[r]:
                continue
            for i, (_, c) in enumerate(s.final_q):
                if c["modality"] in allowed:
                    s.final_q.pop(i)
                    s.final_busy[r] = True
                    self._assign(r, "FINAL", c, c["final_time"])
                    break
        # 대기열 길이 기록
        fm = (sum(1 for _, c in s.final_q if c["modality"] == CT),
              sum(1 for _, c in s.final_q if c["modality"] == MRI))
        if len(s.prelim_q) != s.last_p or scans or pdone:
            s.out_metrics.append(evt("QUEUE_CHANGED", s.now, queue="PRELIM_WORKLIST", length=len(s.prelim_q)))
            s.last_p = len(s.prelim_q)
        if len(s.final_q) != s.last_f or fm != s.last_fm or scans or pdone:
            s.out_metrics.append(evt("QUEUE_CHANGED", s.now, queue="FINAL_WORKLIST",
                                     length=len(s.final_q), by_modality={CT: fm[0], MRI: fm[1]}))
            s.last_f = len(s.final_q)
            s.last_fm = fm
        return s

    def _assign(self, reader, task_type, case, service):
        s = self.state
        s.out_assign.append((reader, evt("START", s.now, case=case, service=service, task=task_type)))
        s.out_metrics.append(evt("READING_ASSIGNED", s.now, case_id=case["case_id"], resource_id=reader,
                                 modality=case["modality"], task_type=task_type, urgent=case["urgent"]))

    def timeAdvance(self):
        s = self.state
        return 0.0 if (s.out_assign or s.out_metrics) else INF

    def outputFnc(self):
        s = self.state
        out = {}
        for r, msg in s.out_assign:
            out.setdefault(self.assign_ports[r], []).append(msg)
        if s.out_metrics:
            out[self.metrics_out] = list(s.out_metrics)
        return out

    def intTransition(self):
        s = self.state
        s.out_assign = []
        s.out_metrics = []
        return s


# ---------------------------------------------------------------- 수집
class MetricsCollector(AtomicDEVS):
    """모든 이벤트를 시각, 검사 ID, 자원 ID 와 함께 기록한다. (분석은 analysis.py)"""

    def __init__(self):
        AtomicDEVS.__init__(self, "MetricsCollector")
        self.events_in = self.addInPort("events_in")
        self.state = State(events=[])

    def extTransition(self, inputs):
        for e in inputs[self.events_in]:
            rec = dict(e)
            if rec["event_type"] != "EXAM_REQUESTED":
                rec.pop("case", None)       # 요청 시에만 전체 속성을 보관
            self.state.events.append(rec)
        return self.state

    def timeAdvance(self):
        return INF
