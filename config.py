"""공통 Config. 모든 값은 '가상 병원' 가정값이며 실제 병원 조사 결과가 아니다.

기준: 박정현 - 명세 (CT 2대 / MRI 1대, 주야 2교대, t=0 = 첫날 08:00).
구조: 정소은 - 공통규칙 (Demand / Scan / Staffing / Reading / Evaluation 분리).
시간 단위는 모두 '분'.

시간대별 설정: 수요, 촬영 슬롯, 판독 근무표는 모두 {시각(0~23): 값} 으로 준다.
시각은 하루 중 시계 시각이다 (t=0 은 clock_at_t0 = 08:00).
"""
import copy
from dataclasses import dataclass, field

CT, MRI = "CT", "MRI"
HOURS = list(range(24))


def by_hours(day, night, day_start=8, day_end=20):
    """주간(day_start~day_end시)에는 day, 나머지는 night 인 24시간 표."""
    return {h: (day if day_start <= h < day_end else night) for h in HOURS}


@dataclass
class ClockConfig:
    clock_at_t0: int = 480      # t=0 이 하루 중 몇 분인지 (08:00 = 480)

    def hour_at(self, t):
        return int(((t + self.clock_at_t0) % 1440) // 60)

    def day_index(self, t):
        """시뮬레이션 달력의 몇 번째 날(0부터, 자정 기준)인지."""
        return int((t + self.clock_at_t0) // 1440)

    def next_change(self, t):
        """t 보다 뒤의 첫 정시(시각이 바뀌는 순간)."""
        d = 60 - ((t + self.clock_at_t0) % 60)
        return t + (d if d > 1e-9 else 60)


@dataclass
class DemandConfig:
    # 시간대별 포아송 도착률 (건/시간). CT·MRI 는 서로 독립.
    rate_per_hour: dict = field(default_factory=lambda: {
        CT: by_hours(7.0, 2.0),
        MRI: by_hours(1.2, 0.3),
    })
    urgent_ratio: dict = field(default_factory=lambda: {CT: 0.20, MRI: 0.10})
    # 일별 변동: 매일 하루 단위로 도착률에 -x ~ +x (건/시간) 를 균등분포로 더한다 (0 = 없음). 시드가 같으면 같은 날엔 같은 값.
    daily_var: dict = field(default_factory=lambda: {CT: 0.0, MRI: 0.0})
    # 요일별 배수 (월=0 ... 일=6) 와 시뮬레이션 첫날의 요일
    weekday_mult: list = field(default_factory=lambda: [1.0] * 7)
    start_weekday: int = 0


@dataclass
class ScanConfig:
    # 장비 수와 '신경영상에 할당된 동시 촬영 슬롯'을 분리한다. 실제 동시 촬영 = min(장비 수, 슬롯).
    ct_units: int = 2
    ct_slots: dict = field(default_factory=lambda: by_hours(2, 1))
    mri_units: int = 1
    mri_slots: dict = field(default_factory=lambda: by_hours(1, 1))
    safety_units: int = 1
    safety_slots: dict = field(default_factory=lambda: by_hours(1, 1))
    # 삼각분포 (최소, 최빈, 최대)
    ct_scan: tuple = (10, 15, 20)
    mri_safety: tuple = (3, 5, 7)
    mri_scan: tuple = (30, 40, 50)


def _default_assignment():
    day = {"FinalReader_1": {CT}, "FinalReader_2": {MRI}, "FinalReader_3": {CT}}
    night = {"FinalReader_1": {CT, MRI}}
    return {h: copy.deepcopy(day if 8 <= h < 20 else night) for h in HOURS}


@dataclass
class StaffingConfig:
    # 시간대별 고정 배정표: 시각 -> {슬롯: 처리 가능한 검사 종류}. 표에 없으면 비근무.
    final_assignment: dict = field(default_factory=_default_assignment)
    # 시간대별 가판독 자리: 시각 -> 근무 중인 가판독 자리 ID 집합 (응급 요청의 초기 소견 담당)
    prelim_assignment: dict = field(default_factory=lambda: {h: {"PreliminaryReader_1"} for h in HOURS})

    @property
    def final_reader_ids(self):
        ids = set()
        for table in self.final_assignment.values():
            ids |= set(table)
        return sorted(ids)

    @property
    def prelim_reader_ids(self):
        ids = set()
        for table in self.prelim_assignment.values():
            ids |= set(table)
        return sorted(ids)


@dataclass
class ReadingConfig:
    prelim: dict = field(default_factory=lambda: {CT: (5, 15, 25), MRI: (8, 18, 28)})
    final: dict = field(default_factory=lambda: {CT: (5, 15, 25), MRI: (8, 18, 28)})


@dataclass
class EvaluationConfig:
    seed: int = 20261122
    warmup_end: int = 7 * 1440          # 준비 구간 7일: 집계 제외
    measure_end: int = 14 * 1440        # 집계 구간 다음 7일
    total_end: int = 16 * 1440          # 후속 관찰 2일 (요청은 계속 생성)


@dataclass
class Config:
    clock: ClockConfig = field(default_factory=ClockConfig)
    demand: DemandConfig = field(default_factory=DemandConfig)
    scan: ScanConfig = field(default_factory=ScanConfig)
    staffing: StaffingConfig = field(default_factory=StaffingConfig)
    reading: ReadingConfig = field(default_factory=ReadingConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)

    def copy(self):
        return copy.deepcopy(self)
