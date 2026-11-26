"""NeuroImagingSystem (Coupled DEVS) 조립.

NeuroImagingSystem
 |- PatientFlow        : PatientArrivalGenerator, OrderRouter
 |- CTSubsystem        : CTResourceManager(=Queue+Resource), CTScanner x N
 |- MRISubsystem       : MRISafetyManager + SafetyChecker, MRIResourceManager + MRIScanner
 |- ReadingSubsystem   : ReadingScheduler(=Worklist+Scheduler), PreliminaryReader, FinalReader x K
 |- StaffScheduleManager
 |- MetricsCollector
"""
from pypdevs.DEVS import CoupledDEVS

from config import Config
from models import (PatientArrivalGenerator, OrderRouter, StaffScheduleManager, ServiceUnit,
                    ResourceManager, ReadingScheduler, MetricsCollector)


class PatientFlow(CoupledDEVS):
    def __init__(self, cfg):
        CoupledDEVS.__init__(self, "PatientFlow")
        gen = self.addSubModel(PatientArrivalGenerator(cfg))
        router = self.addSubModel(OrderRouter())
        self.ct_out = self.addOutPort("ct_out")
        self.mri_out = self.addOutPort("mri_out")
        self.metrics_out = self.addOutPort("metrics_out")
        self.connectPorts(gen.exam_out, router.exam_in)
        self.connectPorts(gen.exam_out, self.metrics_out)
        self.connectPorts(router.ct_out, self.ct_out)
        self.connectPorts(router.mri_out, self.mri_out)


class CTSubsystem(CoupledDEVS):
    def __init__(self, cfg):
        CoupledDEVS.__init__(self, "CTSubsystem")
        ids = ["CTScanner_%d" % (i + 1) for i in range(cfg.scan.ct_units)]
        mgr = self.addSubModel(ResourceManager("CTResourceManager", cfg, ids, cfg.scan.ct_slots,
                                               "SCAN_STARTED", "CT_QUEUE", "scan_time"))
        self.exam_in = self.addInPort("exam_in")
        self.staff_in = self.addInPort("staff_in")
        self.done_out = self.addOutPort("done_out")
        self.metrics_out = self.addOutPort("metrics_out")
        self.connectPorts(self.exam_in, mgr.req)
        self.connectPorts(self.staff_in, mgr.staff_in)
        self.connectPorts(mgr.metrics_out, self.metrics_out)
        for u in ids:
            unit = self.addSubModel(ServiceUnit(u, "SCAN_COMPLETED"))
            self.connectPorts(mgr.start_ports[u], unit.start_in)
            self.connectPorts(unit.done_out, mgr.done)
            self.connectPorts(unit.done_out, self.done_out)


class MRISubsystem(CoupledDEVS):
    def __init__(self, cfg):
        CoupledDEVS.__init__(self, "MRISubsystem")
        s_ids = ["SafetyChecker_%d" % (i + 1) for i in range(cfg.scan.safety_units)]
        m_ids = ["MRIScanner_%d" % (i + 1) for i in range(cfg.scan.mri_units)]
        safety = self.addSubModel(ResourceManager("MRISafetyManager", cfg, s_ids, cfg.scan.safety_slots,
                                                  "SAFETY_STARTED", "MRI_SAFETY_QUEUE", "safety_time"))
        mri = self.addSubModel(ResourceManager("MRIResourceManager", cfg, m_ids, cfg.scan.mri_slots,
                                               "SCAN_STARTED", "MRI_QUEUE", "scan_time"))
        self.exam_in = self.addInPort("exam_in")
        self.staff_in = self.addInPort("staff_in")
        self.done_out = self.addOutPort("done_out")
        self.metrics_out = self.addOutPort("metrics_out")
        self.connectPorts(self.exam_in, safety.req)
        self.connectPorts(self.staff_in, safety.staff_in)
        self.connectPorts(self.staff_in, mri.staff_in)
        self.connectPorts(safety.metrics_out, self.metrics_out)
        self.connectPorts(mri.metrics_out, self.metrics_out)
        for u in s_ids:
            unit = self.addSubModel(ServiceUnit(u, "SAFETY_COMPLETED"))
            self.connectPorts(safety.start_ports[u], unit.start_in)
            self.connectPorts(unit.done_out, safety.done)
            self.connectPorts(unit.done_out, mri.req)          # 안전확인 통과 -> MRI 대기열
            self.connectPorts(unit.done_out, self.metrics_out)
        for u in m_ids:
            unit = self.addSubModel(ServiceUnit(u, "SCAN_COMPLETED"))
            self.connectPorts(mri.start_ports[u], unit.start_in)
            self.connectPorts(unit.done_out, mri.done)
            self.connectPorts(unit.done_out, self.done_out)


class ReadingSubsystem(CoupledDEVS):
    def __init__(self, cfg):
        CoupledDEVS.__init__(self, "ReadingSubsystem")
        sched = self.addSubModel(ReadingScheduler(cfg))
        self.scan_done_in = self.addInPort("scan_done_in")
        self.staff_in = self.addInPort("staff_in")
        self.metrics_out = self.addOutPort("metrics_out")
        self.connectPorts(self.scan_done_in, sched.scan_done)
        self.connectPorts(self.staff_in, sched.staff_in)
        self.connectPorts(sched.metrics_out, self.metrics_out)
        for r in cfg.staffing.prelim_reader_ids:
            prelim = self.addSubModel(ServiceUnit(r, "PRELIM_COMPLETED"))
            self.connectPorts(sched.assign_ports[r], prelim.start_in)
            self.connectPorts(prelim.done_out, sched.prelim_done)
            self.connectPorts(prelim.done_out, self.metrics_out)
        for r in cfg.staffing.final_reader_ids:
            reader = self.addSubModel(ServiceUnit(r, "FINAL_COMPLETED"))
            self.connectPorts(sched.assign_ports[r], reader.start_in)
            self.connectPorts(reader.done_out, sched.final_done)
            self.connectPorts(reader.done_out, self.metrics_out)


class NeuroImagingSystem(CoupledDEVS):
    def __init__(self, cfg=None):
        CoupledDEVS.__init__(self, "NeuroImagingSystem")
        cfg = cfg or Config()
        self.cfg = cfg
        flow = self.addSubModel(PatientFlow(cfg))
        ct = self.addSubModel(CTSubsystem(cfg))
        mri = self.addSubModel(MRISubsystem(cfg))
        reading = self.addSubModel(ReadingSubsystem(cfg))
        staff = self.addSubModel(StaffScheduleManager(cfg))
        self.metrics = self.addSubModel(MetricsCollector())

        self.connectPorts(flow.ct_out, ct.exam_in)
        self.connectPorts(flow.mri_out, mri.exam_in)
        self.connectPorts(ct.done_out, reading.scan_done_in)
        self.connectPorts(mri.done_out, reading.scan_done_in)
        self.connectPorts(staff.staff_out, ct.staff_in)
        self.connectPorts(staff.staff_out, mri.staff_in)
        self.connectPorts(staff.staff_out, reading.staff_in)
        # 기록
        self.connectPorts(flow.metrics_out, self.metrics.events_in)
        self.connectPorts(ct.metrics_out, self.metrics.events_in)
        self.connectPorts(mri.metrics_out, self.metrics.events_in)
        self.connectPorts(reading.metrics_out, self.metrics.events_in)
        self.connectPorts(ct.done_out, self.metrics.events_in)
        self.connectPorts(mri.done_out, self.metrics.events_in)
        self.connectPorts(staff.staff_out, self.metrics.events_in)


def run(cfg=None):
    """시뮬레이션을 실행하고 (이벤트 로그, 최종 상태)를 돌려준다."""
    from pypdevs.simulator import Simulator
    cfg = cfg or Config()
    model = NeuroImagingSystem(cfg)
    sim = Simulator(model)
    sim.setTerminationTime(cfg.evaluation.total_end)
    sim.simulate()
    return model.metrics.state.events
